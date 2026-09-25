"""Advert contact sync is import-only (no mass prune)."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from zorcore.main import MAX_ADDS_PER_SYNC, PluginApp


def _app(tmp_path: Path) -> PluginApp:
    app = PluginApp(tmp_path)
    app.companion = MagicMock()
    app.companion.connected = True
    app.companion.self_public_key = "11" * 32
    app.companion._table_full = False
    app.companion.radio_paused = False
    app.companion._unresolved_senders = 0
    app.companion.list_contact_pubkeys = AsyncMock(
        return_value={"aa" * 32, "bb" * 32, "cc" * 32}
    )
    app.companion.remove_contact = AsyncMock(return_value=True)
    app.companion.ensure_contact = AsyncMock(return_value="added")
    app.companion._refresh_self_info = AsyncMock()
    app._cleaned_contacts = True  # skip junk cleanup side effects
    app.write_runtime = MagicMock()
    return app


def test_sync_skips_prune_on_api_error(tmp_path: Path):
    async def _run() -> None:
        app = _app(tmp_path)
        app.contact_sync.fetch_advert_chat_names = MagicMock(return_value={})
        app.contact_sync.last_error = "401 unauthorized"
        app._prune_stale_contacts = AsyncMock()

        await app._sync_advert_contacts_once()

        app._prune_stale_contacts.assert_not_awaited()
        app.companion.remove_contact.assert_not_awaited()
        app.companion.ensure_contact.assert_not_awaited()
        assert app.stats["contact_sync_error"] == "401 unauthorized"

    asyncio.run(_run())


def test_sync_does_not_mass_remove_on_partial_advert_set(tmp_path: Path):
    async def _run() -> None:
        app = _app(tmp_path)
        # Freshest-N window only sees one of three known contacts.
        app.contact_sync.fetch_advert_chat_names = MagicMock(
            return_value={"aa" * 32: "Alice"}
        )
        app.contact_sync.last_error = ""
        app._prune_stale_contacts = AsyncMock()

        await app._sync_advert_contacts_once()

        app._prune_stale_contacts.assert_not_awaited()
        app.companion.remove_contact.assert_not_awaited()
        app.companion.ensure_contact.assert_not_awaited()
        assert app.stats["adverts_seen"] == 1

    asyncio.run(_run())


def test_sync_imports_missing_advert_keys(tmp_path: Path):
    async def _run() -> None:
        app = _app(tmp_path)
        new_key = "dd" * 32
        app.contact_sync.fetch_advert_chat_names = MagicMock(
            return_value={"aa" * 32: "Alice", new_key: "Newbie"}
        )
        app.contact_sync.last_error = ""

        await app._sync_advert_contacts_once()

        app.companion.ensure_contact.assert_awaited_once_with(new_key, "Newbie")
        assert app.stats["contacts_imported"] == 1

    asyncio.run(_run())


def test_sync_caps_adds_per_tick(tmp_path: Path):
    async def _run() -> None:
        app = _app(tmp_path)
        # Far more new advert keys than the per-tick cap allows.
        many = {f"{i:02x}" + "e" * 62: f"n{i}" for i in range(MAX_ADDS_PER_SYNC + 4)}
        app.contact_sync.fetch_advert_chat_names = MagicMock(return_value=many)
        app.contact_sync.last_error = ""

        await app._sync_advert_contacts_once()

        assert app.companion.ensure_contact.await_count == MAX_ADDS_PER_SYNC
        assert app.stats["contacts_imported"] == MAX_ADDS_PER_SYNC

    asyncio.run(_run())


def test_sync_skips_when_radio_paused(tmp_path: Path):
    async def _run() -> None:
        app = _app(tmp_path)
        app.companion.radio_paused = True
        app.contact_sync.fetch_advert_chat_names = MagicMock(
            return_value={"dd" * 32: "Newbie"}
        )
        app.contact_sync.last_error = ""

        await app._sync_advert_contacts_once()

        app.companion.ensure_contact.assert_not_awaited()
        assert "RADIO_PAUSED" in app.stats["contact_sync_error"]

    asyncio.run(_run())
