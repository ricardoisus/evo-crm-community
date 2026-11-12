from copy import deepcopy
from datetime import datetime, timedelta, timezone
import httpx
import pytest
from vetorial_agent.store import Store
from vetorial_agent.agenda import Agenda, CalendlyAvailability, FixedAvailability
from vetorial_agent.calendar import label_br

SLOT = "2026-11-18T17:00:00Z"
OTHER = "2026-11-19T17:00:00Z"


class GoogleStub:
    def __init__(self):
        self.events = {}
        self.busy = False
        self.crash = False
        self.mutated = 0

    def call(self, op, **args):
        if op == "freebusy":
            busy = self.busy or any(
                e["status"] != "cancelled" and e["start"]["dateTime"] == args["timeMin"]
                for e in self.events.values()
            )
            return {
                "calendars": {
                    "only-calendar": {
                        "busy": [{"start": args["timeMin"], "end": args["timeMax"]}]
                        if busy
                        else []
                    }
                }
            }
        if op == "list":
            return {"items": list(self.events.values())}
        if op == "get":
            return deepcopy(self.events.get(args["id"]))
        if op == "delete":
            self.events[args["id"]]["status"] = "cancelled"
            self.mutated += 1
            return {}
        if op in ("insert", "patch"):
            d = deepcopy(args["body"])
            d["id"] = args["id"]
            d["hangoutLink"] = "https://meet.google.com/synthetic"
            self.events[args["id"]] = d
            self.mutated += 1
            if self.crash:
                self.crash = False
                raise RuntimeError("timeout_after_google_commit")
            return deepcopy(d)
        raise AssertionError(op)


@pytest.fixture
def rig(tmp_path):
    store = Store("sqlite:///" + str(tmp_path / "agenda.db"))
    store.migrate()
    google = GoogleStub()
    agenda = Agenda(
        store,
        google,
        FixedAvailability(
            [
                {"weekday": 2, "start": "14:00", "end": "16:00"},
                {"weekday": 3, "start": "14:00", "end": "16:00"},
            ]
        ),
        clock=lambda: datetime(2026, 11, 11, 12, tzinfo=timezone.utc),
        authorize=lambda o, k: True,
    )
    return agenda, google


def offer(a, owner="c", key="offer"):
    return a.call(owner, key, "consultar_horarios", {"periodo": "próxima semana"})


def book(a, owner="c", key="book", slot=SLOT, email="teste@example.invalid"):
    return a.call(
        owner, key, "agendar", dict(horario=slot, nome="Sintético", email=email)
    )


def test_full_lifecycle_45min_meet_invite_ownership(rig):
    a, g = rig
    assert offer(a)["slots"]
    assert book(a)["sucesso"]
    assert g.mutated == 1
    event = list(g.events.values())[0]
    assert event["end"]["dateTime"] == "2026-11-18T17:45:00Z"
    assert event["attendees"][0]["email"] == "teste@example.invalid"
    assert (
        event["conferenceData"]["createRequest"]["conferenceSolutionKey"]["type"]
        == "hangoutsMeet"
    )
    assert label_br(SLOT) == "quarta-feira, 18 de novembro de 2026, às 14h00"
    offer(a, key="offer2")
    r = a.call(
        "c", "move", "remarcar", dict(email="teste@example.invalid", novo_horario=OTHER)
    )
    assert r["sucesso"]
    assert len(g.events) == 1
    assert not a.call(
        "intruder", "bad", "cancelar", dict(email="teste@example.invalid")
    )["sucesso"]
    assert not a.call("c", "bademail", "cancelar", dict(email="outro@example.invalid"))[
        "sucesso"
    ]
    assert a.call("c", "cancel", "cancelar", dict(email="teste@example.invalid"))[
        "sucesso"
    ]


def test_conflict_after_offer_never_confirms(rig):
    a, g = rig
    offer(a)
    g.busy = True
    assert book(a)["erro"] == "horário indisponível"
    assert g.mutated == 0


def test_crash_after_google_commit_reconciles_and_different_slot_cannot_duplicate(rig):
    a, g = rig
    offer(a)
    g.crash = True
    assert not book(a)["sucesso"]
    assert g.mutated == 1
    assert (
        book(a, key="next-generation", slot=OTHER)["erro"]
        == "reuniao_existente_use_remarcar"
    )
    assert book(a, key="retry-same-slot")["sucesso"]
    assert g.mutated == 1


def test_replay_and_collision(rig):
    a, g = rig
    offer(a)
    assert book(a)["sucesso"]
    assert book(a)["sucesso"]
    assert g.mutated == 1
    with pytest.raises(ValueError):
        book(a, slot=OTHER)


def test_no_availability_no_credentials_and_no_consent_are_closed(rig):
    a, g = rig
    availability, a.availability = a.availability, None
    assert not offer(a)["slots"]
    assert g.mutated == 0
    a.availability = availability
    a.authorize = lambda o, k: False
    assert not book(a)["sucesso"]


def test_no_offer_cannot_book(rig):
    a, g = rig
    assert book(a)["erro"] == "consultar_horarios_antes"


def test_requested_period_restricts_real_slots(rig):
    a, g = rig
    r = a.call("c", "today", "consultar_horarios", {"periodo": "hoje"})
    assert all("2026-11-11" in s["inicio"] for s in r["slots"])
    r = a.call("c", "date", "consultar_horarios", {"periodo": "2026-11-19"})
    assert all("2026-11-19" in s["inicio"] for s in r["slots"])


def test_cross_conversation_conflict_and_restart(rig):
    a, g = rig
    offer(a)
    offer(a, "second", "offer-other")
    assert book(a)["sucesso"]
    assert not book(a, "second", "book-other")["sucesso"]
    a.store = Store(str(a.store.engine.url))
    assert book(a)["sucesso"]
    assert g.mutated == 1


ET = "https://api.calendly.com/event_types/d604c9e1-b09a-4d71-adec-bf1f1532ba9c"
NOW = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)


class CalendlyResponse:
    def __init__(self, status, body):
        self.status_code, self.body = status, body

    def json(self):
        return self.body


class CalendlyStub:
    """Fake Calendly HTTP: records requests, answers available_times like the real API."""

    def __init__(self, starts, status=200):
        self.starts, self.status, self.requests = list(starts), status, []

    def __call__(self, url, params, headers, timeout, follow_redirects):
        self.requests.append(dict(url=url, params=params, headers=headers))
        lo = datetime.fromisoformat(params["start_time"].replace("Z", "+00:00"))
        hi = datetime.fromisoformat(params["end_time"].replace("Z", "+00:00"))
        assert hi - lo <= timedelta(days=7)
        assert lo > NOW
        items = [
            {"status": "available", "start_time": s.replace("Z", ".000000Z"), "invitees_remaining": 1}
            for s in self.starts
            if lo <= datetime.fromisoformat(s.replace("Z", "+00:00")) < hi
        ]
        return CalendlyResponse(self.status, {"collection": items})


def calendly(starts, status=200):
    stub = CalendlyStub(starts, status)
    return CalendlyAvailability("x" * 40, ET, get=stub, now=lambda: NOW), stub


@pytest.fixture
def crig(tmp_path):
    store = Store("sqlite:///" + str(tmp_path / "agenda.db"))
    store.migrate()
    google = GoogleStub()
    source, stub = calendly([SLOT, OTHER, "2026-11-24T13:00:00Z"])
    agenda = Agenda(store, google, source, clock=lambda: NOW, authorize=lambda o, k: True)
    return agenda, google, stub


def test_calendly_request_is_fixed_bearer_with_explicit_user_agent_and_chunked(crig):
    a, g, stub = crig
    r = a.call("c", "o", "consultar_horarios", {"periodo": "próximos dias"})
    assert [s["inicio"] for s in r["slots"]] == [SLOT, OTHER, "2026-11-24T13:00:00Z"]
    assert len(stub.requests) == 3  # 15 days split into ≤7-day Calendly ranges
    for req in stub.requests:
        assert req["url"] == "https://api.calendly.com/event_type_available_times"
        assert req["params"]["event_type"] == ET
        assert req["headers"]["Authorization"] == "Bearer " + "x" * 40
        assert "python" not in req["headers"]["User-Agent"].lower()
    assert g.mutated == 0


def test_calendly_period_and_booking_go_to_google(crig):
    a, g, stub = crig
    r = a.call("c", "o", "consultar_horarios", {"periodo": "2026-11-19"})
    assert [s["inicio"] for s in r["slots"]] == [OTHER]
    assert a.call("c", "b", "agendar", dict(horario=OTHER, nome="S", email="teste@example.invalid"))["sucesso"]
    assert g.mutated == 1
    assert list(g.events.values())[0]["start"]["dateTime"] == OTHER


def test_slot_taken_in_calendly_after_offer_never_books(crig):
    a, g, stub = crig
    offer(a)
    stub.starts.remove(SLOT)
    assert book(a)["erro"] == "horário indisponível"
    assert g.mutated == 0


def test_google_busy_blocks_even_when_calendly_lists_slot(crig):
    a, g, stub = crig
    offer(a)
    g.busy = True
    assert book(a)["erro"] == "horário indisponível"
    assert g.mutated == 0


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_calendly_errors_fail_closed(tmp_path, status):
    store = Store("sqlite:///" + str(tmp_path / "agenda.db"))
    store.migrate()
    source, _ = calendly([SLOT], status)
    a = Agenda(store, GoogleStub(), source, clock=lambda: NOW, authorize=lambda o, k: True)
    r = offer(a)
    assert r["erro"] == "agenda_indisponivel" and r["slots"] == []


@pytest.mark.parametrize(
    "token,event_type",
    [("", ET), ("x" * 40, ""), ("x" * 40, "https://evil.example/event_types/d604c9e1-b09a-4d71-adec-bf1f1532ba9c")],
)
def test_calendly_misconfiguration_fails_closed_without_request(token, event_type):
    stub = CalendlyStub([SLOT])
    source = CalendlyAvailability(token, event_type, get=stub, now=lambda: NOW)
    with pytest.raises(RuntimeError):
        source.slots(NOW, NOW + timedelta(days=1))
    assert stub.requests == []


def test_calendly_network_error_fails_closed():
    def boom(*a, **k):
        raise httpx.ConnectError("down")

    source = CalendlyAvailability("x" * 40, ET, get=boom, now=lambda: NOW)
    with pytest.raises(RuntimeError):
        source.slots(NOW, NOW + timedelta(days=1))
