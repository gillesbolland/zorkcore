"""Channel enter announce on new game start."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from zorcore.main import PluginApp


def _app(tmp_path: Path, **settings_kw) -> PluginApp:
    if settings_kw:
        (tmp_path / "config.json").write_text(
            json.dumps(settings_kw),
            encoding="utf-8",
        )
    app = PluginApp(tmp_path)
    app.companion = MagicMock()
    app.companion.send_channel = AsyncMock(return_value=True)
    app.companion.contact_display_name = MagicMock(return_value="")
    app._settle_before_tx = AsyncMock()
    return app


def test_announce_sends_channel_line(tmp_path: Path):
    async def _run() -> None:
        app = _app(
            tmp_path,
            world_events_channel_enabled=True,
            world_events_channel_name="zork",
            world_events_enabled=False,
        )
        await app._announce_player_enter("Alice", "aa" * 32)
        app.companion.send_channel.assert_awaited_once_with(
            "zork", "@Alice has entered the dungeon."
        )

    asyncio.run(_run())


def test_announce_strips_leading_at(tmp_path: Path):
    async def _run() -> None:
        app = _app(
            tmp_path,
            world_events_channel_enabled=True,
            world_events_channel_name="zork",
        )
        await app._announce_player_enter("@Bob", "bb" * 32)
        app.companion.send_channel.assert_awaited_once_with(
            "zork", "@Bob has entered the dungeon."
        )

    asyncio.run(_run())


def test_announce_falls_back_to_advert_nickname(tmp_path: Path):
    async def _run() -> None:
        key = "abcd" + "00" * 30
        app = _app(
            tmp_path,
            world_events_channel_enabled=True,
            world_events_channel_name="zork",
        )
        app.contact_sync.advert_names = {key: "MeshWizard"}
        await app._announce_player_enter("", key)
        app.companion.send_channel.assert_awaited_once_with(
            "zork", "@MeshWizard has entered the dungeon."
        )

    asyncio.run(_run())


def test_announce_never_puts_pubkey_on_channel(tmp_path: Path):
    async def _run() -> None:
        key = "abcd" + "00" * 30
        app = _app(
            tmp_path,
            world_events_channel_enabled=True,
            world_events_channel_name="zork",
        )
        await app._announce_player_enter(key[:12], key)
        app.companion.send_channel.assert_awaited_once_with(
            "zork", "@someone has entered the dungeon."
        )

    asyncio.run(_run())


def test_announce_skipped_when_channel_off(tmp_path: Path):
    async def _run() -> None:
        app = _app(
            tmp_path,
            world_events_channel_enabled=False,
            world_events_channel_name="zork",
            world_events_enabled=True,
        )
        await app._announce_player_enter("Alice", "aa" * 32)
        app.companion.send_channel.assert_not_awaited()

    asyncio.run(_run())


def test_death_announce_grue_line(tmp_path: Path):
    async def _run() -> None:
        from types import SimpleNamespace

        key = "aa" * 32
        app = _app(
            tmp_path,
            world_events_channel_enabled=True,
            world_events_channel_name="zork",
        )
        app.contact_sync.advert_names = {key: "MeshWizard"}
        session = SimpleNamespace(
            sender_key=key, display_name="", room_id="attic"
        )
        grue = app.game.dark.timer_message
        await app._announce_player_death(session, grue)
        app.companion.send_channel.assert_awaited_once_with(
            "zork",
            "🪦MeshWizard has been gobbled by a lurking grue in the attic!",
        )

    asyncio.run(_run())


def test_death_announce_generic(tmp_path: Path):
    async def _run() -> None:
        from types import SimpleNamespace

        key = "bb" * 32
        app = _app(
            tmp_path,
            world_events_channel_enabled=True,
            world_events_channel_name="zork",
        )
        session = SimpleNamespace(
            sender_key=key, display_name="Bob", room_id="attic"
        )
        await app._announce_player_death(session, "You have been eaten.")
        app.companion.send_channel.assert_awaited_once_with(
            "zork", "🪦Bob has died in the attic!"
        )

    asyncio.run(_run())


def test_death_announce_skipped_when_channel_off(tmp_path: Path):
    async def _run() -> None:
        from types import SimpleNamespace

        app = _app(
            tmp_path,
            world_events_channel_enabled=False,
            world_events_channel_name="zork",
        )
        session = SimpleNamespace(
            sender_key="aa" * 32, display_name="Alice", room_id="attic"
        )
        await app._announce_player_death(session, "dead")
        app.companion.send_channel.assert_not_awaited()

    asyncio.run(_run())
