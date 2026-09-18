"""Access controller: queue, bans, offers, busy-local exception."""

from __future__ import annotations

import time
from pathlib import Path

from zorcore.access import (
    MSG_BANNED,
    MSG_BUSY,
    MSG_OFFER,
    MSG_RESUME,
    MSG_WAIT_TURN,
    AccessController,
)


def _eval(ctrl: AccessController, key: str, **kw):
    defaults = dict(
        safety_enabled=True,
        single_player=True,
        bans_enabled=True,
        is_quiet=True,
        queue_max=20,
        offer_timeout_seconds=900,
        path_len=None,
        local_max_hops=3,
    )
    defaults.update(kw)
    return ctrl.evaluate(key, f"name-{key[:4]}", **defaults)


def test_ban_blocks(tmp_path: Path):
    ctrl = AccessController(tmp_path / "access.json")
    ctrl.ban("aa" * 32, reason="spam", display_name="Bob")
    d = _eval(ctrl, "aa" * 32)
    assert d.allow_play is False
    assert d.reply == MSG_BANNED


def test_single_player_queue_and_messages(tmp_path: Path):
    ctrl = AccessController(tmp_path / "access.json")
    a = "11" * 32
    b = "22" * 32
    d1 = _eval(ctrl, a)
    assert d1.allow_play is True
    assert ctrl.active and ctrl.active.pubkey == a

    d2 = _eval(ctrl, b)
    assert d2.allow_play is False
    assert d2.reply == MSG_WAIT_TURN
    assert "let you know" in d2.reply
    assert len(ctrl.queue) == 1

    d3 = _eval(ctrl, b)
    assert d3.was_waiting is True


def test_busy_blocks_remote(tmp_path: Path):
    ctrl = AccessController(tmp_path / "access.json")
    ctrl.note_mesh_state(False)
    a = "33" * 32
    d = _eval(ctrl, a, is_quiet=False, path_len=5)
    assert d.allow_play is False
    assert d.reply == MSG_BUSY
    assert "resume your game" in d.reply
    assert len(ctrl.queue) == 1
    ctrl.mark_busy_ack(a, True)
    d2 = _eval(ctrl, a, is_quiet=False, path_len=5)
    assert d2.silent is True
    assert d2.reply == ""


def test_busy_notice_retry_without_ack(tmp_path: Path):
    ctrl = AccessController(tmp_path / "access.json")
    ctrl.note_mesh_state(False)
    a = "a1" * 32
    ctrl.claim(a, "A")
    d1 = _eval(ctrl, a, is_quiet=False, path_len=5)
    assert d1.reply == MSG_BUSY
    assert ctrl.active and ctrl.active.busy_tries == 1
    ctrl.mark_busy_ack(a, False)
    d2 = _eval(ctrl, a, is_quiet=False, path_len=5)
    assert d2.silent is True
    ctrl.active.busy_last_try_at = time.time() - 200
    d3 = _eval(ctrl, a, is_quiet=False, path_len=5)
    assert d3.reply == MSG_BUSY
    assert ctrl.active.busy_tries == 2
    ctrl.mark_busy_ack(a, False)
    ctrl.active.busy_last_try_at = time.time() - 200
    d4 = _eval(ctrl, a, is_quiet=False, path_len=5)
    assert d4.silent is True


def test_busy_acked_silent_across_flaps(tmp_path: Path):
    ctrl = AccessController(tmp_path / "access.json")
    a = "b2" * 32
    ctrl.claim(a, "A")
    ctrl.note_mesh_state(False)
    d1 = _eval(ctrl, a, is_quiet=False, path_len=5)
    assert d1.reply == MSG_BUSY
    ctrl.mark_busy_ack(a, True)
    for _ in range(3):
        ctrl.note_mesh_state(True)
        ctrl.note_mesh_state(False)
        d = _eval(ctrl, a, is_quiet=False, path_len=5)
        assert d.silent is True
        assert d.reply == ""


def test_resume_once_after_delivered_busy(tmp_path: Path):
    ctrl = AccessController(tmp_path / "access.json")
    a = "c3" * 32
    ctrl.claim(a, "A")
    ctrl.note_mesh_state(False)
    d = _eval(ctrl, a, is_quiet=False, path_len=5)
    assert d.reply == MSG_BUSY
    ctrl.mark_busy_ack(a, True)
    assert ctrl.active and ctrl.active.paused
    ctrl.note_mesh_state(True)
    r1 = ctrl.begin_resume_notice()
    assert r1 == MSG_RESUME
    r2 = ctrl.begin_resume_notice()
    assert r2 is None


def test_busy_local_exception_admits_le_3_hops(tmp_path: Path):
    ctrl = AccessController(tmp_path / "access.json")
    a = "44" * 32
    d = _eval(ctrl, a, is_quiet=False, path_len=2)
    assert d.allow_play is True
    assert d.local_exception is True
    assert d.claimed is True


def test_busy_blocks_flood_path(tmp_path: Path):
    ctrl = AccessController(tmp_path / "access.json")
    ctrl.note_mesh_state(False)
    a = "55" * 32
    d = _eval(ctrl, a, is_quiet=False, path_len=-1)
    assert d.allow_play is False
    assert d.reply == MSG_BUSY


def test_offer_prefers_stable_local_when_busy(tmp_path: Path):
    ctrl = AccessController(tmp_path / "access.json")
    remote = "66" * 32
    local_unstable = "77" * 32
    local_stable = "88" * 32
    ctrl.enqueue(remote, "R", 20)
    ctrl.enqueue(local_unstable, "U", 20)
    ctrl.enqueue(local_stable, "S", 20)
    ctrl.update_path_quality(remote, path_len=6)
    ctrl.update_path_quality(local_unstable, path_len=1, ack_ok=False)
    ctrl.airtime[local_unstable]["last_ack_fail_at"] = time.time()
    ctrl.airtime[local_unstable]["stable"] = False
    ctrl.update_path_quality(local_stable, path_len=2, ack_ok=True)
    offers = ctrl.maybe_offer_after_quiet(60, mesh_open=False, local_max_hops=3)
    assert offers and offers[0][0] == local_stable


def test_offer_timeout_advances(tmp_path: Path):
    ctrl = AccessController(tmp_path / "access.json")
    a = "99" * 32
    b = "ab" * 32
    ctrl.enqueue(a, "A", 20)
    ctrl.enqueue(b, "B", 20)
    offers = ctrl.maybe_offer_after_quiet(60)
    assert offers and offers[0][0] == a
    assert "within 1 minute" in offers[0][1]

    ctrl.offer_expires_at = time.time() - 1
    nxt = ctrl.expire_offer_if_needed(60)
    assert nxt and nxt[0][0] == b
    assert a not in [q.pubkey for q in ctrl.queue]


def test_claim_offer(tmp_path: Path):
    ctrl = AccessController(tmp_path / "access.json")
    a = "cd" * 32
    ctrl.enqueue(a, "A", 20)
    ctrl.maybe_offer_after_quiet(900)
    d = _eval(ctrl, a)
    assert d.allow_play is True
    assert d.claimed is True
    assert ctrl.active and ctrl.active.pubkey == a
    assert not ctrl.offer_pubkey


def test_airtime_accounting(tmp_path: Path):
    ctrl = AccessController(tmp_path / "access.json")
    key = "ef" * 32
    ctrl.record_outbound(key, 100, parts=2)
    ctrl.record_outbound(key, 50, parts=1)
    row = ctrl.airtime[key]
    assert row["bytes_out"] == 150
    assert row["parts_out"] == 3
    units = ctrl.to_runtime()["airtime"][0]["airtime_units"]
    assert units == 150 // 50


def test_offer_message_mentions_timeout():
    msg = MSG_OFFER.format(mins=15, s="s")
    assert "15 minutes" in msg


# ---------------------------------------------------- concurrent local play


def test_second_local_player_joins_instead_of_queueing(tmp_path: Path):
    """Nearby players share the dungeon; their traffic never crosses the mesh."""
    ctrl = AccessController(tmp_path / "access.json")
    alice, bob = "aa" * 32, "bb" * 32

    first = _eval(ctrl, alice, path_len=1, max_local_players=2)
    assert first.allow_play and first.claimed

    second = _eval(ctrl, bob, path_len=2, max_local_players=2)
    assert second.allow_play and second.claimed
    assert set(ctrl.actives) == {alice, bob}


def test_concurrency_is_capped_by_max_local_players(tmp_path: Path):
    ctrl = AccessController(tmp_path / "access.json")
    alice, bob, carol = "aa" * 32, "bb" * 32, "cc" * 32

    assert _eval(ctrl, alice, path_len=1, max_local_players=2).allow_play
    assert _eval(ctrl, bob, path_len=1, max_local_players=2).allow_play

    third = _eval(ctrl, carol, path_len=1, max_local_players=2)
    assert third.allow_play is False
    assert third.reply == MSG_WAIT_TURN
    assert carol not in ctrl.actives


def test_distant_player_still_waits_for_a_free_slot(tmp_path: Path):
    """A flood-path player floods the whole mesh, so they never share."""
    ctrl = AccessController(tmp_path / "access.json")
    alice, far = "aa" * 32, "bb" * 32

    assert _eval(ctrl, alice, path_len=1, max_local_players=2).allow_play

    decision = _eval(ctrl, far, path_len=-1, max_local_players=2)
    assert decision.allow_play is False
    assert decision.reply == MSG_WAIT_TURN


def test_single_slot_config_keeps_the_old_behaviour(tmp_path: Path):
    ctrl = AccessController(tmp_path / "access.json")
    alice, bob = "aa" * 32, "bb" * 32

    assert _eval(ctrl, alice, path_len=1, max_local_players=1).allow_play
    assert _eval(ctrl, bob, path_len=1, max_local_players=1).allow_play is False


def test_each_active_keeps_playing_when_another_leaves(tmp_path: Path):
    ctrl = AccessController(tmp_path / "access.json")
    alice, bob = "aa" * 32, "bb" * 32
    _eval(ctrl, alice, path_len=1, max_local_players=2)
    _eval(ctrl, bob, path_len=1, max_local_players=2)

    ctrl.clear_active(alice)
    assert set(ctrl.actives) == {bob}
    assert ctrl.is_active(bob)
    assert not ctrl.is_active(alice)


def test_actives_survive_a_reload(tmp_path: Path):
    path = tmp_path / "access.json"
    ctrl = AccessController(path)
    alice, bob = "aa" * 32, "bb" * 32
    _eval(ctrl, alice, path_len=1, max_local_players=2)
    _eval(ctrl, bob, path_len=1, max_local_players=2)

    reloaded = AccessController(path)
    assert set(reloaded.actives) == {alice, bob}


def test_runtime_lists_every_active_player(tmp_path: Path):
    ctrl = AccessController(tmp_path / "access.json")
    alice, bob = "aa" * 32, "bb" * 32
    _eval(ctrl, alice, path_len=1, max_local_players=2)
    _eval(ctrl, bob, path_len=1, max_local_players=2)

    runtime = ctrl.to_runtime()
    assert {row["pubkey"] for row in runtime["actives"]} == {alice, bob}
    assert runtime["active"] is not None
