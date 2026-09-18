"""Shared dungeon state: NPC positions, life, stash, and daemon clocks.

Unlike a Session (one private world per player), this file is owned by the
plugin and observed by every player, so two people playing at once meet the
same thief and can warn each other.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

DEFAULT_IDLE_PAUSE_SECONDS = 300.0
DEFAULT_MIN_INTERVAL_SECONDS = 60.0


class WorldState:
    """Plugin-owned NPC state, reset when the content id/version changes."""

    def __init__(
        self,
        path: Path,
        *,
        now: Callable[[], float] | None = None,
    ) -> None:
        self.path = path
        self.now = now or time.time
        self.content_id: str = ""
        self.content_version: str = ""
        self.npcs: dict[str, dict[str, Any]] = {}
        self.daemon_next: dict[str, float] = {}
        self.paused_since: float | None = None
        self.last_player_at: float = 0.0
        self.updated_at: float = 0.0
        self._defs: dict[str, dict[str, Any]] = {}
        self.load()

    # ---------- persistence ----------

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.exception("failed to load npcs.json")
            return
        self.content_id = str(
            raw.get("content") or raw.get("cartridge") or ""
        )
        self.content_version = str(
            raw.get("content_version") or raw.get("cartridge_version") or ""
        )
        npcs = raw.get("npcs") or {}
        self.npcs = {str(k): dict(v) for k, v in npcs.items() if isinstance(v, dict)}
        dn = raw.get("daemon_next") or {}
        self.daemon_next = {str(k): float(v) for k, v in dn.items()}
        ps = raw.get("paused_since")
        self.paused_since = float(ps) if ps else None
        self.last_player_at = float(raw.get("last_player_at") or 0.0)
        self.updated_at = float(raw.get("updated_at") or 0.0)

    def save(self) -> None:
        data = {
            "content": self.content_id,
            "content_version": self.content_version,
            "npcs": self.npcs,
            "daemon_next": self.daemon_next,
            "paused_since": self.paused_since,
            "last_player_at": self.last_player_at,
            "updated_at": self.now(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ---------- lifecycle ----------

    def sync_content(
        self,
        content_id: str,
        content_version: str,
        npc_defs: dict[str, dict[str, Any]] | None,
    ) -> None:
        """Reset shared state when content id/version changes; seed NPC homes."""
        self._defs = {str(k): dict(v) for k, v in (npc_defs or {}).items()}
        changed = (
            self.content_id != content_id
            or self.content_version != content_version
        )
        if changed:
            self.content_id = content_id
            self.content_version = content_version
            self.npcs = {}
            self.daemon_next = {}
            self.paused_since = None
        for npc_id, spec in self._defs.items():
            row = self.npcs.get(npc_id)
            if not row:
                self.npcs[npc_id] = {
                    "room": str(spec.get("home") or spec.get("room") or ""),
                    "alive": True,
                    "dead_since": None,
                    "stash": [],
                }
        self.save()

    def reset(self) -> None:
        self.npcs = {}
        self.daemon_next = {}
        self.paused_since = None
        for npc_id, spec in self._defs.items():
            self.npcs[npc_id] = {
                "room": str(spec.get("home") or spec.get("room") or ""),
                "alive": True,
                "dead_since": None,
                "stash": [],
            }
        self.save()

    # ---------- NPC queries ----------

    def _row(self, npc_id: str) -> dict[str, Any]:
        return self.npcs.setdefault(
            npc_id,
            {
                "room": str((self._defs.get(npc_id) or {}).get("home") or ""),
                "alive": True,
                "dead_since": None,
                "stash": [],
            },
        )

    def room_of(self, npc_id: str) -> str:
        row = self.npcs.get(npc_id)
        if not row or not row.get("alive"):
            return ""
        return str(row.get("room") or "")

    def is_alive(self, npc_id: str) -> bool:
        row = self.npcs.get(npc_id)
        return bool(row and row.get("alive"))

    def npcs_in_room(self, room_id: str) -> list[str]:
        return [
            nid
            for nid, row in self.npcs.items()
            if row.get("alive") and str(row.get("room") or "") == room_id
        ]

    def home_of(self, npc_id: str) -> str:
        spec = self._defs.get(npc_id) or {}
        return str(spec.get("home") or spec.get("room") or "")

    def display_name(self, npc_id: str) -> str:
        spec = self._defs.get(npc_id) or {}
        return str(spec.get("name") or npc_id)

    def presence_line(self, npc_id: str) -> str:
        spec = self._defs.get(npc_id) or {}
        return str(spec.get("here") or "")

    # ---------- NPC mutations ----------

    def move_npc(self, npc_id: str, room_id: str) -> None:
        row = self._row(npc_id)
        if not row.get("alive"):
            return
        row["room"] = room_id
        self.save()

    def kill_npc(self, npc_id: str) -> None:
        row = self._row(npc_id)
        row["alive"] = False
        row["dead_since"] = self.now()
        self.save()

    def revive_npc(self, npc_id: str) -> None:
        row = self._row(npc_id)
        row["alive"] = True
        row["dead_since"] = None
        row["room"] = self.home_of(npc_id) or row.get("room") or ""
        self.save()

    def stash_add(self, npc_id: str, object_id: str, owner: str) -> None:
        row = self._row(npc_id)
        stash = row.setdefault("stash", [])
        stash.append({"object": object_id, "owner": owner})
        self.save()

    def stash_of(self, npc_id: str) -> list[dict[str, str]]:
        row = self.npcs.get(npc_id) or {}
        return list(row.get("stash") or [])

    def stash_release(self, npc_id: str) -> list[dict[str, str]]:
        """Empty the stash and return items so each victim gets their own back."""
        row = self._row(npc_id)
        items = list(row.get("stash") or [])
        row["stash"] = []
        self.save()
        return items

    def stash_empty(self, npc_id: str) -> bool:
        return not (self.npcs.get(npc_id) or {}).get("stash")

    # ---------- daemon clocks ----------

    def note_player_activity(self, when: float | None = None) -> float:
        """Stamp activity; if the world was paused, shift deadlines by the gap."""
        ts = when if when is not None else self.now()
        shifted = 0.0
        if self.paused_since:
            shifted = max(0.0, ts - self.paused_since)
            if shifted:
                for key in list(self.daemon_next):
                    self.daemon_next[key] += shifted
            self.paused_since = None
        self.last_player_at = ts
        self.save()
        return shifted

    def is_idle(self, idle_pause_seconds: float = DEFAULT_IDLE_PAUSE_SECONDS) -> bool:
        if not self.last_player_at:
            return True
        return (self.now() - self.last_player_at) > idle_pause_seconds

    def pause_clock(self) -> None:
        if self.paused_since is None:
            self.paused_since = self.now()
            self.save()

    def due_daemons(
        self,
        daemons: list[dict[str, Any]],
        *,
        scope: str,
        min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS,
        key_prefix: str = "",
    ) -> list[dict[str, Any]]:
        """Return daemons whose deadline has passed, rescheduling each."""
        ts = self.now()
        due: list[dict[str, Any]] = []
        dirty = False
        for d in daemons:
            if str(d.get("scope", "world")).lower() != scope:
                continue
            did = str(d.get("id") or "")
            if not did:
                continue
            key = f"{key_prefix}{did}"
            every = max(float(min_interval_seconds), float(d.get("every_seconds") or 0))
            nxt = self.daemon_next.get(key)
            if nxt is None:
                self.daemon_next[key] = ts + every
                dirty = True
                continue
            if ts >= nxt:
                self.daemon_next[key] = ts + every
                dirty = True
                due.append(d)
        if dirty:
            self.save()
        return due

    def to_runtime(self, respawn_specs: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        specs = respawn_specs or self._defs
        ts = self.now()
        rows = []
        for nid, row in sorted(self.npcs.items()):
            spec = specs.get(nid) or {}
            respawn_in = None
            if not row.get("alive") and row.get("dead_since"):
                secs = float(spec.get("respawn_seconds") or 0)
                if secs:
                    respawn_in = max(0.0, (float(row["dead_since"]) + secs) - ts)
            rows.append(
                {
                    "id": nid,
                    "name": self.display_name(nid),
                    "room": str(row.get("room") or ""),
                    "alive": bool(row.get("alive")),
                    "stash": len(row.get("stash") or []),
                    "respawn_in": respawn_in,
                }
            )
        return {
            "content": self.content_id,
            "npcs": rows,
            "clock_paused": self.paused_since is not None,
            "last_player_at": self.last_player_at,
        }
