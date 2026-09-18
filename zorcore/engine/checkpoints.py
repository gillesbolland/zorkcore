"""Explicit SAVE/RESTORE checkpoint slots (one per player pubkey)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from zorcore.engine.sessions import Session


class CheckpointStore:
    """OG-style one-slot save per sender_key."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, sender_key: str) -> Path:
        safe = "".join(c if c.isalnum() else "_" for c in sender_key.lower())[:80]
        return self.root / f"{safe}.json"

    def save_checkpoint(self, session: Session) -> None:
        data = session.to_dict()
        data["checkpoint_at"] = time.time()
        path = self._path(session.sender_key)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)

    def load_checkpoint(self, sender_key: str) -> Session | None:
        path = self._path(sender_key)
        if not path.exists():
            return None
        try:
            return Session.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def has_checkpoint(self, sender_key: str) -> bool:
        return self._path(sender_key).exists()
