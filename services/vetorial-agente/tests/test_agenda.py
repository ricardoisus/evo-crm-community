from copy import deepcopy
from datetime import datetime, timezone
import pytest
from vetorial_agent.store import Store
from vetorial_agent.agenda import Agenda
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
        [
            {"weekday": 2, "start": "14:00", "end": "16:00"},
            {"weekday": 3, "start": "14:00", "end": "16:00"},
        ],
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


def test_no_windows_no_credentials_and_no_consent_are_closed(rig):
    a, g = rig
    a.windows = []
    assert not offer(a)["slots"]
    assert g.mutated == 0
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
