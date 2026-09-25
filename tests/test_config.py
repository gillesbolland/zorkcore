"""Settings validation and quiet defaults."""

import json
from pathlib import Path

import pytest

from zorcore.config import load_settings


def test_quiet_defaults(tmp_path: Path):
    s = load_settings(tmp_path)
    assert s.companion_advert_enabled is False
    assert s.companion_advert_local is False
    assert s.companion_advert_flood is False
    assert s.advert_mode == "silent"
    assert s.meshcore_port == 1977
    assert s.advert_sync_hours == 6
    assert s.advert_sync_limit == 20
    assert s.path_hash_mode == 2
    assert s.region_scope == ""
    assert s.reply_settle_ms == 3500
    assert s.command_dedupe_seconds == 20
    assert s.safety_enabled is True
    assert s.bans_enabled is True
    assert s.play_max_tier == "normal"
    assert s.quiet_hold_seconds == 120
    assert s.quiet_poll_seconds == 30
    assert s.active_grace_seconds == 1200
    assert s.single_player_enabled is True
    assert s.offer_timeout_seconds == 900
    assert s.queue_max == 20
    assert s.local_max_hops == 3
    assert s.max_local_players == 2
    assert s.active_idle_seconds == 3600
    assert s.inter_chunk_delay_ms == 800
    assert s.world_events_enabled is True
    assert s.world_events_channel_enabled is False
    assert s.world_events_channel_name == ""
    assert not hasattr(s, "lobby_enabled")
    assert not hasattr(s, "quiet_max_utilization_percent")
    assert not hasattr(s, "zork_lobby_public_key")


def test_meshcore_url(tmp_path: Path):
    (tmp_path / "config.json").write_text(
        json.dumps({"adventurer_public_key": "ab" * 32}),
        encoding="utf-8",
    )
    s = load_settings(tmp_path)
    from zorcore.content_loader import ensure_joystick_suffix

    name = ensure_joystick_suffix("Zork")
    url = s.meshcore_url(name, s.adventurer_public_key, 1)
    assert url.startswith("meshcore://contact/add?name=")
    assert "type=1" in url
    assert "public_key=" in url


def test_region_scope_strips_hash(tmp_path: Path):
    (tmp_path / "config.json").write_text(
        json.dumps({"region_scope": "#be"}),
        encoding="utf-8",
    )
    s = load_settings(tmp_path)
    assert s.region_scope == "be"


def test_autochannel_name_strips_hash(tmp_path: Path):
    (tmp_path / "config.json").write_text(
        json.dumps({"world_events_channel_name": "#zork"}),
        encoding="utf-8",
    )
    s = load_settings(tmp_path)
    assert s.world_events_channel_name == "zork"


def test_legacy_channel_index_ignored(tmp_path: Path):
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "world_events_channel_index": 3,
                "world_events_channel_name": "dungeon",
            }
        ),
        encoding="utf-8",
    )
    s = load_settings(tmp_path)
    assert s.world_events_channel_name == "dungeon"
    assert not hasattr(s, "world_events_channel_index")


def test_max_local_players_allows_eight(tmp_path: Path):
    (tmp_path / "config.json").write_text(
        json.dumps({"max_local_players": 8}),
        encoding="utf-8",
    )
    s = load_settings(tmp_path)
    assert s.max_local_players == 8


def test_max_local_players_rejects_above_sixteen(tmp_path: Path):
    (tmp_path / "config.json").write_text(
        json.dumps({"max_local_players": 17}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="max_local_players"):
        load_settings(tmp_path)


def test_active_idle_seconds_rejects_below_minimum(tmp_path: Path):
    (tmp_path / "config.json").write_text(
        json.dumps({"active_idle_seconds": 60}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="active_idle_seconds"):
        load_settings(tmp_path)
