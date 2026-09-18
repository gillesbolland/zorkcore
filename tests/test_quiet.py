"""Quiet/mesh gate: ARL required, tier rank, hysteresis, telemetry-only util."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from zorcore.config import load_settings
from zorcore.quiet import QuietMonitor, tier_allows_play


def _settings(tmp_path: Path, **overrides):
    raw = {
        "safety_enabled": True,
        "play_max_tier": "normal",
        "quiet_hold_seconds": 5,
        "quiet_poll_seconds": 30,
    }
    raw.update(overrides)
    (tmp_path / "config.json").write_text(json.dumps(raw), encoding="utf-8")
    return load_settings(tmp_path)


def _arl(tier: str = "normal", *, enabled: bool = True, **extra):
    adaptive = {"enabled": enabled, "current_tier": tier}
    adaptive.update(extra)
    return {
        "adaptive": adaptive,
        "metrics": {"adverts_per_min_ewma": 0.1, "packets_per_min_ewma": 0.5},
    }


def test_tier_allows_play_ranks():
    assert tier_allows_play("quiet", "quiet")
    assert tier_allows_play("quiet", "normal")
    assert tier_allows_play("normal", "normal")
    assert not tier_allows_play("busy", "normal")
    assert tier_allows_play("busy", "busy")
    assert not tier_allows_play("congested", "busy")
    assert not tier_allows_play("", "normal")


def test_tier_hysteresis_hold(tmp_path: Path):
    settings = _settings(tmp_path, quiet_hold_seconds=10, play_max_tier="normal")
    mon = QuietMonitor(settings)

    def fake_get(path: str):
        if path == "/api/stats":
            return {"utilization_percent": 40.0}  # util ignored for gate
        if path == "/api/advert_rate_limit_stats":
            return _arl("normal")
        return {}

    with patch.object(mon, "_get_json", side_effect=fake_get):
        with patch("zorcore.quiet.time.time", return_value=1000.0):
            s1 = mon.poll_once()
        assert s1.raw_quiet is True
        assert s1.is_quiet is False
        assert s1.adaptive_enabled is True
        assert "holding" in s1.reason

        with patch("zorcore.quiet.time.time", return_value=1011.0):
            s2 = mon.poll_once()
        assert s2.is_quiet is True


def test_busy_closes_immediately(tmp_path: Path):
    settings = _settings(tmp_path, quiet_hold_seconds=0, play_max_tier="normal")
    mon = QuietMonitor(settings)

    def quiet_get(path: str):
        if path == "/api/stats":
            return {"utilization_percent": 50.0}
        return _arl("quiet")

    def busy_get(path: str):
        if path == "/api/stats":
            return {"utilization_percent": 5.0}
        return _arl("busy")

    with patch.object(mon, "_get_json", side_effect=quiet_get):
        with patch("zorcore.quiet.time.time", return_value=1000.0):
            assert mon.poll_once().is_quiet is True

    with patch.object(mon, "_get_json", side_effect=busy_get):
        with patch("zorcore.quiet.time.time", return_value=1001.0):
            s = mon.poll_once()
        assert s.is_quiet is False
        assert s.raw_quiet is False


def test_safety_disabled_forces_open(tmp_path: Path):
    settings = _settings(tmp_path, safety_enabled=False)
    mon = QuietMonitor(settings)
    with patch.object(mon, "_get_json", side_effect=RuntimeError("no api")):
        s = mon.poll_once()
    assert s.is_quiet is True
    assert s.reason == "safety_disabled"


def test_play_max_tier_busy_allows_busy(tmp_path: Path):
    settings = _settings(tmp_path, quiet_hold_seconds=0, play_max_tier="busy")
    mon = QuietMonitor(settings)

    def busy_get(path: str):
        if path == "/api/stats":
            return {"utilization_percent": 80.0}
        return _arl("BUSY")

    with patch.object(mon, "_get_json", side_effect=busy_get):
        with patch("zorcore.quiet.time.time", return_value=50.0):
            s = mon.poll_once()
    assert s.is_quiet is True


def test_arl_disabled_fails_closed_even_with_stale_tier(tmp_path: Path):
    settings = _settings(tmp_path, quiet_hold_seconds=0, play_max_tier="busy")
    mon = QuietMonitor(settings)

    def fake_get(path: str):
        if path == "/api/stats":
            return {"utilization_percent": 1.0}
        return _arl("quiet", enabled=False)

    with patch.object(mon, "_get_json", side_effect=fake_get):
        s = mon.poll_once()
    assert s.is_quiet is False
    assert s.raw_quiet is False
    assert s.adaptive_enabled is False
    assert s.reason == "arl_disabled"
    assert s.advert_tier == ""
    rt = mon.to_runtime()
    assert rt["arl_required"] is True
    assert rt["adaptive_enabled"] is False


def test_arl_missing_adaptive_fails_closed(tmp_path: Path):
    settings = _settings(tmp_path, quiet_hold_seconds=0)
    mon = QuietMonitor(settings)

    def fake_get(path: str):
        if path == "/api/stats":
            return {}
        return {"stats": {"adverts_allowed": 1}}

    with patch.object(mon, "_get_json", side_effect=fake_get):
        s = mon.poll_once()
    assert s.is_quiet is False
    assert s.reason == "arl_unavailable"
    assert s.adaptive_enabled is None


def test_arl_api_error_fails_closed(tmp_path: Path):
    settings = _settings(tmp_path, quiet_hold_seconds=0)
    mon = QuietMonitor(settings)

    def fake_get(path: str):
        if path == "/api/stats":
            return {}
        raise ConnectionError("down")

    with patch.object(mon, "_get_json", side_effect=fake_get):
        s = mon.poll_once()
    assert s.is_quiet is False
    assert s.reason == "arl_unavailable"
    assert s.error == "ConnectionError"


def test_safety_disabled_bypasses_arl_disabled(tmp_path: Path):
    settings = _settings(tmp_path, safety_enabled=False, quiet_hold_seconds=0)
    mon = QuietMonitor(settings)

    def fake_get(path: str):
        return _arl("quiet", enabled=False)

    with patch.object(mon, "_get_json", side_effect=fake_get):
        s = mon.poll_once()
    assert s.is_quiet is True
    assert s.reason == "safety_disabled"


def test_legacy_util_keys_ignored(tmp_path: Path):
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "quiet_max_utilization_percent": 12,
                "quiet_require_advert_tier": True,
                "play_max_tier": "normal",
            }
        ),
        encoding="utf-8",
    )
    s = load_settings(tmp_path)
    assert s.play_max_tier == "normal"
    assert not hasattr(s, "quiet_max_utilization_percent")
