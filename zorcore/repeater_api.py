"""Shared helpers for authenticated Repeater HTTP API calls."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zorcore.config import Settings


def resolve_api_token(settings: "Settings | None" = None) -> str:
    """Prefer env, then plugin config. Never log the value."""
    for key in ("OPENHOP_REPEATER_TOKEN", "REPEATER_API_TOKEN"):
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    if settings is not None:
        return (getattr(settings, "repeater_api_token", "") or "").strip()
    return ""


def api_headers(settings: "Settings | None" = None) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    token = resolve_api_token(settings)
    if token:
        # OpenHop accepts API tokens via X-API-Key; Bearer also works for JWTs.
        headers["X-API-Key"] = token
        headers["Authorization"] = f"Bearer {token}"
    return headers
