import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from vetorial_agent.store import Store, events
from vetorial_agent.policy import Policy
from vetorial_agent.runtime import Worker, consent
from vetorial_agent.calendar import TestCalendar
from vetorial_agent.adapters import validate_output, validate_action
from vetorial_agent.security import signed, verify
from vetorial_agent.app import create_app
from .fakes import Clock, CRMStub, ModelStub

PHONE = "+15550000002"
POLICY = Policy("box", frozenset([PHONE]))


def event(v=1, **kw):
    return dict(
        dict(
            event_id=f"e{v}",
            inbox_id="box",
            conversation_id="conv",
            message_id=f"m{v}",
            version=v,
            paused=False,
            kind="incoming",
            phone=PHONE,
            audio=False,
            occurred_at=1000,
        ),
        **kw,
    )


@pytest.fixture
def rig(tmp_path):
    store = Store("sqlite:///" + str(tmp_path / "runtime.db"))
    store.migrate()
    crm = CRMStub()
    model = ModelStub()
    clock = Clock()
    calendar = TestCalendar(["2026-11-18T17:00:00Z"])
    return Worker(
        store, crm, model, calendar, POLICY, sleep=clock.sleep, clock=clock
    ), clock


def seed(worker, clock, v=1, **kw):
    worker.store.ingest(event(v, **kw), POLICY, clock())
    worker.crm.context["version"] = v


def test_burst_waits_since_last_message_and_audio_30(rig):
    w, c = rig
    seed(w, c)
    c.now += 10
    seed(w, c, 2)
    c.now += 19
    assert not w.tick()
    c.now += 1
    assert w.tick()
    assert w.model.calls == 1
    seed(w, c, 3, audio=True)
    c.now += 29
    assert not w.tick()
    c.now += 1
    assert w.tick()


def test_recheck_during_generation_discards_draft(rig):
    w, c = rig
    seed(w, c)
    c.now += 20
    w.model.hook = lambda: seed(w, c, 2)
    w.tick()
    assert w.crm.sent == []
    assert w.store.get("conv")["version"] == 2
    w.model.hook = None
    c.now += 20
    w.tick()
    assert len(w.crm.sent) == 2


def test_recheck_during_typing(rig):
    w, c = rig
    seed(w, c)
    c.now += 20
    w.crm.hook = lambda kind: seed(w, c, 2) if kind == "typing" else None
    w.tick()
    assert w.crm.sent == []


def test_recheck_between_blocks(rig):
    w, c = rig
    seed(w, c)
    c.now += 20
    w.crm.hook = lambda kind: seed(w, c, 2) if kind == "send" else None
    w.tick()
    assert len(w.crm.sent) == 1


def test_pause_from_crm_before_event_delivery_blocks_send(rig):
    w, c = rig
    seed(w, c)
    c.now += 20
    w.model.hook = lambda: w.crm.context.update(paused=True)
    w.tick()
    assert not w.crm.sent


def test_pause_persists_restart_only_resume_clears(rig):
    w, c = rig
    seed(w, c)
    ev = event(2)
    ev.update(kind="human", paused=True)
    w.store.ingest(ev, POLICY, c())
    w.store = Store(str(w.store.engine.url))
    seed(w, c, 3)
    c.now += 60
    assert not w.tick()
    assert w.store.get("conv")["paused"]
    ev = event(4)
    ev["kind"] = "resume"
    w.store.ingest(ev, POLICY, c())
    w.crm.context["version"] = 4
    c.now += 20
    assert w.tick()
    assert len(w.crm.sent) == 2


def test_crash_after_remote_send_commit_uses_same_receipt(rig):
    w, c = rig
    seed(w, c)
    c.now += 20
    original = w.crm.command
    crashed = [False]

    def send(*a, **kw):
        r = original(*a, **kw)
        if a[3] == "send" and not crashed[0]:
            crashed[0] = True
            raise RuntimeError("timeout_after_remote_commit")
        return r

    w.crm.command = send
    w.tick()
    assert len(w.crm.sent) == 1
    w.store = Store(str(w.store.engine.url))
    c.now += 16
    w.tick()
    assert len(w.crm.sent) == 2
    assert w.model.calls == 1
    assert w.store.get("conv")["done"] == 1


def test_crash_lease_recovery_and_dedup(rig):
    w, c = rig
    seed(w, c)
    assert not w.store.ingest(event(), POLICY, c())
    c.now += 20
    assert w.store.claim(c())
    w.store = Store(str(w.store.engine.url))
    assert not w.tick()
    c.now += 901
    assert w.tick()
    assert len(w.crm.sent) == 2


def test_out_of_order_does_not_unpause(rig):
    w, c = rig
    seed(w, c, 3)
    ev = event(2)
    ev.update(kind="resume")
    assert not w.store.ingest(ev, POLICY, c())
    assert w.store.get("conv")["version"] == 3


@pytest.mark.parametrize(
    "p",
    [
        Policy("box"),
        Policy("other", frozenset([PHONE])),
        Policy("box", frozenset([PHONE]), frozenset([PHONE])),
    ],
)
def test_policies_closed(rig, p):
    w, c = rig
    w.policy = p
    w.store.ingest(event(), p, c())
    c.now += 60
    w.tick()
    assert not w.crm.sent


def test_emoji_and_silent_no_public_note(rig):
    w, c = rig
    w.model.output = dict(
        acao="encerrar_silencioso", mensagens=[], motivo_interno="Spam repetido"
    )
    seed(w, c)
    c.now += 20
    w.tick()
    assert not w.crm.sent
    assert w.crm.commands[-1]["kind"] == "silent"
    validate_output(dict(acao="emoji", mensagens=["🙏"], motivo_interno="Fim"))
    with pytest.raises(Exception):
        validate_output(dict(acao="emoji", mensagens=["OK"], motivo_interno="Fim"))


def test_quota_durable_across_restart(rig):
    w, c = rig
    seed(w, c)
    for n in range(20):
        w.store.operation(f"q{n}", "conv", "send", {}, lambda _: {}, now=c())
    c.now += 20
    w.tick()
    assert not w.crm.sent
    assert w.crm.commands[-1]["kind"] == "handoff"


def test_media_enters_context_and_cache(rig):
    w, c = rig
    w.crm.context["messages"] = [
        {"role": "user", "content": "", "attachments": [{"id": "audio", "bytes": 30}]}
    ]
    seed(w, c, audio=True)
    c.now += 30
    w.tick()
    assert "cardiologista" in w.model.contexts[0]["messages"][0]["media_text"][0]


def test_analyst_private_and_cas(rig):
    w, c = rig
    seed(w, c)
    c.now += 20
    w.tick()
    n = len(w.crm.sent)
    c.now += 301
    w.analyse()
    assert len(w.crm.sent) == n
    assert w.crm.commands[-1]["kind"] == "analysis"
    assert w.crm.commands[-1]["fields"] == {"cidade": "São Paulo"}
    assert w.store.get("conv")["analysed"] == 1


def test_invalid_email_action_rejected():
    with pytest.raises(Exception):
        validate_action(
            "agendar",
            dict(horario="2026-11-18T17:00:00Z", nome="Teste", email="a@gmailcom"),
        )


def test_hmac_covers_body_time_method_path():
    h = signed(b"{}", "s" * 32, 100, method="GET", path="/one")
    assert verify(b"{}", h, "s" * 32, 100, method="GET", path="/one")
    for body, method, path, now in [
        (b"x", "GET", "/one", 100),
        (b"{}", "POST", "/one", 100),
        (b"{}", "GET", "/two", 100),
        (b"{}", "GET", "/one", 200),
    ]:
        assert not verify(body, h, "s" * 32, now, method=method, path=path)


def test_ingress_ack_after_commit_replay_and_bad_signature(rig):
    w, c = rig
    app = create_app(w.store, POLICY, "s" * 32)
    client = TestClient(app)
    raw = json.dumps(event()).encode()
    headers = signed(raw, "s" * 32)
    assert client.post("/events", content=raw, headers=headers).status_code == 202
    assert client.post("/events", content=raw, headers=headers).status_code == 202
    assert client.post("/events", content=raw).status_code == 401
    assert client.post("/events", content=b"x" * 16385).status_code == 413
    assert client.get("/ready").status_code == 200
    with w.store.engine.connect() as db:
        assert len(db.execute(select(events)).all()) == 1


def test_consent_is_conversation_bound():
    ctx = {
        "messages": [
            {"role": "user", "content": "teste@example.invalid"},
            {"role": "assistant", "content": "Posso cancelar a reunião? Tem certeza?"},
            {"role": "user", "content": "Sim, pode cancelar"},
        ]
    }
    assert consent(ctx, "cancelar", {"email": "teste@example.invalid"})
    assert not consent(ctx, "cancelar", {"email": "outra@example.invalid"})


def test_failed_generation_budget_stops_retry_loop(rig):
    w, c = rig
    seed(w, c)
    c.now += 20

    def broken():
        raise RuntimeError("synthetic_error")

    w.model.hook = broken
    for _ in range(4):
        w.tick()
        c.now += 16
    assert w.model.calls == 3
    assert w.store.get("conv")["paused"]
    assert w.crm.commands[-1]["kind"] == "handoff"


def test_analyst_operates_privately_during_human_pause(rig):
    w, c = rig
    seed(w, c)
    ev = event(2)
    ev.update(kind="human", paused=True)
    w.store.ingest(ev, POLICY, c())
    w.crm.context.update(version=2, paused=True)
    c.now += 301
    w.analyse()
    assert not w.crm.sent
    assert w.crm.commands[-1]["kind"] == "analysis"


def test_analyst_failure_has_durable_budget(rig):
    w, c = rig
    seed(w, c)
    c.now += 301

    def broken():
        raise RuntimeError("synthetic")

    w.model.hook = broken
    for _ in range(5):
        w.analyse()
        c.now += 16
    assert w.model.calls == 3
    assert not w.crm.sent


def test_analyst_all_does_not_expand_public_allowlist(rig):
    w, c = rig
    p = Policy("box", frozenset(), analysis_all=True)
    w.policy = p
    w.store.ingest(event(), p, c())
    w.crm.context.update(allowed=False, eligible=False, analysis_allowed=True)
    c.now += 301
    assert not w.tick()
    w.analyse()
    assert not w.crm.sent
    assert w.crm.commands[-1]["kind"] == "analysis"
    assert not p.allows(PHONE)
    assert p.allows_analysis(PHONE)


def test_analyst_closed_and_internal_exclusion(rig):
    w, c = rig
    for p in [Policy("box"), Policy("box", internal=frozenset([PHONE]), analysis_all=True)]:
        w.policy = p
        w.store.ingest(event(), p, c())
        w.crm.context.update(analysis_allowed=True)
        c.now += 301
        w.analyse()
        assert w.model.calls == 0
        assert not w.crm.sent


@pytest.mark.parametrize("kind", ["group", "status"])
def test_broadcast_cannot_enter_analysis(rig, kind):
    w, c = rig
    assert not w.store.ingest(event(kind=kind), POLICY, c())
    c.now += 301
    w.analyse()
    assert w.model.calls == 0


def test_analyst_all_reads_media_through_private_capability(rig):
    w, c = rig
    p = Policy("box", analysis_all=True)
    w.policy = p
    w.store.ingest(event(), p, c())
    w.crm.context.update(allowed=False, eligible=False, analysis_allowed=True, paused=True)
    w.crm.context["messages"] = [
        {"role": "user", "content": "", "attachments": [{"id": "a", "bytes": 30}]}
    ]
    c.now += 301
    w.analyse()
    assert [cmd["kind"] for cmd in w.crm.commands] == ["analysis_media", "analysis"]
    assert "cardiologista" in w.model.contexts[0]["messages"][0]["media_text"][0]
    assert not w.crm.sent


def test_first_typing_block_caps_at_six_seconds_next_at_twelve(rig):
    w,c=rig
    w.model.output={'acao':'responder','mensagens':['a'*300,'b'*300],'motivo_interno':'teste'}
    seed(w,c);c.now+=20
    start=c();sent=[]
    w.crm.hook=lambda kind:sent.append(c()-start) if kind=='send' else None
    assert w.tick()
    assert sent==[6,18]


def test_general_gate_keeps_internal_numbers_and_invalid_phones_blocked():
    p = Policy('box', internal=frozenset([PHONE]), all_contacts=True)
    assert p.allows('+15550000003')
    assert p.allows_analysis('+15550000003')
    for phone in [PHONE, 'invalid', '', '15550000003']:
        assert not p.allows(phone)
        assert not p.allows_analysis(phone)
    assert not Policy('box', frozenset(['*'])).allows('+15550000003')
    assert not Policy('', all_contacts=True).allows('+15550000003')


def test_ignored_contact_is_logged_without_personal_data(rig, caplog):
    w, c = rig
    phone = '+15550000003'
    assert w.store.ingest(event(phone=phone), POLICY, c())
    assert 'event=ignored reason=outside_allowlist' in caplog.text
    assert phone not in caplog.text
    c.now += 301
    w.tick()
    w.analyse()
    assert w.model.calls == 0
    assert not w.crm.sent
