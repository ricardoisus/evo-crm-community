"""Internal HMAC ingress; no public control port, no startup schema mutations."""

import asyncio
import os
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from .security import verify
from .policy import Policy
from .store import Store
from .adapters import CRM, OpenAI
from .transcription import GroqTranscription
from .runtime import Worker
from .agenda import Agenda, CalendlyAvailability, GoogleGateway


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1, max_length=80)
    inbox_id: str = Field(min_length=1, max_length=80)
    conversation_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    message_id: str = Field(min_length=1, max_length=100)
    version: int = Field(ge=1)
    paused: bool
    kind: Literal["incoming", "human", "resume", "group", "status"]
    phone: str = Field(max_length=30)
    audio: bool = False
    occurred_at: float


def secret(name):
    # Deployed runtime only accepts Swarm files. Development/evals construct adapters explicitly.
    path = os.environ.get(name + "_FILE", "")
    return Path(path).read_text().strip() if path else ""


def build_legacy():
    policy = Policy(
        os.environ.get("VETORIAL_AGENT_INBOX_ID", ""),
        frozenset(
            filter(None, os.environ.get("VETORIAL_AGENT_ALLOWLIST", "").split(","))
        ),
        frozenset(
            filter(
                None, os.environ.get("VETORIAL_AGENT_INTERNAL_NUMBERS", "").split(",")
            )
        ),
    )
    policy = Policy(
        policy.inbox_id, policy.allowlist, policy.internal,
        analysis_all=os.environ.get("VETORIAL_AGENT_ANALYSIS_ALL") == "true",
        all_contacts=os.environ.get("VETORIAL_AGENT_ALL_CONTACTS") == "true",
    )
    database = secret("DATABASE_URL")
    api_key = secret("OPENAI_API_KEY")
    if not database.startswith("postgresql") or len(api_key) < 32:
        raise RuntimeError("Secrets obrigatórios ausentes ou banco sem Postgres")
    store = Store(database)
    bridge_secret = secret("VETORIAL_AGENT_SECRET")
    crm = CRM(os.environ["CRM_URL"], bridge_secret)
    model = OpenAI(api_key, os.environ["OPENAI_MODEL"],
                   transcription=GroqTranscription(secret("GROQ_API_KEY")),
                   analyst_model=os.environ.get("OPENAI_ANALYST_MODEL"))

    def authorize(owner, key):
        state = crm.request(owner)
        return (
            not state["paused"]
            and state["allowed"]
            and key.startswith(f"{owner}:{state['version']}:agenda:")
        )

    agenda = Agenda(
        store,
        GoogleGateway(
            os.environ.get("CALENDAR_GATEWAY_URL", ""),
            secret("CALENDAR_GATEWAY_SECRET"),
        ),
        CalendlyAvailability(
            secret("CALENDLY_TOKEN"), os.environ.get("CALENDLY_EVENT_TYPE", "")
        ),
        authorize=authorize,
    )
    return store, policy, bridge_secret, Worker(store, crm, model, agenda, policy)


def build(*, gateway_routes=None):
    # Technical bootstrap only. Default preserves the A14 deployment during transition.
    source = os.environ.get("VETORIAL_AGENT_CONTROL_SOURCE", "legacy_transition")
    if source == "legacy_transition":
        return build_legacy()
    if source != "crm":
        raise RuntimeError("Fonte de configuração inválida")
    from .configuration import ConfigurationCache, EffectFence
    database, api_key = secret("DATABASE_URL"), secret("OPENAI_API_KEY")
    if not database.startswith("postgresql") or len(api_key) < 32:
        raise RuntimeError("Secrets obrigatórios ausentes ou banco sem Postgres")
    store = Store(database)
    bridge_secret = secret("VETORIAL_AGENT_SECRET")
    crm = CRM(os.environ["CRM_URL"], bridge_secret)
    cache = ConfigurationCache(crm.configuration, os.environ["VETORIAL_AGENT_DEPLOYMENT_ID"])
    # Routes are fixed technical endpoints; no behavior env is read by this factory.
    gateway_routes = {} if gateway_routes is None else dict(gateway_routes)

    def configure(snapshot):
        payload = snapshot.payload
        authority = EffectFence(snapshot, cache)
        try:
            activation = payload["activation"]
            inboxes = payload["binding"]["inbox_ids"]
            if len(inboxes) != 1:
                raise ValueError("Vínculo inválido")
            if any(activation[role] not in ("off", "pilot", "all") for role in ("atendimento", "analista")):
                raise ValueError("Ativação inválida")
            policy = Policy(
                str(inboxes[0]), frozenset(activation["allowlist"]),
                frozenset(activation["internal_numbers"]),
                analysis_all=activation["analista"] == "all",
                all_contacts=activation["atendimento"] == "all",
                attendance_enabled=activation["atendimento"] != "off",
                analysis_enabled=activation["analista"] != "off",
                behavior=payload["behavior"], authority=authority,
            )
            model = OpenAI(api_key, None, snapshot=snapshot,
                           transcription=GroqTranscription(secret("GROQ_API_KEY"), snapshot=snapshot, authority=authority),
                           authority=authority)
            integration = payload["integrations"]
            if integration["availability_provider"] != "calendly":
                raise ValueError("Disponibilidade inválida")
            if integration["google_gateway_ref"] != "vetorial-calendar-v1":
                raise ValueError("Gateway inválido")
            def authorize(owner, key):
                current = cache.pin()
                if current.fence() != snapshot.fence():
                    return False
                state = crm.request(owner)
                return not state["paused"] and state["allowed"] and key.startswith(f"{owner}:{state['version']}:agenda:")
            agenda = Agenda(store, GoogleGateway(gateway_routes.get(integration["google_gateway_ref"], ""), secret("CALENDAR_GATEWAY_SECRET"), authority=authority, calendar_id=integration["calendar_id"]),
                            CalendlyAvailability(secret("CALENDLY_TOKEN"), integration["event_type"], authority=authority),
                            authorize=authorize, snapshot=snapshot, authority=authority)
            return model, agenda, policy
        except (KeyError, TypeError, ValueError):
            from .configuration import ConfigurationUnavailable
            raise ConfigurationUnavailable("Snapshot incompleto") from None

    from .telemetry import Telemetry
    worker = Worker(store, crm, None, None, None, configuration_cache=cache, configure=configure, telemetry=Telemetry(store))
    from .telemetry import TelemetryPump, HTTPDelivery
    from .integrations import runtime_probes
    def gateway_probe_context():
        snapshot = cache.pin()
        return dict(EffectFence(snapshot, cache).data(), calendar_id=snapshot.payload["integrations"]["calendar_id"])

    worker.integration_probes = runtime_probes(api_key, secret("GROQ_API_KEY"), secret("CALENDLY_TOKEN"),
        gateway_routes.get("vetorial-calendar-v1", ""), secret("CALENDAR_GATEWAY_SECRET"),
        os.environ["CRM_URL"], bridge_secret, os.environ["VETORIAL_AGENT_DEPLOYMENT_ID"],
        gateway_context=gateway_probe_context)
    worker.telemetry_pump = TelemetryPump(worker, HTTPDelivery(os.environ["CRM_URL"], bridge_secret), worker.integration_probes)
    worker.prepare()  # An unavailable CRM does not prevent HTTP boot or consume queues.
    return store, None, bridge_secret, worker


def create_app(store, policy, bridge_secret, worker=None, *, sandbox=None, authorize_test=None):
    async def loop():
        while True:
            try:
                await asyncio.to_thread(worker.tick)
                await asyncio.to_thread(worker.analyse)
            except Exception:
                pass  # metrics/readiness expose DB, exceptions must not leak headers/PII
            await asyncio.sleep(1)

    async def telemetry_loop():
        while True:
            try:
                await asyncio.to_thread(worker.telemetry_pump.tick)
            except Exception:
                pass
            await asyncio.sleep(1)

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(loop()) if worker else None
        telemetry_task = asyncio.create_task(telemetry_loop()) if worker and getattr(worker, "telemetry_pump", None) else None
        yield
        if telemetry_task:
            telemetry_task.cancel()
            try:
                await telemetry_task
            except asyncio.CancelledError:
                pass
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/integrations")
    def integrations(request: Request):
        if not verify(b"", request.headers, bridge_secret, method="GET", path="/integrations"):
            return Response(status_code=401)
        from .integrations import NAMES
        probes = getattr(worker, "integration_probes", None)
        return {"data": probes() if probes else {name: "absent" for name in NAMES}}

    @app.get("/ready")
    def ready():
        from sqlalchemy import select
        from .store import conversations

        try:
            if worker and worker.configuration_cache is not None:
                worker.configuration_cache.pin()
            with store.engine.connect() as c:
                c.execute(select(conversations.c.id).limit(1))
            return {"ready": True}
        except Exception:
            return Response(status_code=503)

    @app.post("/events")
    async def events(request: Request):
        # Check length while streaming; do not allocate an arbitrary body before the bound.
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > 16384:
                return Response(status_code=413)
        if not verify(body, request.headers, bridge_secret):
            return Response(status_code=401)
        try:
            event = Event.model_validate_json(body).model_dump()
        except ValueError:
            return Response(status_code=422)
        current_policy = policy
        if worker and worker.configuration_cache is not None:
            from .configuration import ConfigurationUnavailable
            try:
                _, _, current_policy = worker.configure(worker.configuration_cache.pin())
            except ConfigurationUnavailable:
                return Response(status_code=503)  # Upstream durable outbox retries without ACK.
        from .configuration import ConfigurationUnavailable
        try:
            store.ingest(event, current_policy)  # ACK follows commit; duplicate ACK is safe.
        except ConfigurationUnavailable:
            return Response(status_code=503)
        return Response(status_code=202)

    # Reuse the durable runtime ledger; the CRM stores only authorization/history.
    from .sandbox_bridge import BridgeSandbox
    bridge_sandbox = BridgeSandbox(store)
    if sandbox is None and authorize_test is None:
        sandbox = bridge_sandbox
        authorize_test = lambda sid: bridge_sandbox.session(sid)['actor_id']

    @app.post("/test_sessions/manage")
    async def test_manage(request: Request):
        from .sandbox import SandboxDenied, SandboxLimit
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > 524288:
                return Response(status_code=413)
        if not verify(body, request.headers, bridge_secret, path=request.url.path):
            return Response(status_code=401)
        try:
            import json
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError()
            result = await asyncio.to_thread(bridge_sandbox.manage, data)
            return {"success": True, "data": result}
        except SandboxDenied:
            return Response(status_code=403)
        except SandboxLimit:
            return Response(status_code=429)
        except (ValueError, TypeError, KeyError):
            return Response(status_code=422)

    @app.post("/test_sessions/{session_id}/turns")
    async def test_turn(session_id: str, request: Request):
        from uuid import UUID
        from .sandbox import SandboxDenied, SandboxLimit
        # No production default. Browser-supplied identity/revision/destination is forbidden.
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > 20000:
                return Response(status_code=413)
        if not verify(body, request.headers, bridge_secret, path=request.url.path):
            return Response(status_code=401)
        if sandbox is None or authorize_test is None:
            return Response(status_code=503)
        try:
            if str(UUID(session_id)) != session_id:
                raise ValueError()
            import json
            data = json.loads(body)
            if not isinstance(data, dict) or set(data) != {"request_id", "text"}:
                raise ValueError()
            # Trusted CRM bridge checks session owner/admin + inbox + revision + expiry.
            actor_id = await asyncio.to_thread(authorize_test, session_id)
            if not actor_id:
                return Response(status_code=403)
            result = await asyncio.to_thread(sandbox.turn, session_id, actor_id,
                                            data['request_id'], data['text'])
            return {"success": True, "data": result}
        except SandboxDenied:
            return Response(status_code=403)
        except SandboxLimit:
            return Response(status_code=429)
        except (ValueError, TypeError):
            return Response(status_code=422)
        except Exception:
            return Response(status_code=503)

    return app


def production():
    try:
        # Technical bootstrap: the Google gateway URL stays an env route (not behavior config).
        routes = {"vetorial-calendar-v1": os.environ.get("CALENDAR_GATEWAY_URL", "")}
        return create_app(*build(gateway_routes=routes))
    except Exception:
        raise RuntimeError(
            "Configuração do agente incompleta; conferir secrets e variáveis do runbook"
        ) from None
