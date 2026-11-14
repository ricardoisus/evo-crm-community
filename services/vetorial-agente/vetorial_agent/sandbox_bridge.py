"""CRM HMAC bridge, one runtime budget authority. Only offline simulation is enabled.

The provider double inherits prompt construction and output validation, but has no
HTTP client, key, media, telemetry or live adapter. Paid mode is deliberately absent.
"""
import copy
import json
from sqlalchemy import select
from .adapters import OpenAI, strict
from .sandbox import Sandbox, SandboxBudget, SandboxDenied, sessions, calls
from .pricing import CATALOG, VERSION


def sample(schema):
    if 'anyOf' in schema:
        return sample(schema['anyOf'][-1])
    if schema.get('type') == 'null':
        return None
    if 'enum' in schema:
        return schema['enum'][0]
    kind = schema.get('type')
    if isinstance(kind, list):
        return None if 'null' in kind else sample(dict(schema, type=kind[0]))
    if kind == 'object':
        return {k: sample(v) for k, v in schema.get('properties', {}).items()}
    if kind == 'array':
        return []
    if kind == 'boolean':
        return False
    if kind in {'number', 'integer'}:
        return schema.get('minimum', 0)
    return 'Simulação'


class SimulationModel(OpenAI):
    def __init__(self, snapshot):
        self.configuration = copy.deepcopy(snapshot)
        self.model = snapshot['roles']['atendimento']['model']
        self.analyst_model = snapshot['roles']['analista']['model']
        self.usage = []
        self.authority = None
        # No http member: live provider transport is unreachable.

    def post(self, path, **kwargs):
        analyst = kwargs['json']['text']['format']['name'] == 'analise'
        output = (sample(strict(self.configuration['roles']['analista']['output_schema'])) if analyst else
                  {'acao': 'responder', 'mensagens': ['Simulação sem chamada ao modelo.'], 'motivo_interno': 'Teste sintético'})
        return {'status': 'completed', 'output': [{'type': 'message', 'content': [{'text': json.dumps(output)}]}]}


class BridgeSandbox:
    def __init__(self, store, clock=None, model_factory=SimulationModel):
        self.budget = SandboxBudget(store, clock)
        self.model_factory = model_factory
        self.runner = Sandbox(self.budget, self, self.estimate, lambda model: 0)

    def session(self, sid):
        with self.budget.store.engine.connect() as c:
            row = c.execute(select(sessions).where(sessions.c.id == sid)).mappings().one_or_none()
        if row is None:
            raise SandboxDenied('Sessão indisponível')
        return dict(row)

    def for_session(self, session):
        return self.model_factory(session['plan']['snapshot'])

    def estimate(self, sid, text):
        try:
            return self._estimate(sid, text)
        except (KeyError, TypeError, ValueError, OverflowError):
            return None

    def _estimate(self, sid, text):
        session = self.session(sid)
        payload = session['plan']['snapshot']
        role = session['plan']['role']
        rates = CATALOG['openai']['models'].get(payload['roles'][role]['model'])
        if rates is None or payload['provider']['service_tier'] != 'default':
            return None
        # Text only. UTF-8 bytes bound tokens conservatively, including serialized
        # schemas/tools/context, escaping and prompt substitutions. Simulation makes
        # one request and returns no function calls; reserve the configured maximum
        # output size. No media/download or arbitrary tool result can expand it.
        size = len(json.dumps(session['plan'], ensure_ascii=True).encode()) + len(json.dumps(text).encode()) + 32768
        if size > 262144:
            return None
        rounds = payload['limits']['max_tool_rounds']
        output = payload['limits']['max_output_tokens']
        if type(rounds) is not int or not 1 <= rounds <= 8 or type(output) is not int or not 1 <= output <= 16384:
            return None
        # Reserve the full supported context, so a concurrent history update cannot
        # lower the bound. Offline provider completes once without tool calls.
        bound = max(1, (262144 * rates['input'] + output * rates['output'] + 999999) // 1000000)
        return bound if bound <= 500000 else None

    def manage(self, data):
        action = data.get('action')
        if action == 'create':
            if set(data) != {'action', 'session_id', 'actor_id', 'deployment_id', 'revision', 'purpose', 'criteria', 'sample_count', 'ceiling', 'max_turns', 'snapshot', 'role'}:
                raise ValueError('invalid_plan')
            args = dict(data)
            args.pop('action')
            sid = self.budget.create(**args)
        else:
            if set(data) != {'action', 'session_id'} or action not in {'show', 'close'}:
                raise ValueError('invalid_action')
            sid = data['session_id']
            if action == 'close':
                self.budget.close(sid, self.session(sid)['actor_id'])
        session = self.session(sid)
        with self.budget.store.engine.connect() as connection:
            ledger = connection.execute(select(calls).where(calls.c.session_id == sid)).mappings().all()
        spent = sum(row['actual'] or 0 for row in ledger if row['actual'] is not None)
        reserved = sum(row['reserved'] for row in ledger if row['actual'] is None)
        return {'session_id': sid, 'ceiling_micro_usd': session['ceiling'],
                'spent_micro_usd': spent, 'reserved_micro_usd': reserved,
                'spent_reserved_micro_usd': session['spent_reserved'], 'closed': bool(session['closed']),
                'expires_ms': session['expires_ms'], 'pricing_version': VERSION, 'mode': 'simulation'}

    def turn(self, sid, actor_id, request_id, text):
        return self.runner.turn(sid, actor_id, request_id, text)
