"""Fixed adventure content loader (zorcore/content/)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zorcore.engine.verbs import VerbTable, load_verb_table
from zorcore.engine.world import World, load_world_data

JOYSTICK = "🕹️"
CONTENT_SCHEMA = 1


@dataclass(frozen=True)
class DarkPolicy:
    message: str = "It is pitch black. You are likely to be eaten by a grue."
    timer_id: str = "dark"
    timer_seconds: float = 45.0
    timer_message: str = "Oh no! A lurking grue gobbles you up."
    timer_death: bool = True
    timer_hint: str = "Something stirs in the dark…"


@dataclass(frozen=True)
class ContentMeta:
    id: str
    name: str
    version: str
    companion_name: str
    description: str = ""


@dataclass
class GameData:
    meta: ContentMeta
    world: World
    verbs: VerbTable
    scripts: list[dict[str, Any]] = field(default_factory=list)
    dark: DarkPolicy = field(default_factory=DarkPolicy)
    initial_flags: dict[str, bool] = field(default_factory=dict)
    root: Path | None = None
    daemons: list[dict[str, Any]] = field(default_factory=list)
    npcs: dict[str, dict[str, Any]] = field(default_factory=dict)
    system_messages: dict[str, str] = field(default_factory=dict)

    @property
    def companion_name(self) -> str:
        return self.meta.companion_name

    @property
    def id(self) -> str:
        return self.meta.id

    @property
    def version(self) -> str:
        return self.meta.version


def package_content_root() -> Path:
    return Path(__file__).resolve().parent / "content"


def ensure_joystick_suffix(name: str) -> str:
    name = (name or "").strip()
    if not name:
        name = "Zork"
    if not name.endswith(JOYSTICK):
        while name.endswith(JOYSTICK):
            name = name[: -len(JOYSTICK)].rstrip()
        name = f"{name}{JOYSTICK}"
    return name


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# Overridable radio/safety strings from meta.json.
SYSTEM_MESSAGE_KEYS = (
    "busy",
    "paused",
    "resume",
    "wait_turn",
    "offer",
    "banned",
    "queue_full",
)


def _validated_system_messages(raw: Any) -> dict[str, str]:
    """Keep only known keys; drop an `offer` override that loses {mins}/{s}."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key in SYSTEM_MESSAGE_KEYS:
        val = raw.get(key)
        if not isinstance(val, str) or not val.strip():
            continue
        if key == "offer":
            try:
                probe = val.format(mins=2, s="s")
            except (KeyError, IndexError, ValueError):
                continue
            if "2" not in probe:
                continue
        out[key] = val.strip()
    return out


def load_game_data() -> GameData:
    """Load the baked-in adventure under zorcore/content/."""
    root = package_content_root()
    meta_path = root / "meta.json"
    if not meta_path.is_file():
        raise FileNotFoundError(f"Missing game content meta at {meta_path}")
    meta_raw = _read_json(meta_path)
    schema = int(meta_raw.get("schema", CONTENT_SCHEMA))
    if schema != CONTENT_SCHEMA:
        raise ValueError(f"Unsupported content schema {schema}")

    content_id = str(meta_raw.get("id", "zork-full"))
    companion = ensure_joystick_suffix(
        str(meta_raw.get("companion_name", meta_raw.get("name", content_id)))
    )
    meta = ContentMeta(
        id=content_id,
        name=str(meta_raw.get("name", content_id)),
        version=str(meta_raw.get("version", "0.1.0")),
        companion_name=companion,
        description=str(meta_raw.get("description", "")),
    )

    world_path = root / "world.json"
    world_data = _read_json(world_path)
    for key in ("start_room", "welcome", "help"):
        if key in meta_raw and meta_raw[key]:
            world_data[key] = meta_raw[key]
    world = load_world_data(world_data)

    verbs_path = root / "verbs.json"
    verbs = load_verb_table(_read_json(verbs_path) if verbs_path.exists() else {})
    scripts: list[dict[str, Any]] = []
    daemons: list[dict[str, Any]] = []
    scripts_path = root / "scripts.json"
    if scripts_path.exists():
        sraw = _read_json(scripts_path)
        if isinstance(sraw, list):
            scripts = sraw
        elif isinstance(sraw, dict):
            scripts = list(sraw.get("scripts", []))
            daemons = list(sraw.get("daemons", []))

    npcs = {
        str(k): dict(v)
        for k, v in (meta_raw.get("npcs") or {}).items()
        if isinstance(v, dict)
    }
    system_messages = _validated_system_messages(meta_raw.get("system_messages"))

    dark_raw = meta_raw.get("dark") or world_data.get("dark") or {}
    dark = DarkPolicy(
        message=str(dark_raw.get("message", DarkPolicy.message)),
        timer_id=str(dark_raw.get("timer_id", DarkPolicy.timer_id)),
        timer_seconds=float(dark_raw.get("timer_seconds", DarkPolicy.timer_seconds)),
        timer_message=str(dark_raw.get("timer_message", DarkPolicy.timer_message)),
        timer_death=bool(dark_raw.get("timer_death", True)),
        timer_hint=str(dark_raw.get("timer_hint", DarkPolicy.timer_hint)),
    )
    flags = {
        str(k): bool(v)
        for k, v in (meta_raw.get("flags") or world_data.get("flags") or {}).items()
    }

    return GameData(
        meta=meta,
        world=world,
        verbs=verbs,
        scripts=scripts,
        dark=dark,
        initial_flags=flags,
        root=root,
        daemons=daemons,
        npcs=npcs,
        system_messages=system_messages,
    )
