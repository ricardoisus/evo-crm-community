"""J7 real worker/adapters and HMAC transport; all providers are offline doubles."""
import json
import uuid

import httpx
import pytest
from sqlalchemy import select
from vetorial_agent.adapters import OpenAI
from vetorial_agent.transcription import GroqTranscription
from vetorial_agent.configuration import ConfigurationCache, digest
from vetorial_agent.policy import Policy
from vetorial_agent.runtime import Worker
from vetorial_agent.store import Store
from vetorial_agent.telemetry import Telemetry, TelemetryPump, HTTPDelivery, ledger, outbox
from vetorial_agent.security import verify
from .test_snapshot_model import envelope
from .fakes import Clock, CRMStub


def setup(tmp_path, scenario):
    clock, crm = Clock(), CRMStub()
    data = envelope()
    deployment = str(uuid.uuid4())
    data['payload']['deployment_id'] = deployment
    data['sha256'] = digest(data['payload'])
    cache = ConfigurationCache(lambda _: data, deployment, clock=clock)
    snapshot = cache.pin()
    policy = Policy('inbox', frozenset(['+15550000002']), frozenset(), behavior=data['payload']['behavior'])
    store = Store('sqlite:///' + str(tmp_path / 'connected.db'))
    store.migrate()
    speech = GroqTranscription('synthetic')
    speech.http.close()
    speech.http = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={'text': 'Olá', 'duration': 1})), base_url='https://groq.invalid')
    model = OpenAI('synthetic', None, snapshot=snapshot, transcription=speech)
    model.http.close()
    calls = []
    def provider(request):
        calls.append(json.loads(request.content))
        if scenario == 'error':
            raise httpx.ReadTimeout('sensitive-provider-body')
        if scenario == 'discard':
            crm.context['version'] = 2
        output = dict(acao='responder', mensagens=['Olá!'], motivo_interno='sintético')
        return httpx.Response(200, json={'status': 'completed', 'usage': {
            'input_tokens': 100, 'input_tokens_details': {'cached_tokens': 20},
            'output_tokens': 30, 'output_tokens_details': {'reasoning_tokens': 10}},
            'output': [{'type': 'message', 'content': [{'text': json.dumps(output)}]}]})
    model.http = httpx.Client(transport=httpx.MockTransport(provider), base_url='https://openai.invalid')
    w = Worker(store, crm, model, None, policy, sleep=clock.sleep, clock=clock,
               configuration_cache=cache, configure=lambda _: (model, None, policy), telemetry=Telemetry(store))
    conversation = str(uuid.uuid4())
    crm.context['messages'] = [{'role': 'user', 'content': 'Olá', 'attachments': [{'id': 'audio', 'bytes': 9}]}]
    store.ingest(dict(event_id='incoming', inbox_id='inbox', conversation_id=conversation,
        message_id='message', version=1, paused=False, kind='incoming', phone='+15550000002',
        occurred_at=clock()), policy, clock())
    clock.now += 30
    return w, clock, calls, model, conversation


@pytest.mark.parametrize('scenario,status', [('ok', 'succeeded'), ('error', 'failed'), ('discard', 'discarded')])
def test_worker_records_usage_even_on_failure_and_discard(tmp_path, scenario, status):
    w, clock, calls, model, conversation = setup(tmp_path, scenario)
    try:
        assert w.tick()
        with w.store.engine.connect() as c:
            events = c.execute(select(ledger.c.payload)).scalars().all()
            assert len(c.execute(select(outbox)).all()) == 3
        execution = next(e for e in events if e['kind'] == 'execution')
        assert execution['status'] == status
        assert execution['conversation_id'] == conversation
        usages = [e for e in events if e['kind'] == 'usage']
        assert {e['provider'] for e in usages} == {'openai', 'groq'}
        assert all(e['execution_id'] == execution['execution_id'] for e in usages)
        assert all(e['cost_micro_usd'] is None and e['usage_state'] == 'unknown' for e in usages)
        tokens = next(e for e in usages if e['provider'] == 'openai')
        assert tokens['output_tokens'] == (0 if scenario == 'error' else 30)
        assert 'sensitive' not in json.dumps(events)
        if scenario == 'ok':
            assert execution['outcome'] == 'send_confirmed'
    finally:
        model.http.close()
        model.transcription.http.close()


def test_connected_network_loss_retries_same_events_without_duplicates(tmp_path):
    w, clock, calls, model, _ = setup(tmp_path, 'ok')
    try:
        assert w.tick()
        received, keys = {}, []
        def receiver(request):
            assert verify(request.content, request.headers, 's' * 32, path=HTTPDelivery.path)
            keys.append(request.headers['Idempotency-Key'])
            batch = json.loads(request.content)['events']
            for event in batch:
                received.setdefault(event['event_id'], event)
            if len(keys) == 1:
                raise httpx.ReadTimeout('lost ack after commit')
            return httpx.Response(200, json={'acknowledged': [e['event_id'] for e in batch]})
        client = httpx.Client(transport=httpx.MockTransport(receiver))
        pump = TelemetryPump(w, HTTPDelivery('http://crm.invalid', 's' * 32, client),
            {k: 'configured' for k in ('openai', 'groq', 'calendly', 'google_gateway')}, clock)
        assert pump.tick() == 0
        clock.now += 1
        assert pump.tick() == 4
        assert keys[0] == keys[1]
        assert len(received) == 4
        assert pump.tick() == 0
        clock.now += 14
        assert pump.tick() == 1
        heartbeats = [e for e in received.values() if e['kind'] == 'heartbeat']
        assert len(heartbeats) == 2
        assert heartbeats[1]['occurred_ms'] - heartbeats[0]['occurred_ms'] == 15000
        assert heartbeats[0]['revision'] == w.snapshot.payload['revision']
        assert heartbeats[0]['sha256'] == w.snapshot.sha256
        client.close()
    finally:
        model.http.close()
        model.transcription.http.close()


def test_heartbeat_reports_applied_snapshot_and_stops_after_lease_expiry(tmp_path):
    w, clock, calls, model, _ = setup(tmp_path, 'ok')
    try:
        assert w.prepare()
        applied = w.snapshot
        batches = []
        def deliver(batch):
            batches.extend(batch)
            return [e['event_id'] for e in batch]
        pump = TelemetryPump(w, deliver,
            {k: 'unverified' for k in ('openai', 'groq', 'calendly', 'google_gateway')}, clock)
        data = w.configuration_cache.fetch(None)
        data['payload']['revision'] += 1
        data['sha256'] = digest(data['payload'])
        w.configuration_cache.refresh(force=True)
        assert pump.tick() == 1
        assert batches[-1]['revision'] == applied.payload['revision']
        assert batches[-1]['sha256'] == applied.sha256
        # A bad snapshot cannot renew the lease or acknowledge an unapplied revision.
        data['sha256'] = '0' * 64
        clock.now = applied.expires_at + 1
        assert pump.tick() == 0
        assert len(batches) == 1
    finally:
        model.http.close()
        model.transcription.http.close()
