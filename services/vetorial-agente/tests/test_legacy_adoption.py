import copy
from unittest.mock import Mock
import pytest
from vetorial_agent.configuration import ConfigurationCache, digest
from vetorial_agent.legacy_adoption import adopt_legacy_draft
from vetorial_agent.policy import Policy
from vetorial_agent.store import Store
from .test_crm_boot import envelope


def test_adoption_requires_identical_observed_configuration_and_idle_claim(tmp_path):
    store = Store('sqlite:///' + str(tmp_path / 'adoption.db'))
    store.migrate()
    data = envelope()
    snapshot = ConfigurationCache(lambda _: data, data['payload']['deployment_id']).pin()
    store.ingest(dict(event_id='e', conversation_id='c', message_id='m', inbox_id='inbox',
        version=1, kind='incoming', phone='+15550000002', paused=False, occurred_at=100),
        Policy('inbox', frozenset(['+15550000002'])), 100)
    draft = dict(version=1, output={'mensagens': ['Olá']})
    store.update('c', draft=draft, lease=200)
    def adopt(observed=None):
        observed = data['payload'] if observed is None else observed
        return adopt_legacy_draft(store, 'c', observed_payload=observed,
            observed_sha256=digest(observed), snapshot=snapshot, now=150)
    with pytest.raises(ValueError, match='still_claimed'):
        adopt()
    store.update('c', lease=0)
    changed = copy.deepcopy(data['payload'])
    changed['roles']['atendimento']['prompt'] += 'changed'
    with pytest.raises(ValueError, match='configuration_changed'):
        adopt(changed)
    store.operation('receipt', 'c', 'send', {}, Mock(return_value={'status': 'sent'}))
    assert adopt()
    assert store.get('c')['draft'] == dict(draft, configuration=snapshot.fence())
    assert store.result('receipt') == {'status': 'sent'}
    assert not adopt()
    store.update('c', draft=draft)
    with pytest.raises(RuntimeError):
        store.operation('unknown', 'c', 'send', {}, Mock(side_effect=RuntimeError()))
    with pytest.raises(ValueError, match='effect_unresolved'):
        adopt()
    assert store.get('c')['draft'] == draft
    store.engine.dispose()
