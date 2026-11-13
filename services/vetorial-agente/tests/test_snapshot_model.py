"""No paid/network calls: compare the exact provider request in both modes."""
import copy
import json
from datetime import datetime
from pathlib import Path

import pytest

from vetorial_agent.adapters import OpenAI, validate_action
from vetorial_agent.configuration import ConfigurationCache, digest
from vetorial_agent.import_a14 import candidate


def envelope():
    return candidate(
        deployment_id='synthetic', bot_id='bot', inbox_id='inbox', pipeline_id=None,
        allowlist=['+15550000002'], internal_numbers=['+15550000001'], project_id='proj_synthetic',
        event_type='https://api.calendly.com/event_types/00000000-0000-4000-8000-000000000004',
        calendar_id='synthetic@example.invalid',
    )


def pin(data):
    data = copy.deepcopy(data)
    data['sha256'] = digest(data['payload'])
    return ConfigurationCache(lambda _: data, 'synthetic').pin()


class FixedDate(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 12, 12, tzinfo=tz)


def capture(model, monkeypatch, analyst=False):
    import vetorial_agent.adapters as adapters
    monkeypatch.setattr(adapters, 'datetime', FixedDate)
    requests, calls = [], []
    output = {'acao': 'responder', 'mensagens': ['Olá!'], 'motivo_interno': 'acolhimento'}
    def post(path, **kwargs):
        requests.append(copy.deepcopy(kwargs['json']))
        if not analyst and len(requests) == 1:
            return {'status': 'completed', 'output': [{'type': 'function_call', 'name': 'consultar_horarios',
                    'arguments': '{"periodo":"amanhã"}', 'call_id': 'call_synthetic'}]}
        return {'status': 'completed', 'output': [{'type': 'message', 'content': [{'text': json.dumps(output)}]}]}
    monkeypatch.setattr(model, 'post', post)
    # Analyst's schema is large; returning it directly lets us compare its request
    # without inventing a commercial classification fixture.
    if analyst:
        def post_analyst(path, **kwargs):
            requests.append(copy.deepcopy(kwargs['json']))
            raise StopIteration()
        monkeypatch.setattr(model, 'post', post_analyst)
        with pytest.raises(StopIteration):
            model.run({'messages': [{'role': 'user', 'content': 'Olá'}]}, None, analyst=True)
        result = None
    else:
        def act(name, args):
            calls.append((name, args))
            return {'slots': [], 'sucesso': True}
        result = model.run({'messages': [{'role': 'user', 'content': 'Olá'}]}, act)
    return result, requests, calls


@pytest.mark.parametrize('analyst', [False, True])
def test_snapshot_matches_a14_provider_requests_and_tool_calls(monkeypatch, analyst):
    data = envelope()
    monkeypatch.setenv('OPENAI_PROJECT', 'proj_synthetic')
    legacy = OpenAI('synthetic', 'gpt-6-luna')
    managed = OpenAI('synthetic', 'ignored', snapshot=pin(data))
    try:
        assert capture(legacy, monkeypatch, analyst) == capture(managed, monkeypatch, analyst)
    finally:
        legacy.http.close()
        managed.http.close()


def test_snapshot_model_never_reads_prompt_files_or_behavior_env(monkeypatch):
    data = envelope()
    snapshot = pin(data)
    # These values must not enter any request made by a snapshot-configured adapter.
    monkeypatch.setenv('OPENAI_PROJECT', 'proj_wrong')
    monkeypatch.setenv('OPENAI_MODEL', 'gpt-6-astra')
    monkeypatch.setenv('OPENAI_ANALYST_MODEL', 'gpt-6-astra')
    model = OpenAI('synthetic', 'ignored', project='proj_wrong', snapshot=snapshot)
    def forbidden(*args, **kwargs):
        raise AssertionError('snapshot run attempted a file read')
    monkeypatch.setattr(Path, 'read_text', forbidden)
    snapshot.payload['roles']['atendimento']['prompt'] = 'mutated by another execution'
    try:
        _, requests, _ = capture(model, monkeypatch)
        assert requests[0]['model'] == 'gpt-6-luna'
        assert model.http.headers['OpenAI-Project'] == 'proj_synthetic'
        assert 'mutated by another execution' not in requests[0]['instructions']
    finally:
        model.http.close()


def test_snapshot_controls_request_limits_provider_and_tool_contract(monkeypatch):
    data = envelope()
    p = data['payload']
    p['roles']['atendimento']['model'] = 'gpt-6.1-sol'
    p['roles']['atendimento']['prompt'] = 'Prompt editado no CRM'
    p['limits'].update(max_output_tokens=700, blocks_per_reply=2, chars_per_block=80)
    p['provider'].update(reasoning='medium', service_tier='flex')
    p['roles']['atendimento']['actions']['actions']['consultar_horarios']['description'] = 'Descrição editada'
    model = OpenAI('synthetic', 'ignored', snapshot=pin(data))
    try:
        _, requests, _ = capture(model, monkeypatch)
        request = requests[0]
        assert request['model'] == 'gpt-6.1-sol'
        assert request['instructions'].startswith('Prompt editado no CRM')
        assert request['max_output_tokens'] == 700
        assert request['reasoning'] == {'effort': 'medium'}
        assert request['service_tier'] == 'flex'
        schema = request['text']['format']['schema']['properties']['mensagens']
        assert schema['maxItems'] == 2 and schema['items']['maxLength'] == 80
        assert next(t for t in request['tools'] if t['name'] == 'consultar_horarios')['description'] == 'Descrição editada'
    finally:
        model.http.close()


def test_validation_uses_supplied_contract():
    contract = envelope()['payload']['roles']['atendimento']['actions']
    contract['actions']['consultar_horarios']['parameters']['properties']['periodo'] = {'type': 'string', 'enum': ['hoje']}
    from jsonschema.exceptions import ValidationError
    with pytest.raises(ValidationError):
        validate_action('consultar_horarios', {'periodo': 'amanhã'}, contract)
    assert validate_action('consultar_horarios', {'periodo': 'hoje'}, contract) == {'periodo': 'hoje'}
