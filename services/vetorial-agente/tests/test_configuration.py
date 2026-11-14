import copy
import json
from pathlib import Path
import pytest
from vetorial_agent.configuration import canonical, digest, ConfigurationCache, ConfigurationUnavailable, InvalidConfiguration

FIXTURE = Path(__file__).parent / "fixtures" / "snapshot.json"


def fixture():
    return json.loads(FIXTURE.read_text())


def test_shared_canonical_fixture():
    data = fixture()
    assert digest(data["payload"]) == data["sha256"]
    assert "Olá, ação 👋\r\n" in data["payload"]["roles"]["atendimento"]["prompt"]
    with pytest.raises(InvalidConfiguration):
        canonical({"time": 0.5})


def test_cache_fails_closed_without_config():
    cache = ConfigurationCache(lambda _: {}, "synthetic")
    with pytest.raises(ConfigurationUnavailable):
        cache.pin()


def test_outage_keeps_verified_lease_but_never_renews():
    envelope = fixture()
    now = [0]
    def fetch(_):
        if now[0]:
            raise TimeoutError()
        return envelope
    cache = ConfigurationCache(fetch, envelope["payload"]["deployment_id"], lambda: now[0])
    assert cache.pin().sha256 == envelope["sha256"]
    now[0] = 119
    assert cache.pin().sha256 == envelope["sha256"]
    now[0] = 120
    with pytest.raises(ConfigurationUnavailable):
        cache.pin()


def test_snapshot_isolation_and_explicit_revocation():
    envelope = fixture()
    now = [0]
    cache = ConfigurationCache(lambda _: envelope, envelope["payload"]["deployment_id"], lambda: now[0])
    pinned = cache.pin()
    pinned.payload["roles"]["atendimento"]["prompt"] = "changed"
    assert cache.pin().payload != pinned.payload
    envelope["revoked"] = True
    cache.refresh(force=True)
    with pytest.raises(ConfigurationUnavailable):
        cache.pin()


def test_bad_hash_cannot_replace_or_renew_verified_snapshot():
    envelope = fixture()
    now = [0]
    cache = ConfigurationCache(lambda _: envelope, envelope["payload"]["deployment_id"], lambda: now[0])
    original = cache.pin()
    envelope["payload"]["roles"]["atendimento"]["prompt"] = "tampered"
    now[0] = 119
    assert cache.pin().sha256 == original.sha256
    now[0] = 120
    with pytest.raises(ConfigurationUnavailable):
        cache.pin()


def test_models_fail_closed():
    envelope = fixture()
    envelope["payload"]["roles"]["atendimento"]["model"] = "gpt-6-astra"
    envelope["sha256"] = digest(envelope["payload"])
    cache = ConfigurationCache(lambda _: envelope, envelope["payload"]["deployment_id"])
    with pytest.raises(ConfigurationUnavailable):
        cache.pin()


def test_a14_candidate_preserves_reviewed_prompt_bytes_and_contracts():
    from vetorial_agent.import_a14 import candidate, PROMPTS, SAFETY_ADDENDUM
    import ast
    import vetorial_agent.adapters as adapters
    envelope = candidate(deployment_id='synthetic', bot_id='bot', inbox_id='inbox', pipeline_id=None,
                         allowlist=['+15550000002'], internal_numbers=['+15550000001'], project_id='proj_synthetic',
                         event_type='https://api.calendly.com/event_types/00000000-0000-4000-8000-000000000004',
                         calendar_id='synthetic@example.invalid')
    payload = envelope['payload']
    for role in ('atendimento', 'analista'):
        assert payload['roles'][role]['prompt'].encode() == (PROMPTS / f'{role}.md').read_bytes()
    # Assert the imported addendum is the byte-identical literal in the legacy adapter.
    literals = [node.value for node in ast.walk(ast.parse(Path(adapters.__file__).read_text()))
                if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    assert SAFETY_ADDENDUM in literals
    assert payload['roles']['atendimento']['actions'] == adapters.CONTRACT
    assert payload['behavior']['office_hours']['enabled'] is False
    assert payload['behavior']['text_debounce_ms'] == 20000
    assert payload['behavior']['audio_debounce_ms'] == 30000
    assert payload['behavior']['analysis_silence_ms'] == 300000
    assert digest(payload) == envelope['sha256']
