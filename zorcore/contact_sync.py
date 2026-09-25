"""Sync recently advertised Chat Nodes into the game companion contacts."""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from typing import Any

from zorcore.config import Settings
from zorcore.repeater_api import api_headers

logger = logging.getLogger(__name__)

CONTACT_TYPE_CHAT = 1
ADVERT_CONTACT_TYPE = "Chat Node"


def normalize_pubkey(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw).hex().lower()
    text = str(raw).strip().lower().replace("0x", "")
    text = "".join(c for c in text if c in "0123456789abcdef")
    if len(text) >= 64:
        return text[:64]
    return text


def resolve_pubkey(prefix_or_key: str, known_keys: set[str] | list[str]) -> str:
    """Resolve a full 64-hex pubkey from a prefix or return full key if already complete."""
    key = normalize_pubkey(prefix_or_key)
    if len(key) == 64:
        return key
    if len(key) < 8:
        return ""
    matches = [normalize_pubkey(k) for k in known_keys if normalize_pubkey(k).startswith(key)]
    matches = [m for m in matches if len(m) == 64]
    if len(matches) == 1:
        return matches[0]
    return ""


def excluded_sync_keys(*, self_key: str = "", extra: set[str] | None = None) -> set[str]:
    out = set()
    for raw in (self_key, *(extra or set())):
        k = normalize_pubkey(raw)
        if len(k) == 64:
            out.add(k)
    return out


def filter_player_keys(candidate_keys: set[str], excluded: set[str]) -> set[str]:
    excl = {normalize_pubkey(k) for k in excluded}
    return {
        k
        for k in candidate_keys
        if len(normalize_pubkey(k)) == 64 and normalize_pubkey(k) not in excl
    }


def keys_to_add(candidate_keys: set[str], known_keys: set[str]) -> list[str]:
    known = {normalize_pubkey(k) for k in known_keys}
    return sorted(k for k in candidate_keys if k and k not in known and len(k) == 64)


def contact_dict_for_pubkey(pubkey_hex: str, name: str = "") -> dict[str, Any]:
    key = normalize_pubkey(pubkey_hex)
    label = (name or f"player-{key[:8]}").strip()[:16]
    return {
        "public_key": key,
        "type": CONTACT_TYPE_CHAT,
        "flags": 0,
        "out_path_len": -1,
        "out_path": "",
        "out_path_hash_mode": 0,
        "adv_name": label,
        "last_advert": 0,
        "adv_lat": 0.0,
        "adv_lon": 0.0,
    }


def extract_chat_pubkeys_from_adverts(payload: Any, *, limit: int) -> list[str]:
    """Return up to `limit` freshest Chat Node pubkeys from an adverts API payload."""
    rows: list[Any] = []
    if isinstance(payload, dict):
        data = payload.get("data", payload)
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict) and isinstance(data.get("adverts"), list):
            rows = data["adverts"]
    elif isinstance(payload, list):
        rows = payload

    scored: list[tuple[float, str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ctype = str(row.get("contact_type") or "")
        if ctype and ctype != ADVERT_CONTACT_TYPE:
            # API already filters, but be defensive if mistyped rows appear
            if "chat" not in ctype.lower() and "companion" not in ctype.lower():
                continue
        key = normalize_pubkey(row.get("pubkey") or row.get("public_key") or "")
        if len(key) != 64:
            continue
        seen = row.get("last_seen", row.get("timestamp", 0))
        try:
            score = float(seen or 0)
        except (TypeError, ValueError):
            score = 0.0
        name = str(row.get("node_name") or "")
        scored.append((score, key, name))

    scored.sort(key=lambda t: (-t[0], t[1]))
    out: list[str] = []
    seen_keys: set[str] = set()
    for _, key, _ in scored:
        if key in seen_keys:
            continue
        seen_keys.add(key)
        out.append(key)
        if len(out) >= max(1, limit):
            break
    return out


class AdvertContactSync:
    """Poll OpenHop recent Chat Node adverts and return pubkeys to import."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.last_error = ""
        self.adverts_seen = 0
        self.contacts_imported = 0

    def _get_json(self, path: str, query: dict[str, str]) -> Any:
        qs = urllib.parse.urlencode(query)
        url = f"{self.settings.repeater_api_base}{path}?{qs}"
        req = urllib.request.Request(
            url, headers=api_headers(self.settings), method="GET"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
        return json.loads(body) if body else {}

    def fetch_advert_chat_pubkeys(self) -> set[str]:
        hours = int(getattr(self.settings, "advert_sync_hours", 6) or 6)
        limit = int(getattr(self.settings, "advert_sync_limit", 20) or 20)
        try:
            payload = self._get_json(
                "/api/adverts_by_contact_type",
                {
                    "contact_type": ADVERT_CONTACT_TYPE,
                    "hours": str(hours),
                    "limit": str(max(limit * 3, limit)),  # over-fetch then unique/freshest
                },
            )
        except Exception as exc:
            self.last_error = f"adverts_by_contact_type: {exc}"
            logger.warning("adverts poll failed: %s", exc)
            self.adverts_seen = 0
            return set()
        keys = set(extract_chat_pubkeys_from_adverts(payload, limit=limit))
        self.adverts_seen = len(keys)
        self.last_error = ""  # successful HTTP parse — empty mesh is OK
        return keys
