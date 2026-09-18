"""Single-player queue, bans, turn offers, airtime, and busy-local admission."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from zorcore.contact_sync import normalize_pubkey

logger = logging.getLogger(__name__)

MSG_BUSY = (
    "The mesh is busy — play is paused for radio safety. "
    "I will let you know when you can resume your game."
)
MSG_WAIT_TURN = (
    "Someone is already playing. You are in the queue. "
    "I will let you know when you can resume your game."
)
MSG_BANNED = "You cannot play right now."
MSG_OFFER = (
    "Your turn — reply within {mins} minute{s} with any command "
    "(or `new game` to start). I will pass the slot on if you do not answer."
)
MSG_RESUME = "The channel is quiet again — you can resume. Send a command when ready."
MSG_QUEUE_FULL = "The wait queue is full. Try again later."

# Path quality: recent ACK failures or path resets mark unstable.
STABILITY_FAIL_WINDOW_SECONDS = 600.0
STABILITY_RESET_WINDOW_SECONDS = 900.0
DEFAULT_LOCAL_MAX_HOPS = 3
BUSY_NOTICE_MAX_TRIES = 2
BUSY_NOTICE_RETRY_SECONDS = 120.0


def is_local_path(path_len: int | None, local_max_hops: int = DEFAULT_LOCAL_MAX_HOPS) -> bool:
    if path_len is None:
        return False
    try:
        n = int(path_len)
    except (TypeError, ValueError):
        return False
    # MeshCore uses -1 / 255 for flood / unknown
    if n < 0 or n >= 255:
        return False
    return 0 <= n <= local_max_hops


@dataclass
class QueueEntry:
    pubkey: str
    display_name: str = ""
    enqueued_at: float = field(default_factory=time.time)
    notified_waiting: bool = False
    busy_attempt_episode: int = 0
    busy_notified_episode: int = 0
    busy_tries: int = 0
    busy_last_try_at: float = 0.0


@dataclass
class ActiveSlot:
    pubkey: str
    display_name: str = ""
    since: float = field(default_factory=time.time)
    paused: bool = False
    busy_attempt_episode: int = 0
    busy_notified_episode: int = 0
    busy_tries: int = 0
    busy_last_try_at: float = 0.0
    busy_delivered: bool = False
    resume_notified_episode: int = 0


@dataclass
class AccessDecision:
    """Result of evaluating an inbound player message."""

    allow_play: bool = False
    reply: str = ""
    enqueue: bool = False
    claimed: bool = False
    was_waiting: bool = False
    local_exception: bool = False
    silent: bool = False


def _slot_from_dict(act: dict[str, Any], closed_episode: int) -> ActiveSlot:
    busy_ep = int(act.get("busy_notified_episode") or 0)
    if not busy_ep and bool(act.get("pause_notified")):
        busy_ep = max(1, closed_episode)
    delivered = bool(act.get("busy_delivered", bool(act.get("pause_notified"))))
    resume_ep = int(act.get("resume_notified_episode") or 0)
    if not resume_ep and bool(act.get("resume_notified")):
        resume_ep = busy_ep
    return ActiveSlot(
        pubkey=normalize_pubkey(act["pubkey"]),
        display_name=str(act.get("display_name") or ""),
        since=float(act.get("since") or time.time()),
        paused=bool(act.get("paused")),
        busy_attempt_episode=int(act.get("busy_attempt_episode") or busy_ep or 0),
        busy_notified_episode=busy_ep,
        busy_tries=int(act.get("busy_tries") or (1 if busy_ep else 0)),
        busy_last_try_at=float(act.get("busy_last_try_at") or 0.0),
        busy_delivered=delivered,
        resume_notified_episode=resume_ep,
    )


class AccessController:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.actives: dict[str, ActiveSlot] = {}
        self.queue: list[QueueEntry] = []
        self.bans: dict[str, dict[str, Any]] = {}
        self.offer_pubkey: str = ""
        self.offer_expires_at: float = 0.0
        self.airtime: dict[str, dict[str, Any]] = {}
        self.closed_episode: int = 0
        self.mesh_was_open: bool = True
        self.load()

    # ---- active slots -------------------------------------------------
    # `actives` may hold several nearby players at once; `active` keeps the
    # historical single-slot meaning for existing call sites and runtime.json.

    @property
    def active(self) -> ActiveSlot | None:
        if not self.actives:
            return None
        for slot in self.actives.values():
            if not self.is_local(slot.pubkey):
                return slot
        return next(iter(self.actives.values()))

    @active.setter
    def active(self, slot: ActiveSlot | None) -> None:
        if slot is None:
            self.actives = {}
        else:
            self.actives = {slot.pubkey: slot}

    def active_for(self, pubkey: str) -> ActiveSlot | None:
        return self.actives.get(normalize_pubkey(pubkey))

    def is_active(self, pubkey: str) -> bool:
        return normalize_pubkey(pubkey) in self.actives

    def local_active_keys(self, local_max_hops: int = DEFAULT_LOCAL_MAX_HOPS) -> list[str]:
        return [
            k for k in self.actives if self.is_local(k, local_max_hops=local_max_hops)
        ]

    def nonlocal_active_keys(self, local_max_hops: int = DEFAULT_LOCAL_MAX_HOPS) -> list[str]:
        return [
            k for k in self.actives if not self.is_local(k, local_max_hops=local_max_hops)
        ]

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.exception("failed to load access.json")
            return
        self.closed_episode = int(raw.get("closed_episode") or 0)
        self.mesh_was_open = bool(raw.get("mesh_was_open", True))
        self.actives = {}
        act_list = raw.get("actives")
        if isinstance(act_list, list):
            for item in act_list:
                if isinstance(item, dict) and item.get("pubkey"):
                    slot = _slot_from_dict(item, self.closed_episode)
                    self.actives[slot.pubkey] = slot
        act = raw.get("active")
        if isinstance(act, dict) and act.get("pubkey"):
            busy_ep = int(act.get("busy_notified_episode") or 0)
            if not busy_ep and bool(act.get("pause_notified")):
                busy_ep = max(1, self.closed_episode)
            delivered = bool(act.get("busy_delivered", bool(act.get("pause_notified"))))
            resume_ep = int(act.get("resume_notified_episode") or 0)
            if not resume_ep and bool(act.get("resume_notified")):
                resume_ep = busy_ep
            slot = ActiveSlot(
                pubkey=normalize_pubkey(act["pubkey"]),
                display_name=str(act.get("display_name") or ""),
                since=float(act.get("since") or time.time()),
                paused=bool(act.get("paused")),
                busy_attempt_episode=int(act.get("busy_attempt_episode") or busy_ep or 0),
                busy_notified_episode=busy_ep,
                busy_tries=int(act.get("busy_tries") or (1 if busy_ep else 0)),
                busy_last_try_at=float(act.get("busy_last_try_at") or 0.0),
                busy_delivered=delivered,
                resume_notified_episode=resume_ep,
            )
            self.actives.setdefault(slot.pubkey, slot)
        self.queue = []
        for item in raw.get("queue") or []:
            if not isinstance(item, dict) or not item.get("pubkey"):
                continue
            self.queue.append(
                QueueEntry(
                    pubkey=normalize_pubkey(item["pubkey"]),
                    display_name=str(item.get("display_name") or ""),
                    enqueued_at=float(item.get("enqueued_at") or time.time()),
                    notified_waiting=bool(item.get("notified_waiting")),
                    busy_attempt_episode=int(item.get("busy_attempt_episode") or 0),
                    busy_notified_episode=int(item.get("busy_notified_episode") or 0),
                    busy_tries=int(item.get("busy_tries") or 0),
                    busy_last_try_at=float(item.get("busy_last_try_at") or 0.0),
                )
            )
        bans = raw.get("bans") or {}
        self.bans = {
            normalize_pubkey(k): dict(v) if isinstance(v, dict) else {"reason": str(v)}
            for k, v in bans.items()
        }
        offers = raw.get("offers") or {}
        if isinstance(offers, dict) and offers:
            for k, exp in offers.items():
                self.offer_pubkey = normalize_pubkey(k)
                self.offer_expires_at = (
                    float(exp)
                    if not isinstance(exp, dict)
                    else float(exp.get("expires_at") or 0)
                )
                break
        else:
            self.offer_pubkey = ""
            self.offer_expires_at = 0.0
        air = raw.get("airtime") or {}
        self.airtime = {
            normalize_pubkey(k): dict(v) for k, v in air.items() if isinstance(v, dict)
        }

    def save(self) -> None:
        data = {
            "active": asdict(self.active) if self.active else None,
            "actives": [asdict(s) for s in self.actives.values()],
            "queue": [asdict(q) for q in self.queue],
            "bans": self.bans,
            "offers": {self.offer_pubkey: self.offer_expires_at} if self.offer_pubkey else {},
            "airtime": self.airtime,
            "closed_episode": self.closed_episode,
            "mesh_was_open": self.mesh_was_open,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def note_mesh_state(self, is_open: bool) -> None:
        """Track open→closed transitions; each closure starts a new episode."""
        opened = bool(is_open)
        if not opened and self.mesh_was_open:
            self.closed_episode += 1
            self.save()
        elif opened != self.mesh_was_open:
            self.save()
        self.mesh_was_open = opened

    def _busy_should_send(
        self,
        *,
        attempt_episode: int,
        tries: int,
        last_try_at: float,
        delivered: bool,
        now: float | None = None,
    ) -> bool:
        # Once ACKed, stay silent until resume clears busy_delivered.
        if delivered:
            return False
        # Exhausted retries: stay silent until resume clears the counters.
        if tries >= BUSY_NOTICE_MAX_TRIES:
            return False
        if attempt_episode != self.closed_episode:
            return True
        if tries > 0:
            ts = now if now is not None else time.time()
            if ts - last_try_at < BUSY_NOTICE_RETRY_SECONDS:
                return False
        return True

    def begin_busy_notice_for_active(self, pubkey: str | None = None) -> str | None:
        """Return MSG_BUSY if the active player should be told; else None (stay silent)."""
        slot = self.active_for(pubkey) if pubkey else self.active
        if not slot:
            return None
        slot.paused = True
        if not self._busy_should_send(
            attempt_episode=slot.busy_attempt_episode,
            tries=slot.busy_tries,
            last_try_at=slot.busy_last_try_at,
            delivered=slot.busy_delivered,
        ):
            self.save()
            return None
        if slot.busy_attempt_episode != self.closed_episode:
            slot.busy_tries = 0
            slot.busy_attempt_episode = self.closed_episode
        slot.busy_tries += 1
        slot.busy_last_try_at = time.time()
        self.save()
        return MSG_BUSY

    def begin_busy_notice_for_pubkey(self, pubkey: str) -> str | None:
        """Busy notice for active or queued player (ACK-gated)."""
        key = normalize_pubkey(pubkey)
        if key in self.actives:
            return self.begin_busy_notice_for_active(key)
        for q in self.queue:
            if q.pubkey != key:
                continue
            if q.busy_notified_episode > 0:
                return None
            if not self._busy_should_send(
                attempt_episode=q.busy_attempt_episode,
                tries=q.busy_tries,
                last_try_at=q.busy_last_try_at,
                delivered=False,
            ):
                return None
            if q.busy_attempt_episode != self.closed_episode:
                q.busy_tries = 0
                q.busy_attempt_episode = self.closed_episode
            q.busy_tries += 1
            q.busy_last_try_at = time.time()
            self.save()
            return MSG_BUSY
        return MSG_BUSY

    def mark_busy_ack(self, pubkey: str, ok: bool) -> None:
        """Record whether a busy notice was ACKed; only then silence further notices."""
        key = normalize_pubkey(pubkey)
        slot = self.actives.get(key)
        if slot is not None:
            if ok:
                slot.busy_notified_episode = max(1, self.closed_episode)
                slot.busy_delivered = True
            self.save()
            return
        for q in self.queue:
            if q.pubkey == key:
                if ok:
                    q.busy_notified_episode = max(1, self.closed_episode)
                self.save()
                return

    def begin_resume_notice(self, pubkey: str | None = None) -> str | None:
        """Resume once, and only if a busy notice was actually delivered."""
        slot = self.active_for(pubkey) if pubkey else self.active
        if not slot or not slot.paused:
            return None
        slot.paused = False
        if slot.busy_delivered and slot.resume_notified_episode != slot.busy_notified_episode:
            slot.resume_notified_episode = slot.busy_notified_episode
            slot.busy_delivered = False
            slot.busy_notified_episode = 0
            slot.busy_tries = 0
            slot.busy_attempt_episode = 0
            self.save()
            return MSG_RESUME
        slot.busy_delivered = False
        slot.busy_tries = 0
        slot.busy_attempt_episode = 0
        self.save()
        return None

    def set_paused(self, paused: bool, pubkey: str | None = None) -> str | None:
        """Compatibility wrapper: pause uses ACK-gated busy notice; unpause uses resume."""
        if paused:
            return self.begin_busy_notice_for_active(pubkey)
        return self.begin_resume_notice(pubkey)

    def _row(self, pubkey: str) -> dict[str, Any]:
        key = normalize_pubkey(pubkey)
        return self.airtime.setdefault(
            key,
            {
                "bytes_out": 0,
                "parts_out": 0,
                "commands": 0,
                "display_name": "",
                "last_seen": 0.0,
                "path_len": -1,
                "stable": False,
                "ack_ok": 0,
                "ack_fail": 0,
                "last_ack_ok_at": 0.0,
                "last_ack_fail_at": 0.0,
                "last_path_reset_at": 0.0,
            },
        )

    def _touch_name(self, pubkey: str, display_name: str) -> None:
        key = normalize_pubkey(pubkey)
        if not key:
            return
        row = self._row(key)
        if display_name:
            row["display_name"] = display_name
        row["last_seen"] = time.time()

    def update_path_quality(
        self,
        pubkey: str,
        *,
        path_len: int | None = None,
        ack_ok: bool | None = None,
        path_reset: bool = False,
        local_max_hops: int = DEFAULT_LOCAL_MAX_HOPS,
    ) -> None:
        key = normalize_pubkey(pubkey)
        if not key:
            return
        row = self._row(key)
        now = time.time()
        if path_len is not None:
            try:
                row["path_len"] = int(path_len)
            except (TypeError, ValueError):
                pass
        if ack_ok is True:
            row["ack_ok"] = int(row.get("ack_ok") or 0) + 1
            row["last_ack_ok_at"] = now
        elif ack_ok is False:
            row["ack_fail"] = int(row.get("ack_fail") or 0) + 1
            row["last_ack_fail_at"] = now
        if path_reset:
            row["last_path_reset_at"] = now
        row["stable"] = self.is_stable(key, local_max_hops=local_max_hops)
        row["last_seen"] = now
        self.save()

    def path_len_of(self, pubkey: str) -> int:
        row = self.airtime.get(normalize_pubkey(pubkey)) or {}
        try:
            return int(row.get("path_len", -1))
        except (TypeError, ValueError):
            return -1

    def is_stable(self, pubkey: str, *, local_max_hops: int = DEFAULT_LOCAL_MAX_HOPS) -> bool:
        key = normalize_pubkey(pubkey)
        row = self.airtime.get(key) or {}
        path_len = self.path_len_of(key)
        if not is_local_path(path_len, local_max_hops):
            return False
        now = time.time()
        last_fail = float(row.get("last_ack_fail_at") or 0)
        last_reset = float(row.get("last_path_reset_at") or 0)
        if last_fail and (now - last_fail) < STABILITY_FAIL_WINDOW_SECONDS:
            return False
        if last_reset and (now - last_reset) < STABILITY_RESET_WINDOW_SECONDS:
            return False
        return True

    def is_local(
        self, pubkey: str, *, path_len: int | None = None, local_max_hops: int = DEFAULT_LOCAL_MAX_HOPS
    ) -> bool:
        n = self.path_len_of(pubkey) if path_len is None else path_len
        return is_local_path(n, local_max_hops)

    def record_command(self, pubkey: str, display_name: str = "") -> None:
        key = normalize_pubkey(pubkey)
        self._touch_name(key, display_name)
        self.airtime[key]["commands"] = int(self.airtime[key].get("commands") or 0) + 1
        self.save()

    def record_outbound(self, pubkey: str, byte_len: int, parts: int = 1) -> None:
        key = normalize_pubkey(pubkey)
        if not key:
            return
        self._touch_name(key, "")
        self.airtime[key]["bytes_out"] = int(self.airtime[key].get("bytes_out") or 0) + max(
            0, byte_len
        )
        self.airtime[key]["parts_out"] = int(self.airtime[key].get("parts_out") or 0) + max(
            0, parts
        )
        self.save()

    def is_banned(self, pubkey: str) -> bool:
        return normalize_pubkey(pubkey) in self.bans

    def ban(self, pubkey: str, reason: str = "", display_name: str = "") -> None:
        key = normalize_pubkey(pubkey)
        self.bans[key] = {
            "reason": reason or "banned",
            "banned_at": time.time(),
            "display_name": display_name,
        }
        self.drop_queue(key)
        self.actives.pop(key, None)
        if self.offer_pubkey == key:
            self.clear_offer()
        self.save()

    def unban(self, pubkey: str) -> None:
        self.bans.pop(normalize_pubkey(pubkey), None)
        self.save()

    def drop_queue(self, pubkey: str) -> None:
        key = normalize_pubkey(pubkey)
        self.queue = [q for q in self.queue if q.pubkey != key]
        if self.offer_pubkey == key:
            self.clear_offer()
        self.save()

    def clear_queue(self) -> None:
        self.queue = []
        self.clear_offer()
        self.save()

    def clear_offer(self) -> None:
        self.offer_pubkey = ""
        self.offer_expires_at = 0.0

    def clear_active(self, pubkey: str | None = None) -> None:
        if pubkey is None:
            self.actives = {}
        else:
            self.actives.pop(normalize_pubkey(pubkey), None)
        self.save()

    def _queue_index(self, pubkey: str) -> int:
        key = normalize_pubkey(pubkey)
        for i, q in enumerate(self.queue):
            if q.pubkey == key:
                return i
        return -1

    def enqueue(self, pubkey: str, display_name: str, queue_max: int) -> tuple[bool, bool]:
        """Return (ok, already_queued)."""
        key = normalize_pubkey(pubkey)
        idx = self._queue_index(key)
        if idx >= 0:
            if display_name:
                self.queue[idx].display_name = display_name
            self.save()
            return True, True
        if key in self.actives:
            return True, True
        if len(self.queue) >= queue_max:
            return False, False
        self.queue.append(
            QueueEntry(pubkey=key, display_name=display_name or key[:8], enqueued_at=time.time())
        )
        self.save()
        return True, False

    def promote(self, pubkey: str, offer_timeout_seconds: int) -> str | None:
        key = normalize_pubkey(pubkey)
        self.drop_queue(key)
        demoted = self.active
        if demoted and demoted.pubkey != key:
            self.queue.insert(
                0,
                QueueEntry(
                    pubkey=demoted.pubkey,
                    display_name=demoted.display_name,
                    enqueued_at=time.time(),
                    notified_waiting=True,
                ),
            )
            self.actives.pop(demoted.pubkey, None)
        self.offer_pubkey = key
        self.offer_expires_at = time.time() + max(30, offer_timeout_seconds)
        self.save()
        mins = max(1, offer_timeout_seconds // 60)
        return MSG_OFFER.format(mins=mins, s="" if mins == 1 else "s")

    def claim(self, pubkey: str, display_name: str) -> None:
        key = normalize_pubkey(pubkey)
        self.drop_queue(key)
        self.clear_offer()
        self.actives[key] = ActiveSlot(
            pubkey=key,
            display_name=display_name or key[:8],
            since=time.time(),
            paused=False,
        )
        self.save()

    def _busy_decision_for_closed(
        self, key: str, display_name: str, *, single_player: bool, queue_max: int
    ) -> AccessDecision:
        """Mesh closed for this player: at most one busy notice, then silent."""
        if key in self.actives:
            msg = self.begin_busy_notice_for_active(key)
            if msg:
                return AccessDecision(allow_play=False, reply=msg, silent=False)
            return AccessDecision(allow_play=False, reply="", silent=True)

        if not single_player:
            msg = MSG_BUSY
            # Non-queued one-shot: caller ACKs via mark_busy_ack if they track it.
            return AccessDecision(allow_play=False, reply=msg, silent=False)

        ok, already = self.enqueue(key, display_name, queue_max)
        if not ok:
            return AccessDecision(allow_play=False, reply=MSG_QUEUE_FULL)
        msg = self.begin_busy_notice_for_pubkey(key)
        if msg:
            return AccessDecision(
                allow_play=False, reply=msg, enqueue=True, was_waiting=already, silent=False
            )
        return AccessDecision(
            allow_play=False, reply="", enqueue=True, was_waiting=True, silent=True
        )

    def _play_open_path(
        self,
        key: str,
        display_name: str,
        *,
        single_player: bool,
        queue_max: int,
        local_exception: bool,
        is_local: bool = False,
        max_local_players: int = 1,
    ) -> AccessDecision:
        if not single_player:
            return AccessDecision(allow_play=True, local_exception=local_exception)

        if self.offer_pubkey == key and time.time() <= self.offer_expires_at:
            self.claim(key, display_name)
            return AccessDecision(allow_play=True, claimed=True, local_exception=local_exception)

        slot = self.actives.get(key)
        if slot is not None:
            if slot.paused:
                slot.paused = False
                self.save()
            return AccessDecision(allow_play=True, local_exception=local_exception)

        # Nearby players share the dungeon up to max_local_players; their
        # traffic does not cross the mesh, so the radio ceiling still holds.
        if is_local and max_local_players > 1:
            if len(self.local_active_keys()) < max_local_players:
                self.claim(key, display_name)
                return AccessDecision(
                    allow_play=True, claimed=True, local_exception=local_exception
                )

        if not self.actives and not self.offer_pubkey:
            self.claim(key, display_name)
            return AccessDecision(allow_play=True, claimed=True, local_exception=local_exception)

        if not self.actives and self.offer_pubkey and self.offer_pubkey != key:
            ok, already = self.enqueue(key, display_name, queue_max)
            if not ok:
                return AccessDecision(allow_play=False, reply=MSG_QUEUE_FULL)
            if already:
                return AccessDecision(
                    allow_play=False,
                    reply="",
                    enqueue=True,
                    was_waiting=True,
                    local_exception=local_exception,
                    silent=True,
                )
            return AccessDecision(
                allow_play=False,
                reply=MSG_WAIT_TURN,
                enqueue=True,
                was_waiting=False,
                local_exception=local_exception,
            )

        ok, already = self.enqueue(key, display_name, queue_max)
        if not ok:
            return AccessDecision(allow_play=False, reply=MSG_QUEUE_FULL)
        idx = self._queue_index(key)
        if idx >= 0 and not self.queue[idx].notified_waiting:
            self.queue[idx].notified_waiting = True
            self.save()
            return AccessDecision(
                allow_play=False,
                reply=MSG_WAIT_TURN,
                enqueue=True,
                was_waiting=False,
                local_exception=local_exception,
            )
        return AccessDecision(
            allow_play=False,
            reply="",
            enqueue=True,
            was_waiting=True,
            local_exception=local_exception,
            silent=True,
        )

    def evaluate(
        self,
        pubkey: str,
        display_name: str,
        *,
        safety_enabled: bool,
        single_player: bool,
        bans_enabled: bool,
        is_quiet: bool,
        queue_max: int,
        offer_timeout_seconds: int,
        path_len: int | None = None,
        local_max_hops: int = DEFAULT_LOCAL_MAX_HOPS,
        max_local_players: int = 1,
    ) -> AccessDecision:
        key = normalize_pubkey(pubkey)
        self._touch_name(key, display_name)
        if path_len is not None:
            self.update_path_quality(key, path_len=path_len, local_max_hops=local_max_hops)

        if bans_enabled and self.is_banned(key):
            return AccessDecision(allow_play=False, reply=MSG_BANNED)

        if not safety_enabled:
            return AccessDecision(allow_play=True)

        local = self.is_local(key, path_len=path_len, local_max_hops=local_max_hops)
        may_play = is_quiet or local
        local_exception = (not is_quiet) and local

        if not may_play:
            return self._busy_decision_for_closed(
                key, display_name, single_player=single_player, queue_max=queue_max
            )

        return self._play_open_path(
            key,
            display_name,
            single_player=single_player,
            queue_max=queue_max,
            local_exception=local_exception,
            is_local=local,
            max_local_players=max_local_players,
        )

    def expire_offer_if_needed(
        self,
        offer_timeout_seconds: int,
        *,
        mesh_open: bool = True,
        local_max_hops: int = DEFAULT_LOCAL_MAX_HOPS,
    ) -> list[tuple[str, str]]:
        now = time.time()
        out: list[tuple[str, str]] = []
        if self.offer_pubkey and now > self.offer_expires_at:
            skipped = self.offer_pubkey
            self.clear_offer()
            self.drop_queue(skipped)
            self.save()
            nxt = self._offer_next(
                offer_timeout_seconds, mesh_open=mesh_open, local_max_hops=local_max_hops
            )
            if nxt:
                out.append(nxt)
        return out

    def _pick_queue_index(self, *, mesh_open: bool, local_max_hops: int) -> int:
        if not self.queue:
            return -1
        if mesh_open:
            return 0
        best_stable = -1
        best_local = -1
        for i, q in enumerate(self.queue):
            if not self.is_local(q.pubkey, local_max_hops=local_max_hops):
                continue
            if best_local < 0:
                best_local = i
            if self.is_stable(q.pubkey, local_max_hops=local_max_hops) and best_stable < 0:
                best_stable = i
                break
        if best_stable >= 0:
            return best_stable
        return best_local

    def _offer_next(
        self,
        offer_timeout_seconds: int,
        *,
        mesh_open: bool = True,
        local_max_hops: int = DEFAULT_LOCAL_MAX_HOPS,
    ) -> tuple[str, str] | None:
        if self.offer_pubkey or not self.queue:
            return None
        if self.actives and not mesh_open:
            return None
        if self.active:
            return None
        idx = self._pick_queue_index(mesh_open=mesh_open, local_max_hops=local_max_hops)
        if idx < 0:
            return None
        head = self.queue.pop(idx)
        self.offer_pubkey = head.pubkey
        self.offer_expires_at = time.time() + max(30, offer_timeout_seconds)
        self.save()
        mins = max(1, offer_timeout_seconds // 60)
        return head.pubkey, MSG_OFFER.format(mins=mins, s="" if mins == 1 else "s")

    def maybe_offer_after_quiet(
        self,
        offer_timeout_seconds: int,
        *,
        mesh_open: bool = True,
        local_max_hops: int = DEFAULT_LOCAL_MAX_HOPS,
    ) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        if self.active or self.offer_pubkey:
            return out
        nxt = self._offer_next(
            offer_timeout_seconds, mesh_open=mesh_open, local_max_hops=local_max_hops
        )
        if nxt:
            out.append(nxt)
        return out

    def to_runtime(self) -> dict[str, Any]:
        def enrich(pubkey: str, base: dict[str, Any]) -> dict[str, Any]:
            row = self.airtime.get(pubkey) or {}
            path_len = int(row.get("path_len", -1) or -1)
            return {
                **base,
                "path_len": path_len,
                "stable": bool(row.get("stable")),
                "local": is_local_path(path_len),
            }

        active = None
        if self.active:
            active = enrich(self.active.pubkey, asdict(self.active))

        return {
            "active": active,
            "actives": [enrich(s.pubkey, asdict(s)) for s in self.actives.values()],
            "queue": [enrich(q.pubkey, asdict(q)) for q in self.queue],
            "bans": [
                {"pubkey": k, "pubkey_short": k[:16], **v} for k, v in self.bans.items()
            ],
            "offer": {
                "pubkey": self.offer_pubkey,
                "pubkey_short": self.offer_pubkey[:16] if self.offer_pubkey else "",
                "expires_at": self.offer_expires_at,
            }
            if self.offer_pubkey
            else None,
            "closed_episode": self.closed_episode,
            "airtime": [
                {
                    "pubkey": k,
                    "pubkey_short": k[:16],
                    "display_name": v.get("display_name") or k[:8],
                    "bytes_out": int(v.get("bytes_out") or 0),
                    "parts_out": int(v.get("parts_out") or 0),
                    "commands": int(v.get("commands") or 0),
                    "airtime_units": int(v.get("bytes_out") or 0) // 50,
                    "last_seen": v.get("last_seen") or 0,
                    "path_len": int(v.get("path_len", -1) or -1),
                    "stable": bool(v.get("stable")),
                    "local": is_local_path(int(v.get("path_len", -1) or -1)),
                    "ack_ok": int(v.get("ack_ok") or 0),
                    "ack_fail": int(v.get("ack_fail") or 0),
                }
                for k, v in self.airtime.items()
            ],
        }
