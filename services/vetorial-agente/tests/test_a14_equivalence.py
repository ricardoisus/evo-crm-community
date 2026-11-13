"""Offline A14 review and six full synthetic conversations; provider is a double."""
import copy
import json

import pytest

from vetorial_agent.adapters import OpenAI
from vetorial_agent.configuration import ConfigurationCache, digest
from vetorial_agent.import_a14 import import_review
from vetorial_agent.policy import Policy
from vetorial_agent.runtime import Worker
from vetorial_agent.store import Store
from vetorial_agent.calendar import TestCalendar
from .fakes import Clock, CRMStub
from .test_snapshot_model import FixedDate

BINDING = dict(agent_bot_id='62f2af76-ed7f-4118-8d8f-ac11e12fa73c',
               inbox_ids=['702de859-a676-4af9-a2a1-806da82785d8'],
               pipeline_id='86abea7e-e571-4060-91df-26d93f788170')


def test_review_checks_identity_pipeline_hash_and_original_bytes():
    review = import_review(expected_binding=BINDING)
    assert import_review(expected_binding=BINDING, expected_sha256=review['sha256']) == review
    assert review['sha256'] == digest(review['payload'])
    from vetorial_agent.import_a14 import PROMPTS
    for role in ('atendimento', 'analista'):
        assert review['payload']['roles'][role]['prompt'].encode() == (PROMPTS / f'{role}.md').read_bytes()
    for key in BINDING:
        wrong = dict(BINDING, **{key: 'wrong'})
        with pytest.raises(ValueError, match='Identidade'):
            import_review(expected_binding=wrong)
    with pytest.raises(ValueError, match='Hash'):
        import_review(expected_binding=BINDING, expected_sha256='0' * 64)


@pytest.mark.parametrize('scenario', ['texto', 'audio', 'agenda', 'pausa', 'fora_lista', 'transferencia'])
def test_six_conversations_match_legacy_and_crm(monkeypatch, tmp_path, scenario):
    import vetorial_agent.adapters as adapters
    monkeypatch.setattr(adapters, 'datetime', FixedDate)
    review = import_review(expected_binding=BINDING)
    p = review['payload']
    monkeypatch.setenv('OPENAI_PROJECT', p['provider']['project_id'])
    traces = []
    for managed in (False, True):
        clock, crm = Clock(), CRMStub()
        store = Store('sqlite:///' + str(tmp_path / f'{managed}.db'))
        store.migrate()
        cache = ConfigurationCache(lambda _: review, p['deployment_id'], clock=clock)
        snapshot = cache.pin()
        policy = Policy(BINDING['inbox_ids'][0], frozenset(p['activation']['allowlist']),
                        frozenset(p['activation']['internal_numbers']),
                        behavior=p['behavior'] if managed else {})
        model = OpenAI('synthetic', 'gpt-6-luna', snapshot=snapshot if managed else None)
        requests, media, tool_calls = [], [], []
        def post(path, **kwargs):
            requests.append(copy.deepcopy(kwargs['json']))
            if scenario in ('agenda', 'transferencia') and len(requests) == 1:
                name = 'consultar_horarios' if scenario == 'agenda' else 'transferir_para_ricardo'
                args = {'periodo': 'amanhã'} if scenario == 'agenda' else {'motivo': 'Pedido de humano'}
                tool_calls.append((name, args))
                return {'status': 'completed', 'output': [dict(type='function_call', name=name,
                        arguments=json.dumps(args), call_id='synthetic_call')]}
            output = dict(acao='responder', mensagens=['Olá!'], motivo_interno='teste sintético')
            return {'status': 'completed', 'output': [dict(type='message', content=[dict(text=json.dumps(output))])]}
        monkeypatch.setattr(model, 'post', post)
        def transcribe(mime, data):
            media.append((mime, data))
            return 'Sou médico e quero conversar'
        monkeypatch.setattr(model, 'media', transcribe)
        calendar = TestCalendar(['2026-11-18T17:00:00Z'])
        w = Worker(store, crm, model, calendar, policy, sleep=clock.sleep, clock=clock,
                   configuration_cache=cache if managed else None,
                   configure=lambda _: (model, calendar, policy))
        assert w.prepare()
        crm.context['messages'] = [dict(role='user', content='Quero conversar sobre aquisição')]
        if scenario == 'audio':
            crm.context['messages'][0].update(content='', attachments=[dict(id='audio', bytes=30)])
        phone = '+15550000003' if scenario == 'fora_lista' else p['activation']['allowlist'][0]
        event = dict(event_id='e1', inbox_id=policy.inbox_id, conversation_id='synthetic',
                     message_id='m1', version=1, paused=False, kind='incoming', phone=phone,
                     audio=scenario == 'audio', occurred_at=clock())
        store.ingest(event, policy, clock())
        if scenario == 'pausa':
            store.ingest(dict(event, event_id='e2', message_id='m2', version=2, kind='human', paused=True), policy, clock())
            crm.context.update(version=2, paused=True)
        clock.now += 30
        decision = w.tick()
        # Only the CRM protocol fence and revision-scoped media cache key differ;
        # these are transport metadata, never model/tool/business input.
        commands = copy.deepcopy(crm.commands)
        for command in commands:
            if managed:
                assert {key: command[key] for key in snapshot.fence()} == snapshot.fence()
            for key in ('revision', 'policy_epoch', 'sha256'):
                command.pop(key, None)
            if command['kind'] == 'media' and managed:
                command['key'] = command['key'].replace(':' + review['sha256'], '')
        traces.append((decision, requests, media, tool_calls, commands, crm.sent))
        model.http.close()
        store.engine.dispose()
    assert traces[0] == traces[1]
    if scenario in ('pausa', 'fora_lista'):
        assert not traces[0][0] and not traces[0][1] and not traces[0][-1]
    else:
        assert traces[0][0] and traces[0][1] and traces[0][-1]
    if scenario == 'audio':
        assert traces[0][2]
    if scenario in ('agenda', 'transferencia'):
        assert traces[0][3]
