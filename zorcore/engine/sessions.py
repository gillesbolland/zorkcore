"""Per-player session persistence."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from zorcore.contact_sync import normalize_pubkey, resolve_pubkey

logger = logging.getLogger(__name__)


@dataclass
class ActiveTimer:
    id: str
    deadline_unix: float
    message: str
    death: bool = True


@dataclass
class Session:
    sender_key: str
    room_id: str
    inventory: list[str] = field(default_factory=list)
    visited: list[str] = field(default_factory=list)
    score: int = 0
    moves: int = 0
    alive: bool = True
    last_output: str = ""
    last_story: str = ""
    last_ui: str = ""
    last_send_failed: bool = False
    active_timers: list[ActiveTimer] = field(default_factory=list)
    room_objects: dict[str, list[str]] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    display_name: str = ""
    flags: dict[str, bool] = field(default_factory=dict)
    open_objects: set[str] = field(default_factory=set)
    lit_objects: set[str] = field(default_factory=set)
    containers: dict[str, list[str]] = field(default_factory=dict)
    content_id: str = ""
    content_version: str = ""
    deposited: list[str] = field(default_factory=list)  # scored deposits
    last_command: str = ""
    last_it: str = ""
    last_them: list[str] = field(default_factory=list)
    brief_mode: bool | None = None  # None = default revisit brief; True force brief; False force full
    opted_out: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["open_objects"] = sorted(self.open_objects)
        data["lit_objects"] = sorted(self.lit_objects)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        timers = []
        for t in data.get("active_timers", []):
            timers.append(
                ActiveTimer(
                    id=t["id"],
                    deadline_unix=float(t["deadline_unix"]),
                    message=t["message"],
                    death=bool(t.get("death", True)),
                )
            )
        content_id = str(
            data.get("content_id") or data.get("cartridge_id") or ""
        )
        content_version = str(
            data.get("content_version") or data.get("cartridge_version") or ""
        )
        return cls(
            sender_key=data["sender_key"],
            room_id=data["room_id"],
            inventory=list(data.get("inventory", [])),
            visited=list(data.get("visited", [])),
            score=int(data.get("score", 0)),
            moves=int(data.get("moves", 0)),
            alive=bool(data.get("alive", True)),
            last_output=str(data.get("last_output", "")),
            last_story=str(data.get("last_story", "")),
            last_ui=str(data.get("last_ui", "")),
            last_send_failed=bool(data.get("last_send_failed", False)),
            active_timers=timers,
            room_objects={k: list(v) for k, v in data.get("room_objects", {}).items()},
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
            display_name=str(data.get("display_name", "")),
            flags={str(k): bool(v) for k, v in data.get("flags", {}).items()},
            open_objects=set(data.get("open_objects", [])),
            lit_objects=set(data.get("lit_objects", [])),
            containers={k: list(v) for k, v in data.get("containers", {}).items()},
            content_id=content_id,
            content_version=content_version,
            deposited=list(data.get("deposited", [])),
            last_command=str(data.get("last_command", "")),
            last_it=str(data.get("last_it", "")),
            last_them=list(data.get("last_them", [])),
            brief_mode=data.get("brief_mode", None),
            opted_out=bool(data.get("opted_out", False)),
        )


class SessionStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, sender_key: str) -> Path:
        safe = "".join(c if c.isalnum() else "_" for c in sender_key.lower())[:80]
        return self.root / f"{safe}.json"

    def load(self, sender_key: str) -> Session | None:
        path = self._path(sender_key)
        if not path.exists():
            return None
        return Session.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save(self, session: Session) -> None:
        session.updated_at = time.time()
        path = self._path(session.sender_key)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(session.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(path)

    def delete(self, sender_key: str) -> None:
        path = self._path(sender_key)
        if path.exists():
            path.unlink()

    def list_sessions(self) -> list[Session]:
        sessions: list[Session] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                sessions.append(Session.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
        return sessions

    def migrate_truncated_keys(self, known_keys: set[str]) -> int:
        """Remap sessions with short sender_key to full pubkey; drop empty keys."""
        migrated = 0
        for path in list(self.root.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            key = str(data.get("sender_key", ""))
            if not key.strip():
                logger.info("Removing empty sender session %s", path.name)
                path.unlink(missing_ok=True)
                continue
            norm = normalize_pubkey(key)
            if len(norm) == 64:
                if path.name != f"{norm}.json":
                    session = Session.from_dict(data)
                    session.sender_key = norm
                    self.save(session)
                    if path != self._path(norm):
                        path.unlink(missing_ok=True)
                    migrated += 1
                continue
            full = resolve_pubkey(norm, known_keys)
            if not full:
                logger.warning("Cannot remap truncated session %s (key=%s)", path.name, key[:16])
                continue
            session = Session.from_dict(data)
            session.sender_key = full
            self.save(session)
            if path != self._path(full):
                path.unlink(missing_ok=True)
            logger.info("Migrated session %s → %s…", path.name, full[:16])
            migrated += 1
        return migrated

    def leaderboard(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = []
        for s in self.list_sessions():
            if not s.sender_key:
                continue
            rows.append(
                {
                    "sender_key": s.sender_key[:16],
                    "display_name": s.display_name or s.sender_key[:8],
                    "score": s.score,
                    "moves": s.moves,
                    "alive": s.alive,
                    "updated_at": s.updated_at,
                }
            )
        rows.sort(key=lambda r: (-r["score"], r["moves"], -r["updated_at"]))
        return rows[:limit]
