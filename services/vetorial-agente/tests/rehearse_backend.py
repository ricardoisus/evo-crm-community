"""J12 companion to the Rails rehearsal; loopback only, no paid provider.

Run through vetorial_agent_rehearsal_spec.rb, not against a deployed service.
Control routes exist only in that test process, never in either application.
"""
import json
import os
import socket
import sys
import threading
import time
import uuid
from pathlib import Path

import httpx
import uvicorn
from sqlalchemy import select, delete
from vetorial_agent.adapters import CRM, OpenAI
from vetorial_agent.app import create_app
from vetorial_agent.configuration import ConfigurationCache
from vetorial_agent.import_a14 import candidate
from vetorial_agent.legacy_adoption import adopt_legacy_draft
from vetorial_agent.policy import Policy
from vetorial_agent.runtime import Worker
from vetorial_agent.store import Store, conversations, events, operations
from vetorial_agent.telemetry import Telemetry, TelemetryPump, HTTPDelivery, ledger, outbox


def run(path):
    data = json.loads(Path(path).read_text())
    base, secret = data['base'], data['secret']
    assert base.startswith('http://127.0.0.1:')
    assert os.environ['POSTGRES_TEST_URL'].endswith('/vetorial_agent_a12_test')
    control_client = httpx.Client(base_url=base, timeout=30)
    def control(action, **body):
        r = control_client.post('/__j12/' + action, json=body)
        r.raise_for_status()
        return r.json()
    config = candidate(deployment_id='synthetic', bot_id=data['bot'], inbox_id=data['inbox'],
        pipeline_id=None, allowlist=['+15550000002'], internal_numbers=['+15550000001'],
        project_id='proj_synthetic', event_type='https://api.calendly.com/event_types/00000000-0000-4000-8000-000000000004',
        calendar_id='synthetic@example.invalid')
    deployment = control('import', payload=config['payload'])['deployment']
    crm = CRM(base, secret)
    cache = ConfigurationCache(crm.configuration, deployment)
    store = Store(os.environ['POSTGRES_TEST_URL'])
    store.migrate()
    policy = Policy(data['inbox'], frozenset(['+15550000002']))
    calls = []
    def configure(snapshot):
        model = OpenAI('synthetic', None, snapshot=snapshot)
        model.http.close()
        def provider(request):
            calls.append(json.loads(request.content))
            output = dict(acao='responder', mensagens=['Olá!'], motivo_interno='sintético')
            return httpx.Response(200, json={'status': 'completed', 'usage': {
                'input_tokens': 100, 'output_tokens': 30}, 'output': [
                {'type': 'message', 'content': [{'text': json.dumps(output)}]}]})
        model.http = httpx.Client(transport=httpx.MockTransport(provider), base_url='https://offline.invalid')
        models.append(model)
        p = snapshot.payload
        return model, None, Policy(data['inbox'], frozenset(p['activation']['allowlist']),
            frozenset(p['activation']['internal_numbers']),
            attendance_enabled=p['activation']['atendimento'] != 'off', behavior=p['behavior'])
    models = []
    worker = Worker(store, crm, None, None, policy, configuration_cache=cache,
        configure=configure, telemetry=Telemetry(store), sleep=lambda _: None)
    assert worker.prepare()
    # A real HTTP ingress, with no background worker race; tick is driven explicitly.
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    ingress = create_app(store, policy, secret)
    server = uvicorn.Server(uvicorn.Config(ingress, log_level='error', lifespan='off'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(.01)
    runtime = f'http://127.0.0.1:{port}'
    pump = TelemetryPump(worker, HTTPDelivery(base, secret), {name: 'configured' for name in ('openai', 'groq', 'calendly', 'google_gateway', 'evolution')})
    try:
        assert control('compatible')['compatible']
        assert control('publish')['revision'] == 2
        cache.refresh(force=True)
        assert worker.prepare()
        assert pump.tick() == 1
        assert control('metrics')['profile']['health'] == 'healthy'
        event = control('incoming', runtime=runtime)['event']
        store.update(data['conversation'], due=time.time() - 1)
        # First tick persists the draft and pending send; Sidekiq owns transport.
        assert worker.tick()
        deadline = time.monotonic() + 10
        while control('metrics')['commands'][-1] != 'sent':
            assert time.monotonic() < deadline, 'Sidekiq did not confirm send'
            time.sleep(.05)
        store.update(data['conversation'], lease=0)
        assert worker.tick()
        assert store.get(data['conversation'])['done'] == event['version']
        pump.tick()
        view = control('metrics')
        assert view['metrics']['responses'] == 1, view
        assert view['metrics']['usage_unknown'] == 1, view
        assert view['messages'] == 1 and len(calls) == 1
        assert control('pause')['paused']
        assert not control('resume')['paused']
        assert control('off')['off']
        cache.refresh(force=True)
        assert worker.prepare() and not worker.policy.allows('+15550000002')
        # Rehearse A14 -> compatible -> CRM with an already accepted legacy block.
        assert control('legacy')['legacy']
        cache.refresh(force=True)
        assert worker.prepare()
        observed = cache.pin()
        legacy_model, _, legacy_policy = configure(observed)
        legacy = Worker(store, crm, legacy_model, None, legacy_policy, sleep=lambda _: None)
        event = control('incoming', runtime=runtime)['event']
        store.update(data['conversation'], due=time.time() - 1, lease=0)
        assert legacy.tick()
        deadline = time.monotonic() + 10
        while control('metrics')['commands'][-1] != 'sent':
            assert time.monotonic() < deadline, 'legacy Sidekiq receipt missing'
            time.sleep(.05)
        assert 'configuration' not in store.get(data['conversation'])['draft']
        # During start-first overlap the new worker must not steal the old lease.
        store.update(data['conversation'], lease=time.time() + 30)
        assert worker.tick() is False
        assert control('promote')['promoted']
        store.update(data['conversation'], lease=0)  # graceful old worker release
        assert adopt_legacy_draft(store, data['conversation'], observed_payload=observed.payload,
            observed_sha256=observed.sha256, snapshot=cache.pin())
        calls_before_adoption = len(calls)
        assert worker.tick()
        assert len(calls) == calls_before_adoption
        assert store.get(data['conversation'])['done'] == event['version']
        assert control('metrics')['messages'] == 2
        # Rollback must keep queued work, draft and accepted receipts byte-for-byte.
        queued_id = str(uuid.uuid4())
        queued_event = dict(event, event_id=str(uuid.uuid4()), conversation_id=queued_id,
                            message_id=str(uuid.uuid4()))
        assert store.ingest(queued_event, legacy_policy)
        queued_before = store.get(queued_id)
        before = store.get(data['conversation'])
        with store.engine.connect() as c:
            receipts = c.execute(select(operations).where(operations.c.conversation_id == data['conversation'])).mappings().all()
        with store.engine.connect() as c:
            queued = c.execute(select(events).where(events.c.payload['conversation_id'].as_string() == data['conversation'])).mappings().all()
        started = time.monotonic()
        restored = control('rollback')
        assert restored['restored'] and restored['native_active'] is False
        # The restored A14 protocol reads the same durable receipt, with no fence.
        receipt = crm.command(data['conversation'], event['version'],
            f"{data['conversation']}:{event['version']}:block:0", 'send', text='Olá!')
        assert receipt['status'] == 'sent'
        assert crm.request(data['conversation'])['allowed'] is True
        assert control('metrics')['messages'] == 2
        assert len(calls) == calls_before_adoption
        restarted = Store(os.environ['POSTGRES_TEST_URL'])
        after = restarted.get(data['conversation'])
        restarted.engine.dispose()
        assert after == before
        assert store.get(queued_id) == queued_before
        assert queued_before['done'] < queued_before['version']
        with store.engine.connect() as c:
            assert c.execute(select(events).where(events.c.payload['conversation_id'].as_string() == data['conversation'])).mappings().all() == queued
            assert c.execute(select(operations).where(operations.c.conversation_id == data['conversation'])).mappings().all() == receipts
        print(json.dumps({'http': 'green', 'sidekiq': 'green', 'model_calls': len(calls),
                          'rollback_ms': round((time.monotonic() - started) * 1000),
                          'draft_preserved': before['draft'] is not None, 'queue_preserved': True, 'receipts': len(receipts)}))
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()
        for model in models:
            model.http.close()
        crm.http.close()
        control_client.close()
        with store.engine.begin() as c:
            if 'queued_id' in locals():
                c.execute(delete(events).where(events.c.payload['conversation_id'].as_string() == queued_id))
                c.execute(delete(conversations).where(conversations.c.id == queued_id))
            c.execute(delete(outbox).where(outbox.c.event_id.in_(select(ledger.c.event_id).where(ledger.c.payload['deployment_id'].as_string() == deployment))))
            c.execute(delete(ledger).where(ledger.c.payload['deployment_id'].as_string() == deployment))
            c.execute(delete(operations).where(operations.c.conversation_id == data['conversation']))
            c.execute(delete(events).where(events.c.payload['conversation_id'].as_string() == data['conversation']))
            c.execute(delete(conversations).where(conversations.c.id == data['conversation']))
        store.engine.dispose()


if __name__ == '__main__':
    run(sys.argv[1])
