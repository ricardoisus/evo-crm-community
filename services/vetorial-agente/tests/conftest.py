"""Sweep every runtime HTTP response in the test suite, including error responses."""
import re
import pytest
from starlette.testclient import TestClient


def assert_safe_response(response):
    rendered = response.text + str(response.headers)
    assert not any(value in rendered for value in (
        "sk-j8-secret-canary", "token-j8-canary", "5511987654321", "https://private.invalid/auth"))
    assert not re.search(r"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{16,}|gsk_[A-Za-z0-9]{16,}|Bearer\s+[A-Za-z0-9._-]{16,})", rendered), "Provider token in runtime response"
    assert not re.search(r"\+\d{11,15}(?!\d)", rendered), "Full phone in runtime response"


@pytest.fixture(autouse=True)
def scan_all_runtime_responses(monkeypatch):
    original = TestClient.send
    def scanned(self, *args, **kwargs):
        response = original(self, *args, **kwargs)
        assert_safe_response(response)
        return response
    monkeypatch.setattr(TestClient, "send", scanned)
