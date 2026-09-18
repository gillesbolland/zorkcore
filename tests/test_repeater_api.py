"""Repeater API auth helpers."""

from zorcore.repeater_api import api_headers


def test_api_headers_prefer_env(monkeypatch):
    class S:
        repeater_api_token = "from-config"

    monkeypatch.setenv("OPENHOP_REPEATER_TOKEN", "from-env")
    h = api_headers(S())  # type: ignore[arg-type]
    assert h.get("Authorization") == "Bearer from-env" or "from-env" in str(h.values())


def test_api_headers_fallback_config(monkeypatch):
    class S:
        repeater_api_token = "cfg-token"

    monkeypatch.delenv("OPENHOP_REPEATER_TOKEN", raising=False)
    monkeypatch.delenv("REPEATER_API_TOKEN", raising=False)
    h = api_headers(S())  # type: ignore[arg-type]
    assert "cfg-token" in str(h.values())
