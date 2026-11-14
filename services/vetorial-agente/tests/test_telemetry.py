import json
import uuid
import pytest
from sqlalchemy import select
from vetorial_agent.store import Store
from vetorial_agent.telemetry import Telemetry, ledger, outbox, sanitize, health


def uid():
    return str(uuid.uuid4())


def execution():
    return dict(event_id=uid(), deployment_id=uid(), kind="execution", occurred_ms=1000,
                execution_id=uid(), conversation_id=uid(), role="atendimento", source="live",
                revision=1, policy_epoch=1, status="succeeded", latency_ms=1200, outcome="send_confirmed")


def heartbeat():
    return dict(event_id=uid(), deployment_id=uid(), kind="heartbeat", occurred_ms=1000,
                instance_id=uid(), revision=1, policy_epoch=1, sha256="a" * 64,
                integrations={name: "configured" for name in ("openai", "groq", "calendly", "google_gateway")})


def usage():
    return dict(event_id=uid(), deployment_id=uid(), kind="usage", occurred_ms=1000,
                call_id=uid(), execution_id=uid(), source="live", provider="openai", model="gpt-6-luna",
                input_tokens=100, cached_tokens=50, output_tokens=20, reasoning_tokens=10,
                cost_micro_usd=123, usage_state="known")


def test_lost_ack_restart_duplicate_and_backoff(tmp_path):
    url = f"sqlite:///{tmp_path / 'telemetry.sqlite'}"
    store = Store(url)
    store.migrate()
    telemetry = Telemetry(store)
    event = execution()
    telemetry.record(event)
    received = {}
    def lost_ack(batch):
        for item in batch:
            received.setdefault(item['event_id'], item)
        raise TimeoutError('secret-provider-body')
    assert telemetry.flush(lost_ack, now_ms=0) == 0
    telemetry = Telemetry(Store(url))
    telemetry.record(event)
    assert telemetry.flush(lambda _: pytest.fail('backoff ignored'), now_ms=999) == 0
    assert telemetry.flush(lambda batch: [item['event_id'] for item in batch], now_ms=1000) == 1
    assert telemetry.flush(lambda _: pytest.fail('already delivered'), now_ms=5000) == 0
    with store.engine.connect() as connection:
        assert len(connection.execute(select(ledger)).all()) == 1
        row = connection.execute(select(outbox)).mappings().one()
        assert row['attempts'] == 2 and row['delivered_ms'] == 1000
    assert list(received.values()) == [event]


def test_collision_does_not_replace_ledger_or_requeue(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.sqlite'}")
    store.migrate()
    telemetry = Telemetry(store)
    event = execution()
    telemetry.record(event)
    with pytest.raises(ValueError, match='telemetry_collision'):
        telemetry.record(dict(event, status='failed'))
    assert telemetry.flush(lambda batch: [batch[0]['event_id']], now_ms=0) == 1
    telemetry.record(event)
    assert telemetry.flush(lambda _: pytest.fail('requeued'), now_ms=10000) == 0


@pytest.mark.parametrize('field', ['prompt', 'phone', 'secret', 'error', 'response', 'token'])
def test_extra_sensitive_fields_rejected(field):
    with pytest.raises(ValueError, match='invalid_schema'):
        sanitize(dict(execution(), **{field: 'sensitive-marker'}))


@pytest.mark.parametrize('field,value', [('latency_ms', 1.5), ('revision', True), ('policy_epoch', -1), ('outcome', 'raw secret'), ('conversation_id', '+15550000000')])
def test_closed_values(field, value):
    with pytest.raises(ValueError):
        sanitize(dict(execution(), **{field: value}))


def test_status_only_and_no_provider_value_leak():
    event = heartbeat()
    assert sanitize(event) == event
    event['integrations']['openai'] = 'sk-sensitive-marker'
    with pytest.raises(ValueError, match='invalid_enum') as exc:
        sanitize(event)
    assert 'sensitive-marker' not in str(exc.value)
    assert 'configured' in json.dumps(sanitize(heartbeat()))


def test_usage_unknown_is_not_zero_and_integers_only():
    assert sanitize(usage())['cost_micro_usd'] == 123
    unknown = dict(usage(), usage_state='unknown', cost_micro_usd=None)
    assert sanitize(unknown)['cost_micro_usd'] is None
    for event in (dict(unknown, cost_micro_usd=0), dict(usage(), cost_micro_usd=0.5), dict(usage(), reasoning_tokens=21)):
        with pytest.raises(ValueError):
            sanitize(event)


def test_health_ages_and_revision_ack():
    event = heartbeat()
    def state(now, **kw):
        return health(event, now_ms=now, revision=kw.get('revision', 1), sha256='a' * 64)
    assert state(46000) == 'saudavel'
    assert state(46001) == 'atrasado'
    assert state(91000) == 'atrasado'
    assert state(91001) == 'indisponivel'
    assert state(1000, revision=2) == 'aguardando_configuracao'
    assert state(0) == 'indisponivel'
    assert health(None, now_ms=1000, revision=1, sha256='a' * 64) == 'indisponivel'


def test_batch_collision_rolls_back_execution_and_outbox(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.sqlite'}")
    store.migrate()
    telemetry = Telemetry(store)
    first, second = execution(), usage()
    telemetry.record(second)
    with pytest.raises(ValueError, match='telemetry_collision'):
        telemetry.record_batch([first, dict(second, input_tokens=101)])
    with store.engine.connect() as connection:
        assert connection.execute(select(ledger.c.event_id)).scalars().all() == [second['event_id']]
        assert connection.execute(select(outbox.c.event_id)).scalars().all() == [second['event_id']]


def test_http_hmac_and_safe_ack():
    import httpx
    from vetorial_agent.security import verify
    from vetorial_agent.telemetry import HTTPDelivery
    event = execution()
    def handle(request):
        assert request.url.path == HTTPDelivery.path
        assert verify(request.content, request.headers, 'x' * 32, path=HTTPDelivery.path)
        assert json.loads(request.content) == {'events': [event]}
        return httpx.Response(200, json={'acknowledged': [event['event_id']]})
    deliver = HTTPDelivery('http://crm.synthetic', 'x' * 32, httpx.Client(transport=httpx.MockTransport(handle)))
    assert deliver([event]) == [event['event_id']]


@pytest.mark.parametrize('status,body', [(500, {'error': 'sk-sensitive-marker'}), (200, {'acknowledged': ['sk-sensitive-marker']}), (200, {'token': 'sk-sensitive-marker'}), (302, {'acknowledged': []})])
def test_http_failure_does_not_ack_or_expose_secrets(status, body, caplog):
    import httpx
    from vetorial_agent.telemetry import HTTPDelivery
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(status, json=body)))
    assert HTTPDelivery('http://crm.synthetic', 'x' * 32, client)([execution()]) == []
    assert 'sensitive-marker' not in caplog.text


def test_same_call_cannot_be_counted_with_another_event_id(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.sqlite'}")
    store.migrate()
    telemetry = Telemetry(store)
    event = usage()
    telemetry.record(event)
    with pytest.raises(ValueError, match='telemetry_collision'):
        telemetry.record(dict(event, event_id=uid()))
    with store.engine.connect() as connection:
        assert len(connection.execute(select(ledger)).all()) == 1
