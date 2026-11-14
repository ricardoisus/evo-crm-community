import json
import httpx
import pytest
from fastapi.testclient import TestClient
from vetorial_agent.app import create_app
from vetorial_agent.integrations import IntegrationProbes, NAMES, runtime_probes
from vetorial_agent.policy import Policy
from vetorial_agent.security import signed
from vetorial_agent.store import Store

POISON = "sk-j8-secret-canary token-j8-canary +5511987654321 https://private.invalid/auth"


def test_probes_are_get_only_cached_and_discard_provider_data():
    calls = []
    now = [0]
    def remote(request):
        calls.append(request)
        assert request.method == "GET"
        assert not request.content
        if request.url.host == "groq.invalid":
            raise httpx.ReadTimeout(POISON)
        return httpx.Response(401 if request.url.host == "calendly.invalid" else 200,
                              text=POISON, headers={"X-Secret": POISON})
    probes = IntegrationProbes({name: (f"https://{name}.invalid/status", {"Authorization": POISON})
                                for name in NAMES if name != "google_gateway"},
                               client=httpx.Client(transport=httpx.MockTransport(remote)), clock=lambda: now[0])
    expected = dict(openai="configured", groq="error", calendly="error", google_gateway="absent", evolution="configured")
    assert probes() == expected
    assert probes() == expected
    assert len(calls) == 4
    now[0] = 60
    assert probes() == expected
    assert len(calls) == 8
    assert POISON not in json.dumps(probes())


def test_runtime_status_requires_path_bound_hmac_and_never_echoes_remote_errors(tmp_path):
    store = Store("sqlite:///" + str(tmp_path / "status.db"))
    store.migrate()
    secret = "synthetic-j8-bridge-" + "x" * 32
    class Worker:
        configuration_cache = None
        integration_probes = IntegrationProbes({name: ("https://remote.invalid", {"Authorization": POISON}) for name in NAMES},
            client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500, text=POISON))))
    client = TestClient(create_app(store, Policy("inbox", frozenset(), frozenset()), secret, Worker()))
    replies = [client.get("/integrations"), client.get("/integrations", headers=signed(b"", secret)),
               client.get("/integrations", headers=signed(b"", secret, method="GET", path="/integrations")),
               client.get("/ready"), client.post("/events", content=POISON),
               client.post("/events", content=POISON, headers=signed(POISON.encode(), secret)),
               client.post("/test_sessions/invalid/turns", content=POISON), client.get("/missing")]
    assert [r.status_code for r in replies[:3]] == [401, 401, 200]
    assert replies[2].json() == {"data": {name: "error" for name in NAMES}}
    for response in replies:
        rendered = response.text + str(response.headers)
        for forbidden in POISON.split():
            assert forbidden not in rendered


@pytest.mark.parametrize("status, expected", [(200, "configured"), (204, "absent"), (503, "error"), (302, "error")])
def test_evolution_private_bridge_states(status, expected):
    probes = runtime_probes("", "", "", "", "", "https://crm.invalid", "x" * 40, "deployment")
    def remote(request):
        assert request.method == "GET"
        assert request.url.path == "/internal/vetorial_agent/integrations/deployment"
        assert "x-vetorial-signature" in request.headers
        return httpx.Response(status, text=POISON)
    probes.client = httpx.Client(transport=httpx.MockTransport(remote))
    assert probes() == {name: expected if name == "evolution" else "absent" for name in NAMES}


def test_reference_tariff_is_versioned_without_settling_unknown_calls():
    from vetorial_agent.pricing import CATALOG, VERSION
    from vetorial_agent.telemetry import sanitize
    from tests.test_telemetry import usage as usage_event
    assert VERSION == "2026-11-13.v1"
    assert CATALOG["openai"]["models"]["gpt-6-luna"]["cached"] == 10000
    assert CATALOG["groq"]["micro_usd_per_hour"] == 40000
    usage = usage_event()
    usage["pricing_version"] = VERSION
    usage.update(usage_state="unknown", cost_micro_usd=None)
    assert sanitize(usage)["cost_micro_usd"] is None
    usage["pricing_version"] = POISON
    with pytest.raises(ValueError):
        sanitize(usage)


@pytest.mark.parametrize("value", ["sk-j8-secret-canary", "token-j8-canary", "+5511987654321"])
def test_scanner_rejects_leaked_canaries(value):
    from tests.conftest import assert_safe_response
    with pytest.raises(AssertionError):
        assert_safe_response(httpx.Response(200, text=value))


def test_gateway_probe_uses_only_bounded_read_operation():
    from datetime import datetime
    probes = runtime_probes("", "", "", "https://gateway.invalid/private", "x" * 40,
                            "https://crm.invalid", "x" * 40, "deployment",
                            gateway_context=lambda: {"calendar_id": "synthetic", "revision": 1})
    def remote(request):
        if request.url.host == "gateway.invalid":
            assert request.method == "POST"
            data = json.loads(request.content)
            assert set(data) == {"op", "timeMin", "timeMax", "calendar_id", "revision"}
            assert data["op"] == "list"
            assert (datetime.fromisoformat(data["timeMax"]) - datetime.fromisoformat(data["timeMin"])).total_seconds() == 60
            assert "x-vetorial-calendar-key" in request.headers
        else:
            assert request.method == "GET"
        return httpx.Response(200, text=POISON)
    probes.client = httpx.Client(transport=httpx.MockTransport(remote))
    assert probes()["google_gateway"] == "configured"


def test_probes_feed_durable_heartbeat_without_provider_payload(tmp_path):
    from .test_connected_telemetry import setup
    from vetorial_agent.telemetry import TelemetryPump
    worker, clock, _, model, _ = setup(tmp_path, "ok")
    try:
        assert worker.prepare()
        probes = IntegrationProbes({name: ("https://remote.invalid", {}) for name in NAMES},
            client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, text=POISON))))
        batches = []
        def deliver(batch):
            batches.extend(batch)
            return [event["event_id"] for event in batch]
        assert TelemetryPump(worker, deliver, probes, clock).tick() == 1
        assert batches[0]["integrations"] == {name: "configured" for name in NAMES}
        assert not any(value in json.dumps(batches) for value in POISON.split())
    finally:
        model.http.close()
        model.transcription.http.close()
