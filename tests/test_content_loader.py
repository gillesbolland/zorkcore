"""Content loader and full-dungeon playthrough smoke tests."""

from zorcore.content_loader import ensure_joystick_suffix, load_game_data
from zorcore.engine.game import Game


def _game() -> Game:
    return Game.from_game_data(load_game_data())


def test_joystick_suffix():
    assert ensure_joystick_suffix("Zork").endswith("🕹️")
    assert ensure_joystick_suffix("Zork🕹️") == "Zork🕹️"


def test_load_game_data():
    data = load_game_data()
    assert data.id == "zork-full"
    assert data.version
    assert data.companion_name.endswith("🕹️")
    assert "whous" in data.world.rooms
    assert len(data.world.rooms) >= 140


def test_new_opens_near_house():
    game = _game()
    s = game.new_session("aabb")
    assert s.room_id == "whous"
    assert s.content_id == game.content_id
    assert s.content_version == game.content_version


def test_content_mismatch_requires_new_game():
    game = _game()
    s = game.new_session("aabb")
    s.content_version = "other"
    r = game.step(s, "look")
    assert "Game data changed" in r.text
