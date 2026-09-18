"""Golden exit tables vs dung.56 — forest/maze asymmetries, traps, cage, balloon."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zorcore.content_loader import load_game_data
from zorcore.engine.game import Game
from zorcore.worldstate import WorldState


@pytest.fixture(scope="module")
def raw_world():
    root = Path(__file__).resolve().parents[1] / "zorcore" / "content"
    return json.loads((root / "world.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def cart():
    return load_game_data()


def _exit_map(room: dict) -> dict:
    """Normalize exits to comparable plain JSON (sorted keys already in asserts)."""
    return room.get("exits") or {}


def test_forest_loop_exits_match_mdl(raw_world):
    rooms = raw_world["rooms"]
    assert _exit_map(rooms["fore1"]) == {
        "north": "fore1",
        "east": "fore3",
        "south": "fore2",
        "west": "fore1",
    }
    assert _exit_map(rooms["fore2"]) == {
        "north": "shous",
        "east": "clear",
        "south": "fore4",
        "west": "fore1",
    }
    assert _exit_map(rooms["fore3"]) == {
        "north": "fore2",
        "east": "clear",
        "south": "clear",
        "west": "nhous",
    }
    assert _exit_map(rooms["fore4"]) == {
        "north": "fore5",
        "east": "cltop",
        "south": "fore4",
        "west": "fore2",
    }
    assert _exit_map(rooms["fore5"]) == {
        "north": "fore5",
        "south": "fore4",
        "west": "fore2",
        "southeast": "cltop",
    }


def test_clearing_and_grate_exits_match_mdl(raw_world):
    rooms = raw_world["rooms"]
    assert _exit_map(rooms["clear"]) == {
        "north": "clear",
        "east": "clear",
        "west": "fore3",
        "south": "fore2",
        "southwest": "ehous",
        "southeast": "fore5",
        "down": {
            "cexit": {
                "room": "mgrat",
                "flag": "key_flag",
                "fail": "You can't go that way.",
            }
        },
    }
    assert _exit_map(rooms["mgrat"]) == {
        "southwest": "maz11",
        "up": {
            "cexit": {
                "room": "clear",
                "flag": "key_flag",
                "fail": "The grating is locked",
            }
        },
    }


def test_trap_door_and_slide_exits_match_mdl(raw_world):
    rooms = raw_world["rooms"]
    assert _exit_map(rooms["lroom"]) == {
        "east": "kitch",
        "west": {
            "cexit": {
                "room": "blroo",
                "flag": "magic_flag",
                "fail": "The door is nailed shut.",
            }
        },
        "down": {
            "cexit": {
                "room": "cella",
                "flag": "trap_door",
                "fail": "You can't go that way.",
            }
        },
    }
    assert rooms["cella"]["exits"]["up"] == {
        "cexit": {
            "room": "lroom",
            "flag": "trap_door",
            "fail": "The trap door has been barred from the other side.",
        }
    }
    assert rooms["slide"]["exits"]["down"] == "cella"


def test_maze_asymmetry_sample_match_mdl(raw_world):
    rooms = raw_world["rooms"]
    assert _exit_map(rooms["maze1"]) == {
        "north": "maze1",
        "east": "maze4",
        "south": "maze2",
        "west": "mtrol",
    }
    assert _exit_map(rooms["maze2"]) == {
        "north": "maze4",
        "east": "maze3",
        "south": "maze1",
    }
    assert _exit_map(rooms["maze5"]) == {
        "north": "maze3",
        "east": "dead2",
        "southwest": "maze6",
    }
    # Intentionally non-reciprocal: maze1.east → maze4, but maze4.west → maze3
    assert rooms["maze4"]["exits"]["west"] == "maze3"
    assert rooms["maze4"]["exits"]["east"] == "dead1"


def test_river_and_dock_exits_match_mdl(raw_world):
    rooms = raw_world["rooms"]
    assert rooms["dock"]["exits"]["launch"] == "rivr1"
    assert rooms["rivr1"]["exits"]["land"] == "dock"
    assert rooms["rivr1"]["exits"]["west"] == "dock"
    assert rooms["rivr1"]["exits"]["down"] == "rivr2"


def test_caged_north_is_nexit(raw_world):
    assert raw_world["rooms"]["caged"]["exits"] == {"north": {"nexit": ""}}


def test_balloon_midair_exits_match_mdl(raw_world):
    rooms = raw_world["rooms"]
    assert rooms["vair1"]["exits"] == {}
    assert rooms["vair3"]["exits"] == {}
    assert rooms["vair2"]["exits"] == {"west": "ledg2", "land": "ledg2"}
    assert rooms["vair4"]["exits"] == {"land": "ledg4", "east": "ledg4"}


def test_carousel_spins_when_flip_false(cart, tmp_path):
    destinations = set()
    for i in range(24):
        # Deterministic but varying RNG across attempts
        seq = iter([0.01 * ((i * 7 + k) % 100) for k in range(50)])

        def rand():
            return next(seq, 0.5)

        ws = WorldState(tmp_path / f"npcs-{i}.json")
        ws.sync_content(cart.id, cart.version, cart.npcs)
        game = Game.from_game_data(cart, world_state=ws, rand=rand)
        session = game.new_session("c" * 64, "spin")
        session.room_id = "carou"
        session.flags["carousel_flip"] = False
        result = game.step(session, "north")
        assert "impossible to tell directions" in result.text.lower()
        assert session.room_id != "carou"
        destinations.add(session.room_id)

    carou_targets = {
        d.room
        for d in cart.world.rooms["carou"].exits.values()
        if d.room and d.flag == "carousel_flip"
    }
    assert destinations <= carou_targets
    assert len(destinations) >= 3


def test_carousel_static_when_flip_true(cart, tmp_path):
    ws = WorldState(tmp_path / "npcs.json")
    ws.sync_content(cart.id, cart.version, cart.npcs)
    game = Game.from_game_data(cart, world_state=ws)
    session = game.new_session("d" * 64, "fixed")
    session.room_id = "carou"
    session.flags["carousel_flip"] = True
    game.step(session, "west")
    assert session.room_id == "pass1"
