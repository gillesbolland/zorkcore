"""Exits tip lists only currently open directions; climb/launch are parser-reachable."""

from zorcore.content_loader import load_game_data
from zorcore.engine.game import Game
from zorcore.engine.world import ExitDest
from zorcore.worldstate import WorldState


def _full_game(tmp_path) -> Game:
    cart = load_game_data()
    ws = WorldState(tmp_path / "npcs.json")
    ws.sync_content(cart.id, cart.version, cart.npcs)
    return Game.from_game_data(cart, world_state=ws)


def test_whous_omits_locked_east_nexit(tmp_path):
    game = _full_game(tmp_path)
    session = game.new_session("a" * 64, "tester")
    room = game.world.rooms["whous"]
    labels = game.traversable_exit_labels(session, room)
    assert "east" not in labels
    assert "north" in labels
    assert "west" in labels or "south" in labels


def test_magne_lists_no_exits_while_locked(tmp_path):
    game = _full_game(tmp_path)
    session = game.new_session("b" * 64, "tester")
    session.room_id = "magne"
    room = game.world.rooms["magne"]
    assert game.traversable_exit_labels(session, room) == []
    look = game.step(session, "look")
    assert "Exits:" not in (look.ui or "")


def test_climb_from_cltop_reaches_clmid(tmp_path):
    game = _full_game(tmp_path)
    session = game.new_session("c" * 64, "tester")
    session.room_id = "cltop"
    session.inventory = ["lamp"]
    session.flags["lamp_on"] = True
    session.visited = ["cltop"]
    result = game.step(session, "climb")
    assert session.room_id == "clmid", result.text


def test_launch_from_dock_is_understood(tmp_path):
    game = _full_game(tmp_path)
    session = game.new_session("d" * 64, "tester")
    session.room_id = "dock"
    session.visited = ["dock"]
    before = session.room_id
    result = game.step(session, "launch")
    assert "don't understand" not in result.text.lower()
    assert session.room_id != before or "can't" in result.text.lower()
    # Prefer actual movement when the river exit is open
    dest = game.world.rooms["dock"].exits.get("launch")
    if dest and not dest.nexit and not dest.flag:
        assert session.room_id == dest.room


def test_empty_nexit_returns_fallback_message():
    game = Game.from_game_data(load_game_data())
    session = game.new_session("e" * 64)
    room_id, err = game._resolve_exit(session, ExitDest(nexit=""))
    assert room_id is None
    assert err == "You can't go that way."
