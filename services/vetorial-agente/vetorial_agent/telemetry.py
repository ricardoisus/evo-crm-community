"""Durable, closed-schema telemetry. No provider payloads or exception text cross this boundary.

Operator must migrate before use. Delivery is at-least-once; the CRM must dedup event_id.
This module does not grant permission to send messages or invoke models.
"""
import copy
import time
import uuid
import json
import httpx
from .security import signed
from sqlalchemy import Table, Column, String, Integer, BigInteger, JSON, select
from .store import meta

ledger = Table(
    "telemetry_ledger", meta,
    Column("event_id", String, primary_key=True),
    Column("payload", JSON, nullable=False),
    Column("subject_key", String, nullable=False, unique=True),
)
outbox = Table(
    "telemetry_outbox", meta,
    Column("event_id", String, primary_key=True),
    Column("attempts", Integer, nullable=False, default=0),
    Column("next_attempt_ms", BigInteger, nullable=False, default=0),
    Column("delivered_ms", BigInteger),
)


def identifier(value):
    # UUIDs avoid accidentally accepting a phone number or provider error as an ID.
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError("invalid_identifier")
    return value


def integer(value):
    if type(value) is not int or value < 0 or value > 2**63 - 1:
        raise ValueError("invalid_integer")
    return value


def choice(value, options):
    if value not in options:
        raise ValueError("invalid_enum")
    return value


def sanitize(event):
    """Reject unknown fields, including prompt, phone, raw errors and secret values."""
    common = {"event_id", "deployment_id", "kind", "occurred_ms"}
    fields = {
        "execution": {"execution_id", "conversation_id", "role", "source", "revision", "policy_epoch", "status", "latency_ms", "outcome"},
        "usage": {"call_id", "execution_id", "source", "provider", "model", "input_tokens", "cached_tokens", "output_tokens", "reasoning_tokens", "cost_micro_usd", "usage_state"},
        "heartbeat": {"instance_id", "revision", "policy_epoch", "sha256", "integrations"},
    }
    kind = event.get("kind")
    if kind not in fields or set(event) != common | fields[kind]:
        raise ValueError("invalid_schema")
    result = copy.deepcopy(event)
    for key in ("event_id", "deployment_id", "execution_id", "call_id", "instance_id", "conversation_id"):
        if key in result:
            identifier(result[key])
    for key in ("occurred_ms", "revision", "policy_epoch", "latency_ms", "input_tokens", "cached_tokens", "output_tokens", "reasoning_tokens"):
        if key in result:
            integer(result[key])
    if kind in ("execution", "usage"):
        choice(result["source"], ("live", "test"))
    if kind == "execution":
        choice(result["role"], ("atendimento", "analista"))
        choice(result["status"], ("succeeded", "failed", "discarded", "uncertain"))
        choice(result["outcome"], ("none", "send_confirmed", "booking_confirmed", "paused", "resumed"))
    elif kind == "usage":
        choice(result["provider"], ("openai", "groq"))
        choice(result["model"], ("gpt-6-luna", "gpt-6.1-sol", "whisper-large-v3-turbo"))
        choice(result["usage_state"], ("known", "unknown"))
        if result["usage_state"] == "unknown":
            if result["cost_micro_usd"] is not None:
                raise ValueError("unknown_cost")
        else:
            integer(result["cost_micro_usd"])
        if result["cached_tokens"] > result["input_tokens"] or result["reasoning_tokens"] > result["output_tokens"]:
            raise ValueError("invalid_usage")
    else:
        digest = result["sha256"]
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("invalid_digest")
        integrations = result["integrations"]
        if not isinstance(integrations, dict) or set(integrations) != {"openai", "groq", "calendly", "google_gateway"}:
            raise ValueError("invalid_integrations")
        for status in integrations.values():
            choice(status, ("configured", "absent", "error", "unverified"))
    return result


def health(heartbeat, *, now_ms, revision, sha256):
    if heartbeat is None:
        return "indisponivel"
    age = now_ms - heartbeat["occurred_ms"]
    if age < 0 or age > 90000:
        return "indisponivel"
    if heartbeat["revision"] != revision or heartbeat["sha256"] != sha256:
        return "aguardando_configuracao"
    return "atrasado" if age > 45000 else "saudavel"


class Telemetry:
    def __init__(self, store):
        self.store = store

    def record(self, event):
        self.record_batch([event])

    def record_batch(self, events):
        events = [sanitize(event) for event in events]
        with self.store.engine.begin() as connection:
            for event in events:
                self._record(connection, event)

    def _record(self, connection, event):
        subject = event.get("call_id", event.get("execution_id", event["event_id"]))
        subject_key = f'{event["deployment_id"]}:{event["kind"]}:{subject}'
        connection.execute(self.store.insert(ledger).values(event_id=event["event_id"], subject_key=subject_key, payload=event).on_conflict_do_nothing())
        saved = connection.execute(select(ledger.c.payload).where(ledger.c.event_id == event["event_id"])).scalar_one_or_none()
        if saved != event:
            raise ValueError("telemetry_collision")
        connection.execute(self.store.insert(outbox).values(event_id=event["event_id"], attempts=0, next_attempt_ms=0).on_conflict_do_nothing())

    def flush(self, deliver, *, now_ms=None, limit=100):
        """deliver receives a sanitized batch and must return acknowledged event UUIDs.

        Lost ACK retries the exact payload. Exceptions are never persisted or logged.
        Multiple workers may redeliver; recipient deduplication is required.
        """
        now_ms = int(time.time() * 1000) if now_ms is None else integer(now_ms)
        with self.store.engine.begin() as connection:
            rows = connection.execute(select(ledger.c.payload, outbox.c.attempts).join(outbox, ledger.c.event_id == outbox.c.event_id).where(outbox.c.delivered_ms.is_(None), outbox.c.next_attempt_ms <= now_ms).order_by(outbox.c.next_attempt_ms, ledger.c.event_id).limit(min(max(limit, 1), 100))).all()
            for payload, attempts in rows:
                connection.execute(outbox.update().where(outbox.c.event_id == payload["event_id"]).values(attempts=attempts + 1, next_attempt_ms=now_ms + min(300000, 1000 * 2 ** min(attempts, 8))))
        if not rows:
            return 0
        batch = [sanitize(payload) for payload, _ in rows]
        try:
            acknowledgments = set(deliver(copy.deepcopy(batch)))
        except Exception:
            return 0
        acknowledged = acknowledgments & {event["event_id"] for event in batch}
        with self.store.engine.begin() as connection:
            connection.execute(outbox.update().where(outbox.c.event_id.in_(acknowledged), outbox.c.delivered_ms.is_(None)).values(delivered_ms=now_ms))
        return len(acknowledged)


class HTTPDelivery:
    """Private HMAC transport; receiver contract is {acknowledged: [event_id]}.

    No response body/HTTP exception is exposed to logs or metrics. A failure returns
    no ACK, preserving the outbox. Bootstrap URL/secret are supplied by the caller.
    """
    path = "/internal/vetorial_agent/telemetry"

    def __init__(self, base_url, secret, client=None):
        if len(secret) < 32:
            raise ValueError("invalid_telemetry_secret")
        self.base_url = base_url.rstrip("/")
        self.secret = secret
        self.client = client or httpx.Client(timeout=10, follow_redirects=False)

    def __call__(self, events):
        body = json.dumps({"events": [sanitize(event) for event in events]}, ensure_ascii=False, separators=(",", ":")).encode()
        try:
            response = self.client.post(self.base_url + self.path, content=body, headers=signed(body, self.secret, path=self.path))
            if response.status_code != 200 or len(response.content) > 16384:
                return []
            data = response.json()
            if not isinstance(data, dict) or set(data) != {"acknowledged"} or not isinstance(data["acknowledged"], list) or len(data["acknowledged"]) > 100:
                return []
            return [identifier(value) for value in data["acknowledged"]]
        except Exception:
            return []
