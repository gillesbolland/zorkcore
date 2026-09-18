"""Game engine package — parser, world, sessions, and step loop."""

from zorcore.engine.parser import ParsedCommand, parse
from zorcore.engine.sessions import Session, SessionStore
from zorcore.engine.world import World, load_world

# Game imported lazily by callers via zorcore.engine.game to avoid import cycles.

__all__ = [
    "ParsedCommand",
    "Session",
    "SessionStore",
    "World",
    "load_world",
    "parse",
]


def __getattr__(name: str):
    if name in {"Game", "StepResult"}:
        from zorcore.engine import game as _game

        return getattr(_game, name)
    raise AttributeError(name)
