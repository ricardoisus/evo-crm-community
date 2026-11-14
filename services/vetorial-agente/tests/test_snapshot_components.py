"""Synthetic providers only: snapshot limits and stale revision/epoch at every boundary."""
import base64
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock

import pytest
from sqlalchemy import select
from vetorial_agent.configuration import ConfigurationUnavailable, digest
from vetorial_agent.store import events, operations
from .test_crm_boot import envelope, boot
from .fakes import CRMStub, ModelStub


def setup(monkeypatch, tmp_path, mutate=None):
    config = envelope()
    if mutate:
        mutate(config['payload'])
    config['sha256'] = digest(config['payload'])
    built = boot(monkeypatch, tmp_path, config)
    return config, built[0], built[3]


def change(config, worker, field='policy_epoch'):
    config['payload'][field] += 1
    config['sha256'] = digest(config['payload'])
    worker.configuration_cache.refresh(force=True)


def event(**extra):
    return dict(event_id='evt', inbox_id='inbox', conversation_id='conv', message_id='msg',
                version=1, paused=False, kind='incoming', phone='+15550000002',
                occurred_at=1, **extra)


@pytest.mark.parametrize('field', ['revision', 'policy_epoch'])
def test_policy_store_and_all_adapters_reject_stale_fence(monkeypatch, tmp_path, field):
    config, store, worker = setup(monkeypatch, tmp_path)
    policy, model, agenda = worker.policy, worker.model, worker.calendar
    change(config, worker, field)
    calls = [
        lambda: policy.allows('+15550000002'),
        lambda: policy.allows_analysis('+15550000002'),
        lambda: store.ingest(event(), policy, now=100),
        lambda: store.claim(100, authority=worker.authority),
        lambda: store.analyse_due(100, authority=worker.authority),
        lambda: store.operation('key', 'conv', 'send', {}, Mock(), authority=worker.authority),
        lambda: agenda.call('conv', 'key', 'cancelar', {'email': 's@example.invalid'}),
        lambda: agenda.google.call('delete', id='synthetic'),
        lambda: agenda.availability.slots(datetime.now(timezone.utc), datetime.now(timezone.utc)),
        lambda: model.media('audio/ogg', base64.b64encode(b'audio').decode()),
        lambda: model.transcription.transcribe('audio/ogg', b'audio'),
    ]
    for call in calls:
        with pytest.raises(ConfigurationUnavailable):
            call()
    with store.engine.connect() as c:
        assert c.execute(select(events)).first() is None
        assert c.execute(select(operations)).first() is None


def test_policy_modes_lists_store_debounce_and_fence(monkeypatch, tmp_path):
    def mutate(p):
        p['activation']['atendimento'] = 'all'
        p['activation']['analista'] = 'off'
        p['activation']['internal_numbers'] = ['+15550000001']
        p['behavior']['audio_debounce_ms'] = 91000
    config, store, w = setup(monkeypatch, tmp_path, mutate)
    assert w.policy.allows('+15550000003')
    assert not w.policy.allows('+15550000001')
    assert not w.policy.allows_analysis('+15550000003')
    store.ingest(event(audio=True), w.policy, now=100)
    assert store.get('conv')['due'] == 191
    with store.engine.connect() as c:
        assert c.execute(select(events.c.payload)).scalar()['configuration'] == w.snapshot.fence()
    config['payload']['activation']['atendimento'] = 'off'
    change(config, w)
    assert w.prepare()
    assert not w.policy.allows('+15550000002')


def test_store_operation_keeps_fence_and_rejects_stale_receipt_replay(monkeypatch, tmp_path):
    config, store, w = setup(monkeypatch, tmp_path)
    invoke = Mock(return_value={'ok': True})
    assert store.operation('k', 'conv', 'fields', {}, invoke, authority=w.authority) == {'ok': True}
    with store.engine.connect() as c:
        assert c.execute(select(operations.c.payload)).scalar()['configuration'] == w.snapshot.fence()
    change(config, w)
    with pytest.raises(ConfigurationUnavailable):
        store.operation('k', 'conv', 'fields', {}, invoke, authority=w.authority)
    invoke.assert_called_once()


def test_worker_limits_commands_drafts_and_media_from_snapshot(monkeypatch, tmp_path):
    def mutate(p):
        p['limits']['messages_per_hour'] = 1
        p['runtime']['media_items'] = 0
    _, store, w = setup(monkeypatch, tmp_path, mutate)
    crm, model = CRMStub(), ModelStub()
    w.crm = crm
    factory = w.configure
    w.configure = lambda snapshot: (model, factory(snapshot)[1], factory(snapshot)[2])
    store.ingest(event(), w.policy, now=100)
    w.clock = lambda: 1000
    w.sleep = lambda _: None
    finish = store.finish
    store.finish = Mock(wraps=finish)
    assert w.tick()
    assert len(crm.sent) == 1
    assert any(c['kind'] == 'handoff' for c in crm.commands)
    for command in crm.commands:
        assert {k: command[k] for k in w.snapshot.fence()} == w.snapshot.fence()
    drafts = [c.kwargs['draft'] for c in store.finish.call_args_list if c.kwargs.get('draft')]
    assert drafts[0]['configuration'] == w.snapshot.fence()
    crm.context['messages'] = [{'role': 'user', 'attachments': [{'id': 'a', 'bytes': 1}]}]
    context = w.context(dict(id='conv', version=1))
    assert context['messages'][0]['media_unavailable']


def test_media_and_audio_caps_and_pdf_pages_read_snapshot(monkeypatch, tmp_path):
    def mutate(p):
        p['runtime']['attachment_bytes'] = 3
        p['runtime']['pdf_pages'] = 1
    _, _, w = setup(monkeypatch, tmp_path, mutate)
    raw = base64.b64encode(b'toolong').decode()
    with pytest.raises(ValueError, match='midia_grande'):
        w.model.media('image/png', raw)
    with pytest.raises(ValueError, match='midia_grande'):
        w.model.transcription.transcribe('audio/ogg', b'toolong')
    # Different byte cap, same snapshot-defined one-page restriction.
    w.model.configuration['runtime']['attachment_bytes'] = 1000
    monkeypatch.setattr('vetorial_agent.adapters.PdfReader',
                        lambda _: Mock(pages=[Mock(), Mock()], is_encrypted=False))
    with pytest.raises(ValueError, match='pdf_limite'):
        w.model.media('application/pdf', base64.b64encode(b'%PDF-synthetic').decode())


def test_calendly_google_and_agenda_parameters_are_pinned(monkeypatch, tmp_path):
    def mutate(p):
        p['integrations']['booking_minutes'] = 45
        p['runtime']['agenda_horizon_days'] = 2
        p['runtime']['slot_validity_ms'] = 1000
    config, _, w = setup(monkeypatch, tmp_path, mutate)
    a = w.calendar
    assert a.minutes == 45
    assert a.timezone == config['payload']['behavior']['timezone']
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    a.clock = lambda: now
    a.availability.slots = Mock(return_value=[])
    a.candidates(now, now + timedelta(days=20))
    assert a.availability.slots.call_args.args[1] == now + timedelta(days=3)
    a.authorize = lambda *_: True
    result = a.call('conv', 'offer', 'consultar_horarios', {'periodo': 'hoje'})
    assert result['slots'] == []
    from vetorial_agent.agenda import offers
    with a.store.engine.connect() as c:
        offer = c.execute(select(offers.c.data)).scalar()
        assert offer['expires'] == now.timestamp() + 1
        assert offer['configuration'] == w.snapshot.fence()
    a.google.url = 'http://synthetic.invalid'
    post = Mock(return_value=Mock(status_code=200, content=b'{}', json=lambda: {}))
    monkeypatch.setattr('vetorial_agent.agenda.httpx.post', post)
    a.google.call('freebusy')
    body = post.call_args.kwargs['json']
    assert body['calendar_id'] == config['payload']['integrations']['calendar_id']
    assert {k: body[k] for k in w.snapshot.fence()} == w.snapshot.fence()


def test_calendly_rechecks_between_pages(monkeypatch, tmp_path):
    config, _, w = setup(monkeypatch, tmp_path)
    availability = w.calendar.availability
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    availability.now = lambda: now
    def get(*_, **kwargs):
        assert kwargs['params']['event_type'] == config['payload']['integrations']['event_type']
        change(config, w)
        return Mock(status_code=200, json=lambda: {'collection': []})
    availability.get = Mock(side_effect=get)
    with pytest.raises(ConfigurationUnavailable):
        availability.slots(now, now + timedelta(days=10))
    availability.get.assert_called_once()


def test_audio_fence_rechecked_after_media_download(monkeypatch, tmp_path):
    config, store, w = setup(monkeypatch, tmp_path)
    crm = CRMStub()
    crm.context['messages'] = [{'role': 'user', 'attachments': [{'id': 'a', 'bytes': 1}]}]
    crm.hook = lambda _: change(config, w)
    w.crm = crm
    w.model.transcription.http.post = Mock(side_effect=AssertionError('provider must not run'))
    store.ingest(event(), w.policy, now=100)
    with pytest.raises(ConfigurationUnavailable):
        w.context(dict(id='conv', version=1))
    assert crm.commands[0]['revision'] == 1
    w.model.transcription.http.post.assert_not_called()


def test_completed_receipt_survives_revision_without_repeating_effect(monkeypatch, tmp_path):
    config, store, w = setup(monkeypatch, tmp_path)
    invoke = Mock(return_value={'ok': True})
    store.operation('k', 'conv', 'typing', {'kind': 'typing', **w.authority.data()},
                    invoke, authority=w.authority)
    change(config, w)
    assert w.prepare()
    assert store.operation('k', 'conv', 'typing', {'kind': 'typing', **w.authority.data()},
                           invoke, authority=w.authority) == {'ok': True}
    invoke.assert_called_once()
    with store.engine.connect() as c:
        assert c.execute(select(operations.c.payload)).scalar()['configuration']['policy_epoch'] == 1


def test_store_analysis_attempt_limit_comes_from_snapshot(monkeypatch, tmp_path):
    def mutate(p):
        p['runtime']['max_attempts'] = 1
    _, store, w = setup(monkeypatch, tmp_path, mutate)
    store.ingest(event(), w.policy, now=100)
    store.update('conv', analysis_attempts=1)
    assert store.analyse_due(1000, silence=0, max_attempts=w.setting('runtime', 'max_attempts', 3),
                             authority=w.authority) == []


def test_agenda_quota_from_snapshot_blocks_before_calendar(monkeypatch, tmp_path):
    def mutate(p):
        p['runtime']['agenda_mutations_per_hour'] = 0
    _, store, w = setup(monkeypatch, tmp_path, mutate)
    store.ingest(event(), w.policy, now=100)
    w.crm = CRMStub()
    w.crm.context['messages'] = [
        {'role': 'user', 'content': 'Quero agendar reunião. s@example.invalid'},
        {'role': 'assistant', 'content': 'Confirmar horário da reunião?'},
        {'role': 'user', 'content': 'Sim, pode agendar'}]
    w.calendar.call = Mock(side_effect=AssertionError('quota must deny'))
    assert w.action(dict(id='conv', version=1), 'agendar',
                    {'horario': '2030-01-02T15:00:00Z', 'nome': 'Sintético',
                     'email': 's@example.invalid'})['erro'] == 'limite_de_agenda'


def test_google_delete_denied_when_epoch_changes_after_get(monkeypatch, tmp_path):
    config, _, w = setup(monkeypatch, tmp_path)
    a = w.calendar
    a.authorize = lambda *_: True
    a.google.url = 'http://synthetic.invalid'
    def post(*_, **kwargs):
        assert kwargs['json']['op'] == 'get'
        change(config, w)
        return Mock(status_code=200, content=b'{}', json=lambda: {
            'id': 'synthetic', 'status': 'confirmed',
            'extendedProperties': {'private': {'owner': 'conv'}},
            'attendees': [{'email': 's@example.invalid'}]})
    post = Mock(side_effect=post)
    monkeypatch.setattr('vetorial_agent.agenda.httpx.post', post)
    with pytest.raises(ConfigurationUnavailable):
        a.call('conv', 'key', 'cancelar', {'email': 's@example.invalid'})
    post.assert_called_once()


def test_unsupported_availability_provider_fails_closed(monkeypatch, tmp_path):
    config = envelope()
    config['payload']['integrations']['availability_provider'] = 'unsupported'
    config['sha256'] = digest(config['payload'])
    built = boot(monkeypatch, tmp_path, config)
    assert built[3].prepare() is False
    assert built[3].tick() is False
