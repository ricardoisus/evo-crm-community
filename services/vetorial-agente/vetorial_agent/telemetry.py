"""Durable, closed-schema telemetry. No provider payloads or exception text cross this boundary.

Operator must migrate before use. Delivery is at-least-once; the CRM must dedup event_id.
This module does not grant permission to send messages or invoke models.
"""
import hashlib
from contextvars import ContextVar
from functools import wraps
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
    optional = {"pricing_version"} if event.get("kind") == "usage" else set()
    kind = event.get("kind")
    if kind not in fields or set(event) - optional != common | fields[kind]:
        raise ValueError("invalid_schema")
    result = copy.deepcopy(event)
    if "pricing_version" in result:
        choice(result["pricing_version"], ("2026-11-13.v1",))
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
        if not isinstance(integrations, dict) or set(integrations) not in ({"openai", "groq", "calendly", "google_gateway"}, {"openai", "groq", "calendly", "google_gateway", "evolution"}):
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
            response = self.client.post(self.base_url + self.path, content=body, headers={**signed(body, self.secret, path=self.path), "Idempotency-Key": hashlib.sha256(body).hexdigest()})
            if response.status_code != 200 or len(response.content) > 16384:
                return []
            data = response.json()
            if not isinstance(data, dict) or set(data) != {"acknowledged"} or not isinstance(data["acknowledged"], list) or len(data["acknowledged"]) > 100:
                return []
            return [identifier(value) for value in data["acknowledged"]]
        except Exception:
            return []


# Each worker execution owns its provider calls, including media and tool rounds.
_execution = ContextVar("telemetry_execution", default=None)


def measured(provider):
    def decorate(method):
        @wraps(method)
        def call(self, *args, **kwargs):
            execution = _execution.get()
            if execution is None:
                return method(self, *args, **kwargs)
            call_id = str(uuid.uuid4())
            before = len(self.usage)
            try:
                return method(self, *args, **kwargs)
            finally:
                usage = self.usage[-1] if len(self.usage) > before else {}
                model = (kwargs.get("json", {}).get("model") if provider == "openai"
                         else "whisper-large-v3-turbo")
                execution.usage(call_id, provider, model, usage)
        return call
    return decorate


class Execution:
    def __init__(self, telemetry, snapshot, row, role, clock):
        self.telemetry, self.snapshot, self.clock = telemetry, snapshot, clock
        self.id = str(uuid.uuid4())
        self.started = clock()
        self.row, self.role = row, role
        try:
            self.conversation_id = identifier(str(row["id"]))
        except ValueError:
            self.conversation_id = str(uuid.uuid5(uuid.UUID(snapshot.payload["deployment_id"]), str(row["id"])))
        self.outcome = "none"
        self.token = _execution.set(self)

    def event(self, kind, **fields):
        return dict(event_id=str(uuid.uuid4()), deployment_id=self.snapshot.payload["deployment_id"],
                    kind=kind, occurred_ms=int(self.clock() * 1000), **fields)

    def usage(self, call_id, provider, model, usage):
        from .pricing import VERSION
        # A reference tariff is not sufficient evidence to settle a provider call.
        # Unpriced calls (including failed/ambiguous calls) remain unknown.
        def count(name, details=None):
            value = usage.get(name, 0) if details is None else (usage.get(details) or {}).get(name, 0)
            return value if type(value) is int and value >= 0 else 0
        self.telemetry.record(self.event("usage", call_id=call_id, execution_id=self.id,
            source="live", provider=provider, model=model,
            input_tokens=count("input_tokens"), cached_tokens=count("cached_tokens", "input_tokens_details"),
            output_tokens=count("output_tokens"), reasoning_tokens=count("reasoning_tokens", "output_tokens_details"),
            cost_micro_usd=None, usage_state="unknown", pricing_version=VERSION))

    def finish(self, status):
        _execution.reset(self.token)
        self.telemetry.record(self.event("execution", execution_id=self.id,
            conversation_id=self.conversation_id,
            role=self.role, source="live", revision=self.snapshot.payload["revision"],
            policy_epoch=self.snapshot.payload["policy_epoch"], status=status,
            latency_ms=max(0, int((self.clock() - self.started) * 1000)), outcome=self.outcome))


class TelemetryPump:
    """Separate lifecycle loop: heartbeat is not delayed by inference or typing."""
    def __init__(self, worker, deliver, integrations, clock=time.time):
        self.worker, self.deliver, self.integrations, self.clock = worker, deliver, integrations, clock
        self.instance_id = str(uuid.uuid4())
        self.next_heartbeat = 0

    def tick(self):
        now = self.clock()
        if now >= self.next_heartbeat:
            try:
                current = self.worker.configuration_cache.pin()  # expired/revoked lease cannot claim health
                snapshot = self.worker.snapshot  # ACK only what prepare actually applied
                if snapshot is not None and current.fence() == snapshot.fence():
                    snapshot = current  # same applied configuration, renewed lease
                if snapshot is not None and self.worker.configuration_cache.clock() >= snapshot.expires_at:
                    snapshot = None
            except Exception:
                snapshot = None
            if snapshot is not None:
                self.worker.telemetry.record(dict(event_id=str(uuid.uuid4()),
                    deployment_id=snapshot.payload["deployment_id"], kind="heartbeat", occurred_ms=int(now * 1000),
                    instance_id=self.instance_id, **snapshot.fence(), integrations=self.integrations() if callable(self.integrations) else self.integrations))
                self.next_heartbeat = now + 15
        return self.worker.telemetry.flush(self.deliver, now_ms=int(now * 1000))
