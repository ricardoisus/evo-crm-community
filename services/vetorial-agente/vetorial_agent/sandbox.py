"""Isolated test turns. Not wired to production: CRM session authorization is required.

All amounts are integer micro-USD. Session ceilings are allocated conservatively for
an entire Sao Paulo day; closing/crashing never refunds the daily/operator envelope.
No live Store quota, CRM, transport, media downloader or calendar is injected here.
"""
import copy
import hashlib
import json
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import Table, Column, String, Integer, BigInteger, JSON, select
from .store import meta

scopes = Table('sandbox_budgets', meta,
    Column('id', String, primary_key=True),
    Column('allocated', BigInteger, nullable=False))
sessions = Table('sandbox_sessions', meta,
    Column('id', String, primary_key=True), Column('actor_id', String, nullable=False),
    Column('deployment_id', String, nullable=False), Column('revision', Integer, nullable=False),
    Column('day', String, nullable=False), Column('expires_ms', BigInteger, nullable=False),
    Column('ceiling', BigInteger, nullable=False), Column('spent_reserved', BigInteger, nullable=False),
    Column('turns', Integer, nullable=False), Column('max_turns', Integer, nullable=False),
    Column('closed', Integer, nullable=False), Column('plan', JSON, nullable=False))
calls = Table('sandbox_calls', meta,
    Column('id', String, primary_key=True), Column('session_id', String, nullable=False),
    Column('request_sha256', String, nullable=False), Column('reserved', BigInteger, nullable=False),
    Column('actual', BigInteger), Column('state', String, nullable=False), Column('result', JSON))


class SandboxLimit(Exception):
    """Safe error, never includes provider text."""


class SandboxDenied(Exception):
    pass


def amount(value, maximum):
    if type(value) is not int or not 0 < value <= maximum:
        raise ValueError('invalid_budget')
    return value


def day_at(now_ms):
    return datetime.fromtimestamp(now_ms / 1000, ZoneInfo('America/Sao_Paulo')).date().isoformat()


class SandboxBudget:
    def __init__(self, store, clock=None):
        self.store = store
        self.clock = clock or (lambda: int(time.time() * 1000))

    def create(self, *, actor_id, deployment_id, revision, purpose, criteria, sample_count,
               ceiling=50000, max_turns=5):
        # Identity/revision come from an authorized CRM session, never browser claims.
        for value in (actor_id, deployment_id, purpose, criteria):
            if not isinstance(value, str) or not 1 <= len(value.strip()) <= 1000:
                raise ValueError('invalid_plan')
        amount(revision, 2**31 - 1)
        if type(sample_count) is not int or not 3 <= sample_count <= 5:
            raise ValueError('invalid_sample')
        amount(ceiling, 500000)
        amount(max_turns, 20)
        now = self.clock()
        day = day_at(now)
        sid = str(uuid.uuid4())
        # Fixed server-generated grouping: no caller-supplied test_run_id escapes cap.
        envelopes = [(json.dumps(['vetorial', day]), 1000000),
                     (json.dumps(['vetorial', day, actor_id]), 500000)]
        with self.store.engine.begin() as c:
            for key, cap in envelopes:
                c.execute(self.store.insert(scopes).values(id=key, allocated=0).on_conflict_do_nothing())
                changed = c.execute(scopes.update().where(scopes.c.id == key,
                    scopes.c.allocated <= cap - ceiling).values(allocated=scopes.c.allocated + ceiling))
                if changed.rowcount != 1:
                    raise SandboxLimit('Limite do teste atingido')
            c.execute(sessions.insert().values(id=sid, actor_id=actor_id, deployment_id=deployment_id,
                revision=revision, day=day, expires_ms=now + 1800000, ceiling=ceiling,
                spent_reserved=0, turns=0, max_turns=max_turns, closed=0,
                plan={'purpose': purpose, 'criteria': criteria, 'sample_count': sample_count}))
        return sid

    def _session(self, c, sid, actor_id):
        row = c.execute(select(sessions).where(sessions.c.id == sid,
                         sessions.c.actor_id == actor_id)).mappings().one_or_none()
        if row is None:
            raise SandboxDenied('Sessão de teste indisponível')
        return dict(row)

    def reserve(self, sid, actor_id, request_id, text, upper_bound):
        amount(upper_bound, 500000)
        if not isinstance(request_id, str) or str(uuid.UUID(request_id)) != request_id:
            raise ValueError('invalid_request_id')
        fingerprint = hashlib.sha256(text.encode()).hexdigest()
        now = self.clock()
        with self.store.engine.begin() as c:
            # UPDATE is also the row lock on SQLite; Postgres serializes this row.
            locked = c.execute(sessions.update().where(sessions.c.id == sid,
                sessions.c.actor_id == actor_id).values(turns=sessions.c.turns))
            if locked.rowcount != 1:
                raise SandboxDenied('Sessão de teste indisponível')
            session = self._session(c, sid, actor_id)
            old = c.execute(select(calls).where(calls.c.id == request_id)).mappings().one_or_none()
            if old:
                if old['session_id'] != sid or old['request_sha256'] != fingerprint:
                    raise SandboxDenied('Requisição de teste conflitante')
                if old['state'] == 'completed':
                    return session, copy.deepcopy(old['result']), False
                raise SandboxLimit('Teste pendente de conciliação')
            if (session['closed'] or now >= session['expires_ms'] or day_at(now) != session['day'] or
                session['turns'] >= session['max_turns'] or
                session['spent_reserved'] + upper_bound > session['ceiling']):
                raise SandboxLimit('Limite do teste atingido')
            # One turn in flight per session. Unknown use stops all subsequent turns.
            pending = c.execute(select(calls.c.id).where(calls.c.session_id == sid,
                                calls.c.state != 'completed').limit(1)).first()
            if pending:
                raise SandboxLimit('Teste pendente de conciliação')
            c.execute(sessions.update().where(sessions.c.id == sid).values(
                spent_reserved=session['spent_reserved'] + upper_bound, turns=session['turns'] + 1))
            c.execute(calls.insert().values(id=request_id, session_id=sid,
                request_sha256=fingerprint, reserved=upper_bound, state='unknown'))
            return session, None, True

    def settle(self, sid, request_id, result, actual):
        # No usage means unknown; keep the committed reservation and stop, no retry.
        if type(actual) is not int or actual < 0:
            raise SandboxLimit('Consumo do teste pendente de conciliação')
        with self.store.engine.begin() as c:
            c.execute(sessions.update().where(sessions.c.id == sid).values(turns=sessions.c.turns))
            call = c.execute(select(calls).where(calls.c.id == request_id,
                             calls.c.session_id == sid)).mappings().one()
            if call['state'] == 'completed':
                if call['actual'] != actual or call['result'] != result:
                    raise SandboxDenied('Liquidação conflitante')
                return
            if actual > call['reserved']:
                # Persist closure even though the caller receives a safe exception.
                c.execute(sessions.update().where(sessions.c.id == sid).values(closed=1))
            else:
                c.execute(sessions.update().where(sessions.c.id == sid).values(
                    spent_reserved=sessions.c.spent_reserved - call['reserved'] + actual))
                c.execute(calls.update().where(calls.c.id == request_id).values(
                    state='completed', actual=actual, result=copy.deepcopy(result)))
                return
        raise SandboxLimit('Limite do teste atingido')

    def close(self, sid, actor_id):
        with self.store.engine.begin() as c:
            self._session(c, sid, actor_id)
            c.execute(sessions.update().where(sessions.c.id == sid).values(closed=1))


class NoopTransport:
    def send(self, *args, **kwargs):
        raise SandboxDenied('Transporte indisponível no teste')


class FakeCRM:
    def __init__(self):
        self.fields = {}
        self.transferred = False

    def call(self, name, args):
        if name == 'atualizar_negocio':
            self.fields.update(copy.deepcopy(args.get('campos', {})))
        elif name == 'transferir_para_ricardo':
            self.transferred = True
        else:
            raise SandboxDenied('Ação indisponível no teste')
        return {'sucesso': True, 'simulado': True}


class FakeAgenda:
    def call(self, name, args):
        if name not in {'consultar_horarios', 'agendar', 'remarcar', 'cancelar'}:
            raise SandboxDenied('Ação indisponível no teste')
        return {'sucesso': True, 'simulado': True, 'slots': []}


class SandboxAdapters:
    def __init__(self):
        self.crm = FakeCRM()
        self.agenda = FakeAgenda()
        self.transport = NoopTransport()

    def call(self, name, args):
        if name in {'atualizar_negocio', 'transferir_para_ricardo'}:
            return self.crm.call(name, args)
        return self.agenda.call(name, args)


class Sandbox:
    def __init__(self, budget, model_factory, estimate, usage):
        # These trusted callbacks must be supplied by the future CRM bridge.
        # estimate bounds the WHOLE run (all rounds/media), not a single API call.
        self.budget, self.model_factory, self.estimate, self.usage = budget, model_factory, estimate, usage

    def turn(self, sid, actor_id, request_id, text):
        if not isinstance(text, str) or not 1 <= len(text.encode()) <= 16384:
            raise ValueError('invalid_text')
        bound = self.estimate(sid, text)
        if bound is None:
            raise SandboxLimit('Não foi possível limitar o custo do teste')
        session, result, invoke = self.budget.reserve(sid, actor_id, request_id, text, bound)
        if not invoke:
            return result
        adapters = SandboxAdapters()
        try:
            model = self.model_factory(session['deployment_id'], session['revision'])
            result = model.run({'messages': [{'role': 'user', 'content': text}]}, adapters.call)
            actual = self.usage(model)
            self.budget.settle(sid, request_id, result, actual)
            # Structured output is returned; no send or live effect is available.
            return result
        except SandboxLimit:
            raise
        except Exception:
            raise SandboxLimit('Teste interrompido; consumo pendente de conciliação') from None
