import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from vetorial_agent.store import Store, conversations, operations
from vetorial_agent.sandbox import SandboxBudget, Sandbox, SandboxLimit, SandboxDenied, calls, sessions, scopes, NoopTransport
from vetorial_agent.app import create_app
from vetorial_agent.policy import Policy
from vetorial_agent.security import signed

NOW = 1794484800000
SECRET = 'synthetic-test-secret-' * 3


def make(tmp_path):
    store = Store('sqlite:///' + str(tmp_path / 'sandbox.db'))
    store.migrate()
    return store, SandboxBudget(store, clock=lambda: NOW)


def create(budget, **kw):
    return budget.create(**dict(actor_id='operator', deployment_id='synthetic', revision=1,
        purpose='Conferir atendimento', criteria='Resposta breve e ferramenta simulada', sample_count=3, **kw))


def ident():
    return str(uuid.uuid4())


def test_cap_allocation_concurrent_and_not_refunded_on_close(tmp_path):
    store, budget = make(tmp_path)
    def attempt(_):
        try:
            return create(budget, ceiling=100000)
        except SandboxLimit:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        accepted = [x for x in pool.map(attempt, range(12)) if x]
    assert len(accepted) == 5  # operator/day 0.50, not 12 x 0.10
    for sid in accepted:
        budget.close(sid, 'operator')
    with pytest.raises(SandboxLimit):
        create(budget)
    with store.engine.connect() as c:
        assert sorted(c.execute(select(scopes.c.allocated)).scalars()) == [500000, 500000]


def test_daily_cap_across_operators_and_atomic_rollback(tmp_path):
    store, budget = make(tmp_path)
    for actor in ['a', 'b']:
        budget.create(actor_id=actor, deployment_id='synthetic', revision=1, purpose='Teste',
            criteria='Sem efeito real', sample_count=3, ceiling=500000)
    with pytest.raises(SandboxLimit):
        create(budget)
    # Failed creation cannot leave a session, increment, or partially allocated operator.
    with store.engine.connect() as c:
        assert len(c.execute(select(sessions)).all()) == 2
        assert len(c.execute(select(scopes)).all()) == 3


def test_unknown_reservation_survives_restart_and_stops_new_calls(tmp_path):
    store, budget = make(tmp_path)
    sid, rid = create(budget), ident()
    budget.reserve(sid, 'operator', rid, 'Olá', 40000)
    restarted = SandboxBudget(Store(str(store.engine.url)), clock=lambda: NOW)
    for request_id in [rid, ident()]:
        with pytest.raises(SandboxLimit):
            restarted.reserve(sid, 'operator', request_id, 'Olá', 10000)
    with store.engine.connect() as c:
        assert c.execute(select(sessions.c.spent_reserved)).scalar_one() == 40000
        assert c.execute(select(calls.c.actual)).scalar_one() is None


def test_turn_concurrency_reserves_once_and_replays_without_model(tmp_path):
    store, budget = make(tmp_path)
    sid, rid, executions = create(budget), ident(), []
    class Model:
        def run(self, context, act):
            executions.append(context)
            return {'acao': 'encerrar_silencioso', 'mensagens': [], 'motivo_interno': 'Teste'}
    runner = Sandbox(budget, lambda d, r: Model(), lambda s, t: 50000, lambda m: 10000)
    def attempt(_):
        try:
            return runner.turn(sid, 'operator', rid, 'Olá')
        except SandboxLimit:
            return None
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(attempt, range(12)))
    assert len(executions) == 1
    assert all(r is None or r['acao'] == 'encerrar_silencioso' for r in results)
    assert runner.turn(sid, 'operator', rid, 'Olá')['acao'] == 'encerrar_silencioso'
    assert len(executions) == 1
    with pytest.raises(SandboxDenied):
        runner.turn(sid, 'operator', rid, 'Texto alterado')
    with pytest.raises(SandboxDenied):
        runner.turn(sid, 'intruder', ident(), 'Olá')


@pytest.mark.parametrize('cost', [None, True, -1, 50001])
def test_unknown_or_excess_usage_stops_without_retry(tmp_path, cost):
    store, budget = make(tmp_path)
    sid = create(budget)
    runner = Sandbox(budget, lambda d, r: SimpleNamespace(run=lambda *a: {}), lambda s, t: 50000, lambda m: cost)
    with pytest.raises(SandboxLimit):
        runner.turn(sid, 'operator', ident(), 'Olá')
    with pytest.raises(SandboxLimit):
        runner.turn(sid, 'operator', ident(), 'Olá')
    with store.engine.connect() as c:
        assert len(c.execute(select(calls)).all()) == 1
        assert c.execute(select(sessions.c.spent_reserved)).scalar_one() == 50000


def test_limits_before_model_and_expiration(tmp_path):
    store, budget = make(tmp_path)
    sid = create(budget)
    def forbidden(*a):
        pytest.fail('model invoked before budget approval')
    runner = Sandbox(budget, forbidden, lambda s, t: None, forbidden)
    with pytest.raises(SandboxLimit):
        runner.turn(sid, 'operator', ident(), 'Olá')
    runner.estimate = lambda s, t: 50001
    with pytest.raises(SandboxLimit):
        runner.turn(sid, 'operator', ident(), 'Olá')
    with pytest.raises(ValueError):
        runner.turn(sid, 'operator', ident(), 'x' * 16385)
    budget.clock = lambda: NOW + 1800000
    runner.estimate = lambda s, t: 100
    with pytest.raises(SandboxLimit):
        runner.turn(sid, 'operator', ident(), 'Olá')


def test_tools_have_no_live_dependencies_or_live_quota(tmp_path, monkeypatch):
    store, budget = make(tmp_path)
    sid = create(budget)
    def forbidden(*a, **kw):
        pytest.fail('live adapter or quota reached')
    import vetorial_agent.adapters as adapters
    import vetorial_agent.agenda as agenda
    monkeypatch.setattr(adapters.CRM, 'command', forbidden)
    monkeypatch.setattr(agenda.Agenda, 'call', forbidden)
    monkeypatch.setattr(Store, 'quota', forbidden)
    monkeypatch.setattr(Store, 'quota_key', forbidden)
    import httpx
    monkeypatch.setattr(httpx.Client, 'request', forbidden)
    class Model:
        def run(self, context, act):
            for name in ['consultar_horarios', 'agendar', 'remarcar', 'cancelar', 'atualizar_negocio', 'transferir_para_ricardo']:
                assert act(name, {})['simulado'] is True
            with pytest.raises(SandboxDenied):
                act('send', {'phone': '+15550000002'})
            return {'acao': 'responder', 'mensagens': ['Teste']}
    assert Sandbox(budget, lambda d, r: Model(), lambda s, t: 100, lambda m: 0).turn(sid, 'operator', ident(), 'Olá')['acao'] == 'responder'
    with pytest.raises(SandboxDenied):
        NoopTransport().send('anything')
    with store.engine.connect() as c:
        assert not c.execute(select(conversations)).all()
        assert not c.execute(select(operations)).all()


def test_http_hmac_closed_schema_authorization_and_disabled_default(tmp_path):
    store, budget = make(tmp_path)
    sid, seen = create(budget), []
    runner = Sandbox(budget, lambda d, r: SimpleNamespace(run=lambda *a: seen.append((d, r)) or {}),
                     lambda s, t: 100, lambda m: 0)
    path = f'/test_sessions/{sid}/turns'
    body = json.dumps({'request_id': ident(), 'text': 'Olá'}).encode()
    client = TestClient(create_app(store, Policy('synthetic', frozenset()), SECRET,
                                  sandbox=runner, authorize_test=lambda s: 'operator'))
    assert client.post(path, content=body).status_code == 401
    assert client.post(path, content=body, headers=signed(body, SECRET)).status_code == 401
    extra = json.dumps({'request_id': ident(), 'text': 'Olá', 'phone': '+15550000002'}).encode()
    assert client.post(path, content=extra, headers=signed(extra, SECRET, path=path)).status_code == 422
    assert client.post(path, content=body, headers=signed(body, SECRET, path=path)).status_code == 200
    assert seen == [('synthetic', 1)]
    assert client.post(path, content=body, headers=signed(body, SECRET, path=path)).status_code == 200
    assert len(seen) == 1
    blocked = TestClient(create_app(store, Policy('synthetic', frozenset()), SECRET,
                                   sandbox=runner, authorize_test=lambda s: None))
    assert blocked.post(path, content=body, headers=signed(body, SECRET, path=path)).status_code == 403
    disabled = TestClient(create_app(store, Policy('synthetic', frozenset()), SECRET))
    assert disabled.post(path, content=body, headers=signed(body, SECRET, path=path)).status_code == 503
    assert len(seen) == 1


def test_same_snapshot_prompt_model_and_tool_validation_with_provider_double(tmp_path, monkeypatch):
    from vetorial_agent.adapters import OpenAI
    from .test_snapshot_model import envelope, pin
    store, budget = make(tmp_path)
    sid, requests = create(budget), []
    snapshot = pin(envelope())
    snapshot.payload['roles']['atendimento']['prompt'] = 'Prompt sintético da revisão CRM'
    def factory(deployment_id, revision):
        assert deployment_id == 'synthetic' and revision == 1
        model = OpenAI('synthetic', 'ignored', snapshot=snapshot)
        def post(path, **kwargs):
            requests.append(kwargs['json'])
            if len(requests) == 1:
                return {'status': 'completed', 'output': [{'type': 'function_call', 'name': 'consultar_horarios',
                    'arguments': '{"periodo":"amanhã"}', 'call_id': 'synthetic-call'}]}
            return {'status': 'completed', 'output': [{'type': 'message', 'content': [{'text': json.dumps({
                'acao': 'responder', 'mensagens': ['Olá!'], 'motivo_interno': 'Teste sintético'})}]}]}
        monkeypatch.setattr(model, 'post', post)
        model.http.close()  # provider is entirely replaced, no socket is used
        return model
    result = Sandbox(budget, factory, lambda s, t: 50000, lambda m: 0).turn(sid, 'operator', ident(), 'Olá')
    assert result['mensagens'] == ['Olá!']
    assert len(requests) == 2
    assert requests[0]['model'] == 'gpt-6-luna'
    assert requests[0]['instructions'].startswith('Prompt sintético da revisão CRM')
    assert any(item.get('type') == 'function_call_output' and 'simulado' in item['output']
               for item in requests[1]['input'])


def test_daily_scope_is_front_not_caller_selected_deployment(tmp_path):
    store, budget = make(tmp_path)
    create(budget, ceiling=500000)
    with pytest.raises(SandboxLimit):
        budget.create(actor_id='operator', deployment_id='different-deployment', revision=1,
            purpose='Teste', criteria='Sem efeito real', sample_count=3, ceiling=1)


def test_completed_turn_settlement_is_idempotent_and_turn_limit_is_enforced(tmp_path):
    store, budget = make(tmp_path)
    sid, rid = create(budget, max_turns=1), ident()
    budget.reserve(sid, 'operator', rid, 'Olá', 50000)
    budget.settle(sid, rid, {'ok': True}, 10)
    budget.settle(sid, rid, {'ok': True}, 10)
    with pytest.raises(SandboxDenied):
        budget.settle(sid, rid, {'ok': True}, 11)
    with pytest.raises(SandboxLimit):
        budget.reserve(sid, 'operator', ident(), 'Outra mensagem', 1)
    with store.engine.connect() as c:
        assert c.execute(select(sessions.c.spent_reserved)).scalar_one() == 10
