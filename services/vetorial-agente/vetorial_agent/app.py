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


def build():
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


def create_app(store, policy, bridge_secret, worker=None):
    async def loop():
        while True:
            try:
                await asyncio.to_thread(worker.tick)
                await asyncio.to_thread(worker.analyse)
            except Exception:
                pass  # metrics/readiness expose DB, exceptions must not leak headers/PII
            await asyncio.sleep(1)

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(loop()) if worker else None
        yield
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/ready")
    def ready():
        from sqlalchemy import select
        from .store import conversations

        try:
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
        store.ingest(event, policy)  # ACK follows commit; duplicate ACK is safe.
        return Response(status_code=202)

    return app


def production():
    try:
        return create_app(*build())
    except Exception:
        raise RuntimeError(
            "Configuração do agente incompleta; conferir secrets e variáveis do runbook"
        ) from None
