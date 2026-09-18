"""Content lint + walkthrough for the full Zork adventure."""

import json
from pathlib import Path

import pytest

from zorcore.content_loader import load_game_data
from zorcore.engine.game import Game
from zorcore.text import format_reply, utf8_len
from zorcore.worldstate import WorldState

MAX_BYTES = 145
MAX_CHUNKS = 16


@pytest.fixture(scope="module")
def cart():
    return load_game_data()


@pytest.fixture(scope="module")
def raw_world():
    root = Path(__file__).resolve().parents[1] / "zorcore" / "content"
    return json.loads((root / "world.json").read_text(encoding="utf-8"))


def _game(cart, tmp_path, rand=None) -> tuple[Game, WorldState]:
    ws = WorldState(tmp_path / "npcs.json")
    ws.sync_content(cart.id, cart.version, cart.npcs)
    return Game.from_game_data(cart, world_state=ws, rand=rand), ws


# --------------------------------------------------------------- structure


def test_content_loads_with_expected_size(cart):
    assert len(cart.world.rooms) >= 140
    assert len(cart.world.objects) >= 150
    assert cart.world.start_room in cart.world.rooms


def test_every_exit_points_at_a_real_room(raw_world):
    rooms = set(raw_world["rooms"])
    bad = []
    for rid, room in raw_world["rooms"].items():
        for direction, dest in (room.get("exits") or {}).items():
            if isinstance(dest, str):
                if dest not in rooms:
                    bad.append((rid, direction, dest))
            elif isinstance(dest, dict) and dest.get("cexit"):
                target = dest["cexit"].get("room")
                if target and target not in rooms:
                    bad.append((rid, direction, target))
    assert bad == []


def test_every_room_object_exists(raw_world):
    objects = set(raw_world["objects"])
    bad = [
        (rid, oid)
        for rid, room in raw_world["rooms"].items()
        for oid in (room.get("objects") or [])
        if oid not in objects
    ]
    assert bad == []


def test_all_rooms_reachable_from_start(raw_world, cart):
    """Ignoring flags, every room must be connected to the start.

    Vehicles (bucket, balloon, cage, shrinking cake) move the player through
    `teleport` scripts rather than exits, so those count as edges too.
    """
    rooms = raw_world["rooms"]
    adjacency = {rid: set() for rid in rooms}
    for rid, room in rooms.items():
        for dest in (room.get("exits") or {}).values():
            target = dest if isinstance(dest, str) else (dest.get("cexit") or {}).get("room")
            if target in rooms:
                adjacency[rid].add(target)
    for script in cart.scripts:
        source = (script.get("when") or {}).get("room")
        if not source:
            continue
        for eff in script.get("effects") or []:
            if eff.get("op") == "teleport" and eff.get("room") in rooms:
                adjacency[source].add(eff["room"])

    # Balloon mid-air travel is MDL-scripted (NULEXIT rooms); do not invent
    # static exits on vair*, but count the classic ascent/landing chain here.
    for src, dst in (
        ("vair1", "vair2"),
        ("vair2", "vair3"),
        ("vair3", "vair4"),
        ("vair2", "ledg2"),
        ("vair4", "ledg4"),
    ):
        if src in adjacency and dst in rooms:
            adjacency[src].add(dst)

    seen = {raw_world.get("start_room", "whous")}
    frontier = list(seen)
    while frontier:
        rid = frontier.pop()
        for target in adjacency.get(rid, ()):
            if target not in seen:
                seen.add(target)
                frontier.append(target)
    assert sorted(set(rooms) - seen) == []


# ------------------------------------------------------------------ prose


def test_room_prose_fits_within_max_chunks(cart):
    """Original MDL prose may span many radio messages; never truncate past max_chunks."""
    over = []
    for rid, room in cart.world.rooms.items():
        for label, text in (("desc", room.description), ("brief", room.brief)):
            if not text:
                continue
            parts = format_reply(text, "", MAX_BYTES, MAX_CHUNKS)
            if len(parts) > MAX_CHUNKS:
                over.append((rid, label, len(parts)))
            assert all(utf8_len(p) <= MAX_BYTES for p in parts), rid
    assert over == []


def test_object_prose_fits_within_max_chunks(cart):
    over = []
    for oid, obj in cart.world.objects.items():
        if not obj.description:
            continue
        parts = format_reply(obj.description, "", MAX_BYTES, MAX_CHUNKS)
        if len(parts) > MAX_CHUNKS:
            over.append((oid, len(parts)))
        assert all(utf8_len(p) <= MAX_BYTES for p in parts), oid
    assert over == []


def test_long_looks_still_deliver_within_chunk_ceiling(cart):
    """Long rooms (machine room, etc.) must fit in the raised multipart budget."""
    long_rooms = [
        rid
        for rid, room in cart.world.rooms.items()
        if utf8_len(room.description or "") > MAX_BYTES * 2
    ]
    assert long_rooms, "expected some multi-part room looks after OG prose restore"
    for rid in long_rooms:
        parts = format_reply(cart.world.rooms[rid].description, "", MAX_BYTES, MAX_CHUNKS)
        assert 1 < len(parts) <= MAX_CHUNKS, rid


def test_no_room_is_left_without_prose(cart):
    blank = [rid for rid, r in cart.world.rooms.items() if not (r.description or "").strip()]
    assert blank == []


def test_every_room_has_a_scene_emoji(cart):
    missing = [rid for rid, r in cart.world.rooms.items() if not r.emoji]
    assert missing == []


# ----------------------------------------------------------------- scoring


def test_treasures_all_deposit_into_the_trophy_case(raw_world):
    treasures = {
        oid: o for oid, o in raw_world["objects"].items() if o.get("score_on_deposit")
    }
    assert len(treasures) >= 15
    targets = {o.get("deposit_target") for o in treasures.values()}
    assert targets == {"tcase"}


def test_declared_flags_cover_every_conditional_exit(raw_world, cart):
    declared = set(cart.initial_flags)
    used = set()
    for room in raw_world["rooms"].values():
        for dest in (room.get("exits") or {}).values():
            if isinstance(dest, dict) and dest.get("cexit"):
                flag = dest["cexit"].get("flag")
                if flag:
                    used.add(flag)
    assert used - declared == set()


def test_script_and_daemon_references_resolve(cart, raw_world):
    rooms, objects = set(raw_world["rooms"]), set(raw_world["objects"])
    bad = []

    def check_when(when):
        for key in ("npc_here", "npc_alive", "npc_dead"):
            val = when.get(key)
            if isinstance(val, str) and val not in objects:
                bad.append((key, val))
        if when.get("room") and when["room"] not in rooms:
            bad.append(("room", when["room"]))
        for key in ("has", "missing"):
            val = when.get(key)
            if not val:
                continue
            for oid in [val] if isinstance(val, str) else val:
                if oid not in objects:
                    bad.append((key, oid))

    for script in cart.scripts:
        for key in ("object", "indirect"):
            if script.get(key) and script[key] not in objects:
                bad.append((key, script[key]))
        check_when(script.get("when") or {})
        for eff in script.get("effects") or []:
            for key in ("object", "npc"):
                if eff.get(key) and eff[key] not in objects:
                    bad.append((key, eff[key]))
            if eff.get("room") and eff["room"] not in rooms:
                bad.append(("effect.room", eff["room"]))

    for daemon in cart.daemons:
        check_when(daemon.get("when") or {})
        for eff in daemon.get("effects") or []:
            if eff.get("npc") and eff["npc"] not in objects:
                bad.append(("daemon.npc", eff["npc"]))
            for rid in eff.get("rooms") or []:
                if rid not in rooms:
                    bad.append(("daemon.room", rid))
    assert bad == []


# ------------------------------------------------------------- radio voice


def test_system_messages_fit_one_radio_message(cart):
    assert cart.system_messages, "zork-full should speak in its own voice"
    for key, text in cart.system_messages.items():
        parts = format_reply(text, "", MAX_BYTES, MAX_CHUNKS)
        assert len(parts) == 1, f"{key} needs {len(parts)} parts"


def test_offer_message_keeps_its_placeholders(cart):
    offer = cart.system_messages.get("offer")
    assert offer and "{mins}" in offer


def test_world_event_broadcasts_fit_one_message(cart):
    texts = [
        eff["text"]
        for script in cart.scripts
        for eff in (script.get("effects") or [])
        if eff.get("op") in {"broadcast", "notify_room"}
    ]
    assert texts, "shared dungeon should announce something"
    for text in texts:
        assert len(format_reply(text, "", MAX_BYTES, MAX_CHUNKS)) == 1, text


# ------------------------------------------------------------- walkthrough


def test_walkthrough_reaches_the_cellar_and_scores(cart, tmp_path):
    game, _ = _game(cart, tmp_path)
    session = game.new_session("a" * 64, "tester")
    for cmd in [
        "n", "e", "open window", "in", "w",
        "take lamp", "take sword", "move rug", "open trap door",
        "light lamp", "d",
    ]:
        game.step(session, cmd)
    assert session.room_id == "cella"
    assert session.alive
    assert session.score >= 35


def test_killing_the_troll_opens_the_way_and_broadcasts(cart, tmp_path):
    game, world_state = _game(cart, tmp_path)
    session = game.new_session("b" * 64, "tester")
    for cmd in [
        "n", "e", "open window", "in", "w",
        "take lamp", "take sword", "move rug", "open trap door",
        "light lamp", "d", "e",
    ]:
        game.step(session, cmd)
    assert session.room_id == "mtrol"
    assert world_state.is_alive("troll")

    blocked = game.step(session, "s")
    assert session.room_id == "mtrol", blocked.text

    result = game.step(session, "attack troll with sword")
    assert "troll" in result.text.lower()
    assert not world_state.is_alive("troll")

    broadcasts, _ = game.drain_outbound()
    assert any("troll" in b.lower() for b in broadcasts)

    game.step(session, "s")
    assert session.room_id == "maze1"


def test_two_players_share_one_troll(cart, tmp_path):
    """The dungeon is shared: one player's kill is seen by the other."""
    game, world_state = _game(cart, tmp_path)
    walk = [
        "n", "e", "open window", "in", "w",
        "take lamp", "take sword", "move rug", "open trap door",
        "light lamp", "d", "e",
    ]
    alice = game.new_session("a" * 64, "alice")
    bob = game.new_session("b" * 64, "bob")
    for cmd in walk:
        game.step(alice, cmd)
        game.step(bob, cmd)
    assert alice.room_id == bob.room_id == "mtrol"

    assert "troll" in game.step(bob, "look").text.lower()
    game.step(alice, "attack troll with sword")
    assert not world_state.is_alive("troll")

    seen_by_bob = game.step(bob, "look").text.lower()
    assert "troll" not in seen_by_bob
