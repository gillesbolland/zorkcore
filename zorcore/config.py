"""Load and validate plugin settings from OPENHOP_PLUGIN_DATA/config.json."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

# MeshCore path.hash.mode: 0=1-byte, 1=2-byte, 2=3-byte
PATH_HASH_MODE_3BYTE = 2

DEFAULTS: dict[str, Any] = {
    "meshcore_host": "127.0.0.1",
    "meshcore_port": 1977,
    "companion_advert_enabled": False,
    "companion_advert_local": False,
    "companion_advert_flood": False,
    "repeater_api_base": "http://127.0.0.1:8000",
    # Prefer env OPENHOP_REPEATER_TOKEN / REPEATER_API_TOKEN; config is fallback.
    "repeater_api_token": "",
    "adventurer_public_key": "",
    "advert_sync_hours": 6,
    "advert_sync_limit": 20,
    "path_hash_mode": PATH_HASH_MODE_3BYTE,
    "region_scope": "",
    "max_chunk_bytes": 145,
    "max_chunks": 16,
    "inter_chunk_delay_ms": 800,
    "max_commands_per_minute": 20,
    "duplicate_ttl_seconds": 600,
    # Half-duplex: wait before TX so phone retries finish; collapse same-text retries.
    # Higher defaults so game TX yields to repeater forwarding.
    "reply_settle_ms": 3500,
    "command_dedupe_seconds": 20,
    "safety_enabled": True,
    "bans_enabled": True,
    # Max OpenHop advert tier allowing global play: quiet | normal | busy
    "play_max_tier": "normal",
    "quiet_hold_seconds": 120,
    "quiet_poll_seconds": 30,
    "single_player_enabled": True,
    "offer_timeout_seconds": 900,
    "queue_max": 20,
    "local_max_hops": 3,
    "max_local_players": 2,
    "daemons_enabled": True,
    "daemon_idle_pause_seconds": 300,
    "daemon_min_interval_seconds": 60,
    "world_events_enabled": True,
    "broadcast_min_interval_seconds": 30,
    "log_level": "INFO",
}


@dataclass(frozen=True)
class Settings:
    meshcore_host: str
    meshcore_port: int
    companion_advert_enabled: bool
    companion_advert_local: bool
    companion_advert_flood: bool
    repeater_api_base: str
    repeater_api_token: str
    adventurer_public_key: str
    advert_sync_hours: int
    advert_sync_limit: int
    path_hash_mode: int
    region_scope: str
    max_chunk_bytes: int
    max_chunks: int
    inter_chunk_delay_ms: int
    max_commands_per_minute: int
    duplicate_ttl_seconds: int
    reply_settle_ms: int
    command_dedupe_seconds: int
    safety_enabled: bool
    bans_enabled: bool
    play_max_tier: str
    quiet_hold_seconds: int
    quiet_poll_seconds: int
    single_player_enabled: bool
    offer_timeout_seconds: int
    queue_max: int
    local_max_hops: int
    max_local_players: int
    daemons_enabled: bool
    daemon_idle_pause_seconds: int
    daemon_min_interval_seconds: int
    world_events_enabled: bool
    broadcast_min_interval_seconds: int
    log_level: str

    @property
    def advert_mode(self) -> str:
        if not self.companion_advert_enabled:
            return "silent"
        if self.companion_advert_flood:
            return "flood"
        if self.companion_advert_local:
            return "local"
        return "silent"

    def meshcore_url(self, name: str, public_key: str, contact_type: int = 1) -> str:
        key = (public_key or "").strip().lower()
        if not key:
            return ""
        return (
            f"meshcore://contact/add?name={quote(name, safe='')}"
            f"&public_key={key}&type={contact_type}"
        )


def _require_bool(raw: Any, key: str) -> bool:
    if not isinstance(raw, bool):
        raise ValueError(f"{key} must be a boolean")
    return raw


def _require_int(raw: Any, key: str, lo: int, hi: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"{key} must be an integer from {lo} to {hi}")
    if not lo <= raw <= hi:
        raise ValueError(f"{key} must be an integer from {lo} to {hi}")
    return raw


def _require_str(raw: Any, key: str) -> str:
    if not isinstance(raw, str):
        raise ValueError(f"{key} must be a string")
    return raw


def load_settings(data_dir: Path | None = None) -> Settings:
    if data_dir is None:
        data_dir = Path(os.environ.get("OPENHOP_PLUGIN_DATA", "."))
    path = data_dir / "config.json"
    raw: dict[str, Any] = dict(DEFAULTS)
    if path.exists():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("config.json must contain an object")
        raw.update(loaded)

    host = _require_str(raw.get("meshcore_host", DEFAULTS["meshcore_host"]), "meshcore_host")
    port = _require_int(raw.get("meshcore_port", DEFAULTS["meshcore_port"]), "meshcore_port", 1, 65535)
    level = _require_str(raw.get("log_level", DEFAULTS["log_level"]), "log_level").upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise ValueError("log_level must be DEBUG, INFO, WARNING, or ERROR")

    chunk = _require_int(
        raw.get("max_chunk_bytes", DEFAULTS["max_chunk_bytes"]), "max_chunk_bytes", 40, 200
    )
    chunks = _require_int(raw.get("max_chunks", DEFAULTS["max_chunks"]), "max_chunks", 1, 20)
    delay = _require_int(
        raw.get("inter_chunk_delay_ms", DEFAULTS["inter_chunk_delay_ms"]),
        "inter_chunk_delay_ms",
        0,
        10_000,
    )
    rate = _require_int(
        raw.get("max_commands_per_minute", DEFAULTS["max_commands_per_minute"]),
        "max_commands_per_minute",
        1,
        120,
    )
    dup = _require_int(
        raw.get("duplicate_ttl_seconds", DEFAULTS["duplicate_ttl_seconds"]),
        "duplicate_ttl_seconds",
        10,
        86_400,
    )
    settle = _require_int(
        raw.get("reply_settle_ms", DEFAULTS["reply_settle_ms"]),
        "reply_settle_ms",
        0,
        10_000,
    )
    cmd_dedupe = _require_int(
        raw.get("command_dedupe_seconds", DEFAULTS["command_dedupe_seconds"]),
        "command_dedupe_seconds",
        1,
        300,
    )
    hours = _require_int(
        raw.get("advert_sync_hours", DEFAULTS["advert_sync_hours"]),
        "advert_sync_hours",
        1,
        168,
    )
    limit = _require_int(
        raw.get("advert_sync_limit", DEFAULTS["advert_sync_limit"]),
        "advert_sync_limit",
        1,
        100,
    )
    path_mode = _require_int(
        raw.get("path_hash_mode", DEFAULTS["path_hash_mode"]),
        "path_hash_mode",
        0,
        2,
    )
    region = _require_str(raw.get("region_scope", DEFAULTS["region_scope"]), "region_scope").strip()
    if region.startswith("#"):
        region = region[1:].strip()
    if len(region.encode("utf-8")) > 29:
        raise ValueError("region_scope must be at most 29 UTF-8 bytes")

    quiet_hold = _require_int(
        raw.get("quiet_hold_seconds", DEFAULTS["quiet_hold_seconds"]),
        "quiet_hold_seconds",
        0,
        3600,
    )
    quiet_poll = _require_int(
        raw.get("quiet_poll_seconds", DEFAULTS["quiet_poll_seconds"]),
        "quiet_poll_seconds",
        5,
        600,
    )
    offer_timeout = _require_int(
        raw.get("offer_timeout_seconds", DEFAULTS["offer_timeout_seconds"]),
        "offer_timeout_seconds",
        30,
        86_400,
    )
    queue_max = _require_int(
        raw.get("queue_max", DEFAULTS["queue_max"]), "queue_max", 1, 200
    )
    local_hops = _require_int(
        raw.get("local_max_hops", DEFAULTS["local_max_hops"]), "local_max_hops", 0, 64
    )
    max_local = _require_int(
        raw.get("max_local_players", DEFAULTS["max_local_players"]), "max_local_players", 1, 4
    )
    daemon_idle = _require_int(
        raw.get("daemon_idle_pause_seconds", DEFAULTS["daemon_idle_pause_seconds"]),
        "daemon_idle_pause_seconds",
        30,
        86_400,
    )
    daemon_min = _require_int(
        raw.get("daemon_min_interval_seconds", DEFAULTS["daemon_min_interval_seconds"]),
        "daemon_min_interval_seconds",
        5,
        3600,
    )
    broadcast_min = _require_int(
        raw.get("broadcast_min_interval_seconds", DEFAULTS["broadcast_min_interval_seconds"]),
        "broadcast_min_interval_seconds",
        0,
        3600,
    )
    play_max = _require_str(
        raw.get("play_max_tier", DEFAULTS["play_max_tier"]), "play_max_tier"
    ).strip().lower()
    if play_max not in {"quiet", "normal", "busy"}:
        raise ValueError("play_max_tier must be quiet, normal, or busy")

    return Settings(
        meshcore_host=host,
        meshcore_port=port,
        companion_advert_enabled=_require_bool(
            raw.get("companion_advert_enabled", False), "companion_advert_enabled"
        ),
        companion_advert_local=_require_bool(
            raw.get("companion_advert_local", False), "companion_advert_local"
        ),
        companion_advert_flood=_require_bool(
            raw.get("companion_advert_flood", False), "companion_advert_flood"
        ),
        repeater_api_base=_require_str(
            raw.get("repeater_api_base", DEFAULTS["repeater_api_base"]), "repeater_api_base"
        ).rstrip("/"),
        repeater_api_token=_require_str(
            raw.get("repeater_api_token", ""), "repeater_api_token"
        ),
        adventurer_public_key=_require_str(
            raw.get("adventurer_public_key", ""), "adventurer_public_key"
        ),
        advert_sync_hours=hours,
        advert_sync_limit=limit,
        path_hash_mode=path_mode,
        region_scope=region,
        max_chunk_bytes=chunk,
        max_chunks=chunks,
        inter_chunk_delay_ms=delay,
        max_commands_per_minute=rate,
        duplicate_ttl_seconds=dup,
        reply_settle_ms=settle,
        command_dedupe_seconds=cmd_dedupe,
        safety_enabled=_require_bool(
            raw.get("safety_enabled", DEFAULTS["safety_enabled"]), "safety_enabled"
        ),
        bans_enabled=_require_bool(
            raw.get("bans_enabled", DEFAULTS["bans_enabled"]), "bans_enabled"
        ),
        play_max_tier=play_max,
        quiet_hold_seconds=quiet_hold,
        quiet_poll_seconds=quiet_poll,
        single_player_enabled=_require_bool(
            raw.get("single_player_enabled", DEFAULTS["single_player_enabled"]),
            "single_player_enabled",
        ),
        offer_timeout_seconds=offer_timeout,
        queue_max=queue_max,
        local_max_hops=local_hops,
        max_local_players=max_local,
        daemons_enabled=_require_bool(
            raw.get("daemons_enabled", DEFAULTS["daemons_enabled"]), "daemons_enabled"
        ),
        daemon_idle_pause_seconds=daemon_idle,
        daemon_min_interval_seconds=daemon_min,
        world_events_enabled=_require_bool(
            raw.get("world_events_enabled", DEFAULTS["world_events_enabled"]),
            "world_events_enabled",
        ),
        broadcast_min_interval_seconds=broadcast_min,
        log_level=level,
    )


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
