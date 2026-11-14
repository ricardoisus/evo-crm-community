"""Explicit rollout operation, never startup fallback.

The operator supplies the reviewed effective A14 payload after draining old workers.
Only idle, version-matching drafts are stamped; queues/receipts remain untouched.
"""
import copy
import time

from sqlalchemy import select
from .configuration import digest, validate
from .store import conversations, operations


def adopt_legacy_draft(store, conversation_id, *, observed_payload, observed_sha256, snapshot, now=None):
    validate(observed_payload)
    if digest(observed_payload) != observed_sha256:
        raise ValueError('legacy_hash_mismatch')
    def behavior(payload):
        return {k: v for k, v in payload.items() if k not in ('revision', 'policy_epoch')}
    if behavior(observed_payload) != behavior(snapshot.payload):
        raise ValueError('legacy_configuration_changed')
    now = time.time() if now is None else now
    with store.engine.begin() as c:
        row = c.execute(select(conversations).where(conversations.c.id == conversation_id)
                        .with_for_update()).mappings().one()
        if row['lease'] > now or row['analysis_lease'] > now:
            raise ValueError('legacy_worker_still_claimed')
        draft = row['draft']
        if not draft or draft.get('version') != row['version']:
            raise ValueError('legacy_draft_version_mismatch')
        if draft.get('configuration') == snapshot.fence():
            return False
        if draft.get('configuration') is not None:
            raise ValueError('not_a_legacy_draft')
        # Unknown effects must be reconciled, never unlocked by migration.
        pending = c.execute(select(operations.c.key).where(
            operations.c.conversation_id == conversation_id, operations.c.result.is_(None))).first()
        if pending:
            raise ValueError('legacy_effect_unresolved')
        draft = copy.deepcopy(draft)
        draft['configuration'] = snapshot.fence()
        c.execute(conversations.update().where(conversations.c.id == conversation_id).values(draft=draft))
        return True
