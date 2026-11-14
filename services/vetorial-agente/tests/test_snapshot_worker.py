"""A configuration outage must not consume claims or invoke a legacy adapter."""
from unittest.mock import Mock

import pytest

from vetorial_agent.configuration import ConfigurationCache
from vetorial_agent.runtime import Worker, Stale
from .test_snapshot_model import envelope


def worker(cache, configure):
    store, crm, legacy_model, legacy_calendar, legacy_policy = (Mock() for _ in range(5))
    return Worker(store, crm, legacy_model, legacy_calendar, legacy_policy,
                  configuration_cache=cache, configure=configure)


def test_unavailable_configuration_preserves_both_queues():
    configure = Mock()
    w = worker(ConfigurationCache(lambda _: {}, 'synthetic'), configure)
    assert w.tick() is False
    assert w.analyse() is False
    w.store.claim.assert_not_called()
    w.store.analyse_due.assert_not_called()
    w.model.run.assert_not_called()
    w.crm.command.assert_not_called()
    configure.assert_not_called()


def test_snapshot_is_pinned_before_claim_and_rechecked_before_effects():
    data = envelope()
    now = [0]
    cache = ConfigurationCache(lambda _: data, 'synthetic', clock=lambda: now[0])
    adapters = tuple(Mock() for _ in range(3))
    configure = Mock(return_value=adapters)
    w = worker(cache, configure)
    w.store.claim.return_value = None
    assert w.tick() is False
    configure.assert_called_once()
    assert configure.call_args.args[0].sha256 == data['sha256']
    assert (w.model, w.calendar, w.policy) == adapters
    now[0] = 10
    data['payload']['revision'] += 1
    from vetorial_agent.configuration import digest
    data['sha256'] = digest(data['payload'])
    with pytest.raises(Stale):
        w.guard('synthetic', 1)
    w.crm.request.assert_not_called()


def test_legacy_worker_never_uses_snapshot_factory():
    configure = Mock(side_effect=AssertionError('legacy must not call CRM config'))
    w = worker(None, configure)
    w.store.claim.return_value = None
    assert w.tick() is False
    configure.assert_not_called()
    assert w.snapshot is None
