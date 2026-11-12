"""Optional real Postgres concurrency checks. Dedicated synthetic DB only."""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy.engine import make_url
from vetorial_agent.store import Store
from vetorial_agent.policy import Policy
from vetorial_agent.agenda import Agenda, FixedAvailability
from .test_agenda import GoogleStub, SLOT
from datetime import datetime, timezone

URL = os.environ.get("POSTGRES_TEST_URL", "")
pytestmark = pytest.mark.skipif(
    not URL, reason="POSTGRES_TEST_URL points to dedicated synthetic DB only"
)


def setup():
    assert make_url(URL).database == "vetorial_agent_a12_test"
    s = Store(URL)
    s.migrate()
    return s


def test_pg_duplicate_ack_and_concurrent_claim():
    s = setup()
    ident = str(uuid.uuid4())
    policy = Policy(ident, frozenset(["+15550000002"]))
    event = dict(
        event_id=ident,
        inbox_id=ident,
        conversation_id=ident,
        message_id=ident,
        version=1,
        paused=False,
        kind="incoming",
        phone="+15550000002",
        audio=False,
        occurred_at=1000,
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: s.ingest(event, policy, 1000), range(20)))
    assert sum(results) == 1
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: s.claim(1021), range(8)))
    assert len([r for r in claims if r and r["id"] == ident]) == 1
    s.finish(ident, 1, done=1, paused=True)
    assert not Store(URL).ingest(event, policy, 2000)


def test_pg_calendar_serializes_overlapping_bookings_and_replays():
    s = setup()
    g = GoogleStub()
    a = Agenda(
        s,
        g,
        FixedAvailability([{"weekday": 2, "start": "14:00", "end": "16:00"}]),
        clock=lambda: datetime(2026, 11, 11, 12, tzinfo=timezone.utc),
        authorize=lambda o, k: True,
    )
    owners = [str(uuid.uuid4()), str(uuid.uuid4())]
    for o in owners:
        a.call(o, o + ":offer", "consultar_horarios", {"periodo": "próxima semana"})

    def book(o):
        return a.call(
            o,
            o + ":book",
            "agendar",
            dict(horario=SLOT, nome="Teste", email="teste@example.invalid"),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(book, owners))
    assert sum(r.get("sucesso", False) for r in results) == 1
    assert g.mutated == 1
    winner = owners[next(i for i, r in enumerate(results) if r.get("sucesso"))]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(book, [winner] * 4))
    assert all(r["sucesso"] for r in results)
    assert g.mutated == 1
