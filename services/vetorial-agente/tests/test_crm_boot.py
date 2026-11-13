import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from vetorial_agent import app
from vetorial_agent.configuration import digest
from vetorial_agent.import_a14 import candidate
from vetorial_agent.security import signed
from vetorial_agent.store import Store


def envelope():
    return candidate(deployment_id='synthetic', bot_id='bot', inbox_id='inbox', pipeline_id=None,
                     allowlist=['+15550000002'], internal_numbers=[], project_id='proj_synthetic',
                     event_type='https://api.calendly.com/event_types/00000000-0000-4000-8000-000000000004',
                     calendar_id='synthetic@example.invalid')


def boot(monkeypatch, tmp_path, config):
    store = Store('sqlite:///' + str(tmp_path / 'queue.db'))
    store.migrate()
    monkeypatch.setenv('VETORIAL_AGENT_CONTROL_SOURCE', 'crm')
    monkeypatch.setenv('VETORIAL_AGENT_DEPLOYMENT_ID', 'synthetic')
    monkeypatch.setenv('CRM_URL', 'http://synthetic.invalid')
    monkeypatch.setattr(app, 'secret', lambda name: 'postgresql://synthetic' if name == 'DATABASE_URL' else 's' * 40)
    monkeypatch.setattr(app, 'Store', lambda _: store)
    monkeypatch.setattr(app.CRM, 'configuration', lambda self, _: config)
    return app.build()


def test_crm_boot_http_and_factory_ignore_behavior_env_and_files(monkeypatch, tmp_path):
    config = envelope()
    built = boot(monkeypatch, tmp_path, config)
    worker = built[3]
    original = worker.model.configuration
    for key in ('OPENAI_MODEL', 'OPENAI_ANALYST_MODEL', 'OPENAI_PROJECT',
                'VETORIAL_AGENT_INBOX_ID', 'VETORIAL_AGENT_ALLOWLIST',
                'VETORIAL_AGENT_INTERNAL_NUMBERS', 'VETORIAL_AGENT_ALL_CONTACTS',
                'VETORIAL_AGENT_ANALYSIS_ALL', 'CALENDLY_EVENT_TYPE', 'CALENDAR_GATEWAY_URL'):
        monkeypatch.setenv(key, 'poison')
    def no_file(*args, **kwargs):
        raise AssertionError('Behavior file read in CRM mode')
    monkeypatch.setattr(Path, 'read_text', no_file)
    monkeypatch.setattr(Path, 'read_bytes', no_file)
    rebuilt = app.build()
    assert rebuilt[3].model.configuration == original
    assert worker.prepare()
    assert worker.policy.inbox_id == 'inbox'
    assert worker.policy.allows('+15550000002')
    assert not worker.policy.allows('+15550000003')
    config['payload']['behavior']['text_debounce_ms'] = 7000
    config['payload']['revision'] = 2
    config['sha256'] = digest(config['payload'])
    worker.configuration_cache.refresh(force=True)
    event = dict(event_id='evt', inbox_id='inbox', conversation_id='conv', message_id='msg',
                 version=1, paused=False, kind='incoming', phone='+15550000002', occurred_at=1)
    body = json.dumps(event).encode()
    client = TestClient(app.create_app(*built))
    assert client.post('/events', content=body, headers=signed(body, built[2])).status_code == 202
    row = built[0].get('conv')
    assert row['due'] - row['last_event'] == 7
    assert worker.prepare()
    assert worker.model.configuration['revision'] == 2


def test_missing_snapshot_keeps_queues_and_http_retries(monkeypatch, tmp_path):
    built = boot(monkeypatch, tmp_path, {})
    store, _, secret, worker = built
    assert worker.tick() is False
    assert worker.analyse() is False
    monkeypatch.setattr(store, 'claim', lambda *_: pytest.fail('queue claimed'))
    assert worker.tick() is False
    client = TestClient(app.create_app(*built))
    assert client.get('/ready').status_code == 503
    event = dict(event_id='evt', inbox_id='inbox', conversation_id='conv', message_id='msg',
                 version=1, paused=False, kind='incoming', phone='+15550000002', occurred_at=1)
    body = json.dumps(event).encode()
    assert client.post('/events', content=body, headers=signed(body, secret)).status_code == 503
    assert store.get('conv') is None


def test_legacy_selection_and_invalid_source(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(app, 'build_legacy', lambda: sentinel)
    monkeypatch.delenv('VETORIAL_AGENT_CONTROL_SOURCE', raising=False)
    assert app.build() is sentinel
    monkeypatch.setenv('VETORIAL_AGENT_CONTROL_SOURCE', 'legacy_transition')
    assert app.build() is sentinel
    monkeypatch.setenv('VETORIAL_AGENT_CONTROL_SOURCE', 'both')
    with pytest.raises(RuntimeError):
        app.build()
