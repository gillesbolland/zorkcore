"""Verb / direction tables for the parser."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class VerbTable:
    directions: dict[str, str] = field(default_factory=dict)
    synonyms: dict[str, str] = field(default_factory=dict)
    start_phrases: frozenset[str] = field(default_factory=frozenset)
    resend_phrases: frozenset[str] = field(default_factory=frozenset)
    single_verbs: dict[str, str] = field(default_factory=dict)


DEFAULT_DIRECTIONS = {
    "n": "north",
    "s": "south",
    "e": "east",
    "w": "west",
    "u": "up",
    "d": "down",
    "ne": "northeast",
    "nw": "northwest",
    "se": "southeast",
    "sw": "southwest",
    "north": "north",
    "south": "south",
    "east": "east",
    "west": "west",
    "up": "up",
    "down": "down",
    "in": "in",
    "out": "out",
    "enter": "in",
    "exit": "out",
    "northeast": "northeast",
    "northwest": "northwest",
    "southeast": "southeast",
    "southwest": "southwest",
}

DEFAULT_SYNONYMS = {
    "get": "take",
    "grab": "take",
    "pick": "take",
    "x": "examine",
    "read": "read",
    "inspect": "examine",
    "look": "look",
    "l": "look",
    "i": "inventory",
    "inv": "inventory",
    "z": "wait",
    "move": "move",
    "push": "move",
    "pull": "move",
    "kill": "attack",
    "hit": "attack",
    "fight": "attack",
    "light": "light",
    "ignite": "light",
    "extinguish": "extinguish",
    "unlight": "extinguish",
    "turn": "turn",
    "put": "put",
    "place": "put",
    "insert": "put",
    "open": "open",
    "close": "close",
    "shut": "close",
    "drop": "drop",
    "throw": "throw",
    "give": "give",
    "walk": "go",
    "run": "go",
    "go": "go",
}

DEFAULT_START = frozenset({"new game", "new", "start", "start game", "play", "play game"})
DEFAULT_RESEND = frozenset({"hello?", "hello ?", "?", "hello"})

DEFAULT_SINGLE = {
    "help": "help",
    "score": "score",
    "inventory": "inventory",
    "i": "inventory",
    "look": "look",
    "l": "look",
    "wait": "wait",
    "z": "wait",
    "quit": "quit",
    "q": "quit",
    "bye": "quit",
    "save": "save",
    "restore": "restore",
    "brief": "brief",
    "verbose": "verbose",
    "version": "version",
}

def load_verb_table(raw: dict[str, Any] | None) -> VerbTable:
    raw = raw or {}
    directions = dict(DEFAULT_DIRECTIONS)
    directions.update({str(k).casefold(): str(v).casefold() for k, v in (raw.get("directions") or {}).items()})
    synonyms = dict(DEFAULT_SYNONYMS)
    synonyms.update({str(k).casefold(): str(v).casefold() for k, v in (raw.get("synonyms") or {}).items()})
    single = dict(DEFAULT_SINGLE)
    single.update({str(k).casefold(): str(v).casefold() for k, v in (raw.get("single_verbs") or {}).items()})
    start = frozenset(
        str(x).casefold() for x in (raw.get("start_phrases") or list(DEFAULT_START))
    )
    resend = frozenset(
        str(x).casefold() for x in (raw.get("resend_phrases") or list(DEFAULT_RESEND))
    )
    return VerbTable(
        directions=directions,
        synonyms=synonyms,
        start_phrases=start,
        resend_phrases=resend,
        single_verbs=single,
    )
