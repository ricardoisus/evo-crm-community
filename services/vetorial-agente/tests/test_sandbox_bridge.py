import json
import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from vetorial_agent.app import create_app
from vetorial_agent.policy import Policy
from vetorial_agent.security import signed
from vetorial_agent.sandbox import SandboxLimit, sessions, scopes, calls
from vetorial_agent.sandbox_bridge import BridgeSandbox
from .test_sandbox import make, SECRET, NOW
from .test_snapshot_model import envelope


def plan():
    payload = envelope()['payload']
    return dict(action='create', session_id=str(uuid.uuid4()), actor_id='operator', deployment_id='synthetic',
                revision=payload['revision'], purpose='Teste sintético', criteria='Sem rede ou efeito real',
                sample_count=3, ceiling=50000, max_turns=5, snapshot=payload, role='atendimento')


def post(client, path, data):
    body = json.dumps(data, ensure_ascii=False).encode()
    return client.post(path, content=body, headers=signed(body, SECRET, path=path))


@pytest.mark.parametrize('role', ['atendimento', 'analista'])
def test_bridge_hmac_pinned_draft_history_and_no_live_reachability(tmp_path, monkeypatch, role):
    store, _ = make(tmp_path)
    import httpx
    from vetorial_agent.adapters import CRM, OpenAI
    from vetorial_agent.agenda import Agenda
    from vetorial_agent.store import Store
    def forbidden(*args, **kwargs):
        pytest.fail('live dependency reached')
    for cls, method in [(CRM, 'command'), (Agenda, 'call'), (Store, 'quota'), (OpenAI, 'post')]:
        monkeypatch.setattr(cls, method, forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', forbidden)
    client = TestClient(create_app(store, Policy('synthetic', frozenset()), SECRET))
    data = plan()
    data['role'] = role
    data['snapshot']['roles'][role]['prompt'] = 'Revisão sintética fixada'
    manage = '/test_sessions/manage'
    assert client.post(manage, json=data).status_code == 401
    created = post(client, manage, data)
    assert created.status_code == 200
    sid = created.json()['data']['session_id']
    assert post(client, manage, data).json() == created.json()
    path = f'/test_sessions/{sid}/turns'
    turn = dict(request_id=str(uuid.uuid4()), text='Olá sintético')
    response = post(client, path, turn)
    assert response.status_code == 200, response.text
    assert post(client, path, turn).json() == response.json()
    with store.engine.connect() as c:
        session = c.execute(select(sessions)).mappings().one()
        assert session['plan']['snapshot'] == data['snapshot']
        assert len(session['plan']['history']) == 2
        assert c.execute(select(calls.c.actual)).scalar_one() == 0
    assert post(client, manage, dict(action='close', session_id=sid)).status_code == 200
    assert post(client, path, dict(request_id=str(uuid.uuid4()), text='Outra')).status_code == 429


def test_estimator_rejects_before_constructing_model(tmp_path):
    store, _ = make(tmp_path)
    seen = []
    bridge = BridgeSandbox(store, clock=lambda: NOW, model_factory=lambda p: seen.append(p))
    data = plan()
    data['ceiling'] = 1
    sid = bridge.manage(data)['session_id']
    with pytest.raises(SandboxLimit):
        bridge.turn(sid, 'operator', str(uuid.uuid4()), '😀' * 2000)
    assert seen == []
    with store.engine.connect() as c:
        assert not c.execute(select(calls)).all()
    data = plan()
    data['snapshot']['provider']['service_tier'] = 'priority'
    sid = bridge.manage(data)['session_id']
    with pytest.raises(SandboxLimit):
        bridge.turn(sid, 'operator', str(uuid.uuid4()), 'Olá')
    assert seen == []


def test_two_concurrent_sessions_cannot_bypass_operator_cap(tmp_path):
    store, _ = make(tmp_path)
    bridge = BridgeSandbox(store, clock=lambda: NOW)
    def attempt(_):
        data = plan()
        data['ceiling'] = 300000
        try:
            return bridge.manage(data)['session_id']
        except SandboxLimit:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(attempt, range(2)))
    assert sum(x is not None for x in result) == 1
    with store.engine.connect() as c:
        assert list(c.execute(select(scopes.c.allocated)).scalars()) == [300000, 300000]
