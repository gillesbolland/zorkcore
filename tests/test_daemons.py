"""Shared world state, NPC daemons, respawns and world-event broadcasts."""

import pytest

from zorcore.content_loader import load_game_data
from zorcore.engine.game import Game
from zorcore.worldstate import WorldState

NPC_DEFS = {
    "troll": {"name": "troll", "home": "mtrol", "here": "A troll blocks the way.", "respawn_seconds": 100},
    "thief": {
        "name": "thief",
        "home": "treas",
        "here": "A seedy character lurks here.",
        "respawn_seconds": 100,
        "respawn_requires_empty_stash": True,
    },
}


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def state(tmp_path, clock):
    ws = WorldState(tmp_path / "npcs.json", now=clock)
    ws.sync_content("test", "1", NPC_DEFS)
    return ws


# ------------------------------------------------------------ world state


def test_npcs_start_at_home(state):
    assert state.room_of("troll") == "mtrol"
    assert state.room_of("thief") == "treas"
    assert state.is_alive("troll")


def test_state_survives_a_restart(tmp_path, clock):
    first = WorldState(tmp_path / "npcs.json", now=clock)
    first.sync_content("test", "1", NPC_DEFS)
    first.move_npc("thief", "cella")
    first.kill_npc("troll")

    second = WorldState(tmp_path / "npcs.json", now=clock)
    second.sync_content("test", "1", NPC_DEFS)
    assert second.room_of("thief") == "cella"
    assert not second.is_alive("troll")


def test_changing_content_resets_the_dungeon(tmp_path, clock):
    first = WorldState(tmp_path / "npcs.json", now=clock)
    first.sync_content("test", "1", NPC_DEFS)
    first.kill_npc("troll")

    second = WorldState(tmp_path / "npcs.json", now=clock)
    second.sync_content("test", "2", NPC_DEFS)
    assert second.is_alive("troll")
    assert second.room_of("troll") == "mtrol"


def test_dead_npc_is_nowhere(state):
    state.kill_npc("troll")
    assert state.room_of("troll") == ""
    assert state.npcs_in_room("mtrol") == []


def test_stash_tracks_who_lost_what(state):
    state.stash_add("thief", "coffi", "alice-key")
    state.stash_add("thief", "bar", "bob-key")
    assert not state.stash_empty("thief")

    released = state.stash_release("thief")
    assert {item["object"] for item in released} == {"coffi", "bar"}
    assert state.stash_empty("thief")


# --------------------------------------------------------- daemon clocks


def test_daemon_fires_only_after_its_interval(state, clock):
    daemons = [{"id": "wander", "scope": "world", "every_seconds": 60, "effects": []}]
    assert state.due_daemons(daemons, scope="world") == []  # first call schedules

    clock.advance(30)
    assert state.due_daemons(daemons, scope="world") == []

    clock.advance(40)
    assert len(state.due_daemons(daemons, scope="world")) == 1


def test_min_interval_overrides_a_greedy_daemon(state, clock):
    daemons = [{"id": "fast", "scope": "world", "every_seconds": 1, "effects": []}]
    state.due_daemons(daemons, scope="world", min_interval_seconds=120)
    clock.advance(60)
    assert state.due_daemons(daemons, scope="world", min_interval_seconds=120) == []
    clock.advance(61)
    assert len(state.due_daemons(daemons, scope="world", min_interval_seconds=120)) == 1


def test_scope_filters_daemons(state):
    daemons = [
        {"id": "w", "scope": "world", "every_seconds": 10, "effects": []},
        {"id": "p", "scope": "player", "every_seconds": 10, "effects": []},
    ]
    state.due_daemons(daemons, scope="world")
    assert set(state.daemon_next) == {"w"}


def test_world_goes_idle_and_freezes_its_clock(state, clock):
    state.note_player_activity()
    assert not state.is_idle(300)

    clock.advance(301)
    assert state.is_idle(300)


def test_returning_player_shifts_deadlines_instead_of_losing_the_gap(state, clock):
    daemons = [{"id": "wander", "scope": "world", "every_seconds": 60, "effects": []}]
    state.note_player_activity()
    state.due_daemons(daemons, scope="world")
    deadline = state.daemon_next["wander"]

    clock.advance(600)  # player walked away
    state.pause_clock()
    clock.advance(600)  # still away

    shifted = state.note_player_activity()  # player comes back
    assert shifted == pytest.approx(600)
    assert state.daemon_next["wander"] == pytest.approx(deadline + 600)
    assert state.paused_since is None


# -------------------------------------------------------------- respawns


def test_troll_respawns_after_its_cooldown(state, clock):
    game = Game(world=_tiny_world(), world_state=state, now=clock)
    state.kill_npc("troll")

    clock.advance(50)
    assert game.apply_respawns(NPC_DEFS) == []

    clock.advance(60)
    assert game.apply_respawns(NPC_DEFS) == ["troll"]
    assert state.is_alive("troll")
    assert state.room_of("troll") == "mtrol"


def test_thief_waits_until_his_stash_is_empty(state, clock):
    game = Game(world=_tiny_world(), world_state=state, now=clock)
    state.stash_add("thief", "coffi", "alice-key")
    state.kill_npc("thief")

    clock.advance(200)
    assert game.apply_respawns(NPC_DEFS) == []
    assert not state.is_alive("thief")

    state.stash_release("thief")
    assert game.apply_respawns(NPC_DEFS) == ["thief"]


# ------------------------------------------------- daemons against a game


def _tiny_world():
    from zorcore.engine.world import load_world_data

    return load_world_data(
        {
            "start_room": "mtrol",
            "rooms": {
                "mtrol": {"name": "Troll Room", "description": "A troll room.", "exits": {}},
                "treas": {"name": "Treasure Room", "description": "A hoard.", "exits": {}},
                "cella": {"name": "Cellar", "description": "A cellar.", "exits": {}},
            },
            "objects": {},
        }
    )


def test_world_daemon_moves_an_npc_and_notifies_the_room(state, clock):
    daemons = [
        {
            "id": "wander",
            "scope": "world",
            "every_seconds": 60,
            "effects": [
                {"op": "move_npc", "npc": "thief", "rooms": ["cella"]},
                {"op": "notify_room", "npc": "thief", "text": "A shadow slips in."},
            ],
        }
    ]
    game = Game(world=_tiny_world(), world_state=state, now=clock, daemons=daemons, rand=lambda: 0.0)
    game.apply_world_daemons()  # schedules
    clock.advance(61)
    broadcasts, notices = game.apply_world_daemons()

    assert state.room_of("thief") == "cella"
    assert notices == [{"room": "cella", "text": "A shadow slips in."}]
    assert broadcasts == []


def test_world_daemon_skips_a_dead_npc(state, clock):
    daemons = [
        {
            "id": "wander",
            "scope": "world",
            "every_seconds": 60,
            "when": {"npc_alive": "thief"},
            "effects": [{"op": "move_npc", "npc": "thief", "rooms": ["cella"]}],
        }
    ]
    game = Game(world=_tiny_world(), world_state=state, now=clock, daemons=daemons, rand=lambda: 0.0)
    game.apply_world_daemons()
    state.kill_npc("thief")
    clock.advance(61)
    game.apply_world_daemons()
    assert state.room_of("thief") == ""


def test_chance_gate_can_skip_a_tick(state, clock):
    daemons = [
        {
            "id": "wander",
            "scope": "world",
            "every_seconds": 60,
            "chance": 0.5,
            "effects": [{"op": "move_npc", "npc": "thief", "rooms": ["cella"]}],
        }
    ]
    game = Game(world=_tiny_world(), world_state=state, now=clock, daemons=daemons, rand=lambda: 0.9)
    game.apply_world_daemons()
    clock.advance(61)
    game.apply_world_daemons()
    assert state.room_of("thief") == "treas"


def test_thief_steals_a_treasure_the_player_carries(state, clock):
    from zorcore.engine.world import load_world_data

    world = load_world_data(
        {
            "start_room": "treas",
            "rooms": {"treas": {"name": "Hoard", "description": "A hoard.", "exits": {}}},
            "objects": {
                "coffi": {"name": "coffin", "description": "A coffin.", "takeable": True, "score_on_deposit": 7},
                "lamp": {"name": "lamp", "description": "A lamp.", "takeable": True},
            },
        }
    )
    daemons = [
        {
            "id": "steal",
            "scope": "player",
            "every_seconds": 60,
            "when": {"npc_here": "thief"},
            "effects": [{"op": "steal", "npc": "thief"}],
        }
    ]
    game = Game(world=world, world_state=state, now=clock, daemons=daemons, rand=lambda: 0.0)
    session = game.new_session("a" * 64, "alice")
    session.room_id = "treas"
    session.inventory = ["lamp", "coffi"]

    game.apply_player_daemons(session)
    clock.advance(61)
    game.apply_player_daemons(session)

    assert "coffi" not in session.inventory
    assert "lamp" in session.inventory, "only treasure is worth stealing"
    assert state.stash_of("thief") == [{"object": "coffi", "owner": "a" * 64}]


def test_player_daemon_ignores_an_npc_in_another_room(state, clock):
    daemons = [
        {
            "id": "steal",
            "scope": "player",
            "every_seconds": 60,
            "when": {"npc_here": "thief"},
            "effects": [{"op": "message", "text": "boo"}],
        }
    ]
    game = Game(world=_tiny_world(), world_state=state, now=clock, daemons=daemons, rand=lambda: 0.0)
    session = game.new_session("a" * 64, "alice")
    session.room_id = "cella"
    game.apply_player_daemons(session)
    clock.advance(61)
    assert game.apply_player_daemons(session) is None


# ------------------------------------------------- shared dungeon, on cart


def test_killing_the_troll_broadcasts_to_the_dungeon(tmp_path):
    cart = load_game_data()
    ws = WorldState(tmp_path / "npcs.json")
    ws.sync_content(cart.id, cart.version, cart.npcs)
    game = Game.from_game_data(cart, world_state=ws)

    session = game.new_session("a" * 64, "alice")
    session.room_id = "mtrol"
    session.inventory = ["sword", "lamp"]
    session.lit_objects = {"lamp"}

    result = game.step(session, "attack troll with sword")
    assert not ws.is_alive("troll")
    broadcasts, _ = game.drain_outbound()
    assert broadcasts and "troll" in broadcasts[0].lower()
    assert result.text


def test_npc_presence_is_reported_to_everyone_in_the_room(tmp_path):
    cart = load_game_data()
    ws = WorldState(tmp_path / "npcs.json")
    ws.sync_content(cart.id, cart.version, cart.npcs)
    game = Game.from_game_data(cart, world_state=ws)

    session = game.new_session("a" * 64, "alice")
    session.room_id = "mtrol"
    session.inventory = ["lamp"]
    session.lit_objects = {"lamp"}
    assert "troll" in game.step(session, "look").text.lower()

    ws.move_npc("troll", "cella")
    assert "troll" not in game.step(session, "look").text.lower()
