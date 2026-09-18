"""World model loaded from content world.json."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json


@dataclass
class TimerSpec:
    id: str
    seconds: float
    message: str
    death: bool = True


@dataclass
class ExitDest:
    """Resolved exit: room id, blocked message, or conditional flag exit."""

    room: str | None = None
    nexit: str | None = None
    flag: str | None = None
    fail: str | None = None

    @property
    def is_plain(self) -> bool:
        return bool(self.room) and not self.flag and not self.nexit


def parse_exit(raw: Any) -> ExitDest:
    if isinstance(raw, str):
        return ExitDest(room=raw)
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid exit value: {raw!r}")
    if "nexit" in raw:
        return ExitDest(nexit=str(raw["nexit"]))
    if "cexit" in raw:
        c = raw["cexit"]
        if isinstance(c, dict):
            return ExitDest(
                room=str(c["room"]),
                flag=str(c["flag"]),
                fail=str(c.get("fail") or "You can't go that way."),
            )
    if "room" in raw:
        return ExitDest(
            room=str(raw["room"]),
            flag=str(raw["flag"]) if raw.get("flag") else None,
            fail=str(raw["fail"]) if raw.get("fail") else None,
        )
    raise ValueError(f"Invalid exit object: {raw!r}")


@dataclass
class ObjectDef:
    id: str
    name: str
    aliases: list[str]
    description: str
    takeable: bool = False
    fixed: bool = False
    provides_light: bool = False  # when lit / always if light_always
    light_always: bool = False
    score_on_take: int = 0
    score_on_deposit: int = 0  # when put in deposit_target
    deposit_target: str = ""
    openable: bool = False
    start_open: bool = False
    container: bool = False
    capacity: int = 0
    readable: str = ""
    weapon: bool = False
    fixture: bool = False  # scenery; not listed as takeable clutter
    contains: list[str] = field(default_factory=list)


@dataclass
class RoomDef:
    id: str
    name: str
    description: str
    brief: str
    exits: dict[str, ExitDest]
    objects: list[str] = field(default_factory=list)
    dark: bool = False
    score_on_enter: int = 0
    timer_on_enter: TimerSpec | None = None
    emoji: str = ""


@dataclass
class World:
    start_room: str
    welcome: str
    help: str
    rooms: dict[str, RoomDef]
    objects: dict[str, ObjectDef]

    def find_object(self, token: str, candidates: list[str]) -> ObjectDef | None:
        token = (token or "").strip().lower()
        if not token:
            return None
        words = [w for w in token.replace("-", " ").split() if w]
        # Exact matches win over substrings, so "trap door" cannot be captured
        # by a "wooden door" that happens to be listed first.
        for oid in candidates:
            obj = self.objects.get(oid)
            if not obj:
                continue
            if token == oid or token == obj.name.lower() or token in obj.aliases:
                return obj
        best: ObjectDef | None = None
        best_score = 0
        for oid in candidates:
            obj = self.objects.get(oid)
            if not obj:
                continue
            names = {oid, obj.name.lower(), *[a.lower() for a in obj.aliases]}
            score = 0
            for alias in names:
                if not alias:
                    continue
                if token in alias or alias in token:
                    score += len(alias)
            # Prefer objects whose adjectives/synonyms cover more of the phrase
            # ("trap door" → DOOR with trap adj, not WDOOR).
            for w in words:
                if any(w == a or w in a or a in w for a in names):
                    score += 10
            if score > best_score:
                best, best_score = obj, score
        return best

    def exit_labels(self, room: RoomDef) -> list[str]:
        return sorted(room.exits.keys())


def load_world_data(data: dict[str, Any]) -> World:
    objects: dict[str, ObjectDef] = {}
    for oid, raw in data.get("objects", {}).items():
        objects[oid] = ObjectDef(
            id=oid,
            name=raw["name"],
            aliases=[a.lower() for a in raw.get("aliases", [oid])],
            description=raw["description"],
            takeable=bool(raw.get("takeable", False)),
            fixed=bool(raw.get("fixed", False)),
            provides_light=bool(raw.get("provides_light", False)),
            light_always=bool(raw.get("light_always", False)),
            score_on_take=int(raw.get("score_on_take", 0)),
            score_on_deposit=int(raw.get("score_on_deposit", 0)),
            deposit_target=str(raw.get("deposit_target", "") or ""),
            openable=bool(raw.get("openable", False)),
            start_open=bool(raw.get("start_open", False)),
            container=bool(raw.get("container", False)),
            capacity=int(raw.get("capacity", 0) or 0),
            readable=str(raw.get("readable", "") or ""),
            weapon=bool(raw.get("weapon", False)),
            fixture=bool(raw.get("fixture", False)),
            contains=list(raw.get("contains", [])),
        )
    rooms: dict[str, RoomDef] = {}
    for rid, raw in data.get("rooms", {}).items():
        timer = None
        if raw.get("timer_on_enter"):
            t = raw["timer_on_enter"]
            timer = TimerSpec(
                id=t["id"],
                seconds=float(t["seconds"]),
                message=t["message"],
                death=bool(t.get("death", True)),
            )
        exits: dict[str, ExitDest] = {}
        for direction, dest in (raw.get("exits") or {}).items():
            exits[str(direction).lower()] = parse_exit(dest)
        rooms[rid] = RoomDef(
            id=rid,
            name=raw["name"],
            description=raw["description"],
            brief=raw.get("brief", raw["name"]),
            exits=exits,
            objects=list(raw.get("objects", [])),
            dark=bool(raw.get("dark", False)),
            score_on_enter=int(raw.get("score_on_enter", 0)),
            timer_on_enter=timer,
            emoji=str(raw.get("emoji", "") or ""),
        )
    return World(
        start_room=data["start_room"],
        welcome=data.get("welcome", ""),
        help=data.get("help", ""),
        rooms=rooms,
        objects=objects,
    )


def load_world(path: Path | None = None) -> World:
    """Legacy helper: load a bare world.json (tests / fixtures)."""
    if path is None:
        path = Path(__file__).resolve().parent.parent / "content" / "world.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return load_world_data(data)


def world_to_dict(world: World) -> dict[str, Any]:
    return {
        "start_room": world.start_room,
        "rooms": list(world.rooms),
        "objects": list(world.objects),
    }
