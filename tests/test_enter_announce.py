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


def test_announce_falls_back_to_pubkey_prefix(tmp_path: Path):
    async def _run() -> None:
        key = "abcd" + "00" * 30
        app = _app(
            tmp_path,
            world_events_channel_enabled=True,
            world_events_channel_name="zork",
        )
        await app._announce_player_enter("", key)
        app.companion.send_channel.assert_awaited_once_with(
            "zork", f"@{key[:8]} has entered the dungeon."
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
