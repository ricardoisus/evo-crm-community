import pytest
from evals.run import identity_disclosed
from vetorial_agent.adapters import OpenAI


@pytest.mark.parametrize("text", [
    "Sou um assistente de IA da Vetorial, não uma pessoa.",
    "Sou uma inteligência artificial.", "Sou um bot.", "Como assistente virtual, posso ajudar.",
])
def test_identity_regression_is_detected(text):
    assert identity_disclosed([text])


@pytest.mark.parametrize("text", [
    "Estou aqui pra entender seu caso e ver como posso ajudar.",
    "Sou da Vetorial. Como posso ajudar?", "Bom dia, João.",
])
def test_identity_check_allows_regular_messages(text):
    assert not identity_disclosed([text])


def test_openai_requires_explicit_project(monkeypatch):
    monkeypatch.delenv("OPENAI_PROJECT", raising=False)
    with pytest.raises(ValueError, match="OPENAI_PROJECT"):
        OpenAI("synthetic", "synthetic")
    monkeypatch.setenv("OPENAI_PROJECT", "proj_synthetic")
    client = OpenAI("synthetic", "synthetic")
    assert client.http.headers["OpenAI-Project"] == "proj_synthetic"
    client.http.close()


@pytest.mark.parametrize("provider_code,expected", [
    ("insufficient_quota", "openai_status_429_insufficient_quota"),
    ("rate_limit_exceeded", "openai_status_429_rate_limit_exceeded"),
    ("secret-do-not-print", "openai_status_429"),
])
def test_openai_errors_expose_only_approved_status_codes(provider_code, expected):
    import httpx
    client = OpenAI("synthetic", "synthetic", project="proj_synthetic")
    client.http = httpx.Client(
        base_url="https://api.openai.com/v1",
        transport=httpx.MockTransport(lambda request: httpx.Response(
            429, json={"error": {"code": provider_code, "message": "secret-do-not-print"}}
        )),
    )
    with pytest.raises(RuntimeError) as exc:
        client.post("/responses", json={})
    assert str(exc.value) == expected
    client.http.close()


def test_summary_with_only_rejected_api_calls_has_no_fake_latency(tmp_path, monkeypatch):
    import json
    from evals.run import main
    row = {
        "scenario": "C01", "status": "FALHA_AUTOMATICA",
        "latency_seconds": [1.0], "usage": [],
        "turns": [{"analyst": False, "error": "RuntimeError"}],
    }
    (tmp_path / "gpt-6.1-sol-01.json").write_text(json.dumps(row))
    monkeypatch.setattr("sys.argv", [
        "evals.run", "--output", str(tmp_path), "--media-dir", str(tmp_path),
        "--models", "gpt-6.1-sol", "--scenarios", "1",
    ])
    main()
    summary = json.loads((tmp_path / "summary.json").read_text())["gpt-6.1-sol"]
    assert summary["first_block_estimate_p50"] is None
    assert summary["execution_inconclusive"] == ["C01"]
    assert summary["behavioral_failures"] == []
    assert summary["passed"] == 0
