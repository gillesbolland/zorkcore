"""Companion client: pubkey resolve + ACK-gated multipart send."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from zorcore.companion_client import (
    RADIO_FAILURE_THRESHOLD,
    CompanionClient,
)
from zorcore.config import Settings


def _settings(**kwargs) -> Settings:
    fields = dict(
        meshcore_host="127.0.0.1",
        meshcore_port=1977,
        companion_advert_enabled=False,
        companion_advert_local=False,
        companion_advert_flood=False,
        repeater_api_base="http://127.0.0.1:8000",
        repeater_api_token="",
        adventurer_public_key="",
        advert_sync_hours=6,
        advert_sync_limit=20,
        path_hash_mode=2,
        region_scope="",
        max_chunk_bytes=145,
        max_chunks=8,
        inter_chunk_delay_ms=0,
        max_commands_per_minute=30,
        duplicate_ttl_seconds=5,
        reply_settle_ms=0,
        command_dedupe_seconds=20,
        safety_enabled=True,
        bans_enabled=True,
        play_max_tier="normal",
        quiet_hold_seconds=120,
        quiet_poll_seconds=30,
        active_grace_seconds=1200,
        single_player_enabled=True,
        offer_timeout_seconds=900,
        queue_max=20,
        local_max_hops=3,
        max_local_players=2,
        active_idle_seconds=3600,
        daemons_enabled=True,
        daemon_idle_pause_seconds=300,
        daemon_min_interval_seconds=60,
        world_events_enabled=True,
        world_events_channel_enabled=False,
        world_events_channel_name="",
        broadcast_min_interval_seconds=30,
        log_level="INFO",
    )
    fields.update(kwargs)
    return Settings(**fields)


def test_resolve_sender_key_prefers_full():
    async def _run() -> None:
        client = CompanionClient(_settings(), AsyncMock())
        client.list_contact_pubkeys = AsyncMock(return_value=set())
        full = "ab" * 32
        assert await client.resolve_sender_key(full) == full

    asyncio.run(_run())


def test_resolve_sender_key_from_prefix():
    async def _run() -> None:
        client = CompanionClient(_settings(), AsyncMock())
        full = "cd" * 32
        client.list_contact_pubkeys = AsyncMock(return_value={full, "ee" * 32})
        assert await client.resolve_sender_key(full[:12]) == full
        assert await client.resolve_sender_key("zz") == ""

    asyncio.run(_run())


def test_send_parts_aborts_on_ack_failure():
    async def _run() -> None:
        client = CompanionClient(_settings(), AsyncMock())
        client.meshcore = MagicMock()
        results = [
            SimpleNamespace(is_error=lambda: False),
            SimpleNamespace(is_error=lambda: True, payload="NO_ACK"),
            SimpleNamespace(is_error=lambda: False),
        ]

        async def send_msg(dst, text):
            return results.pop(0)

        client.meshcore.commands = SimpleNamespace(send_msg=send_msg)
        ok = await client.send_parts("aa" * 32, ["(1/3) a", "(2/3) b", "(3/3) c"])
        assert ok is False
        assert len(results) == 1  # third part never attempted

    asyncio.run(_run())


def test_send_parts_all_ok():
    async def _run() -> None:
        client = CompanionClient(_settings(), AsyncMock())
        client.meshcore = MagicMock()
        n = {"calls": 0}

        async def send_msg(dst, text):
            n["calls"] += 1
            return SimpleNamespace(is_error=lambda: False)

        client.meshcore.commands = SimpleNamespace(send_msg=send_msg)
        ok = await client.send_parts("aa" * 32, ["one", "two"])
        assert ok is True
        assert n["calls"] == 2

    asyncio.run(_run())


def test_send_channel_uses_chan_msg():
    async def _run() -> None:
        client = CompanionClient(_settings(), AsyncMock())
        client.meshcore = MagicMock()
        client.connected = True
        seen = {}

        async def get_channel(idx):
            if idx == 2:
                return SimpleNamespace(
                    is_error=lambda: False,
                    payload={"channel_name": "#zork", "channel_idx": 2},
                )
            return SimpleNamespace(
                is_error=lambda: False, payload={"channel_name": "", "channel_idx": idx}
            )

        async def send_chan_msg(chan, text):
            seen["chan"] = chan
            seen["text"] = text
            return SimpleNamespace(is_error=lambda: False)

        client.meshcore.commands = SimpleNamespace(
            get_channel=get_channel,
            set_channel=AsyncMock(),
            send_chan_msg=send_chan_msg,
        )
        ok = await client.send_channel("zork", "  Far below, a troll shrieks.  ")
        assert ok is True
        assert seen == {"chan": 2, "text": "Far below, a troll shrieks."}
        client.meshcore.commands.set_channel.assert_not_called()

    asyncio.run(_run())


def test_send_channel_creates_autochannel_slot():
    async def _run() -> None:
        client = CompanionClient(_settings(), AsyncMock())
        client.meshcore = MagicMock()
        client.connected = True

        async def get_channel(idx):
            return SimpleNamespace(
                is_error=lambda: False, payload={"channel_name": "", "channel_idx": idx}
            )

        set_channel = AsyncMock(return_value=SimpleNamespace(is_error=lambda: False))

        async def send_chan_msg(chan, text):
            return SimpleNamespace(is_error=lambda: False)

        client.meshcore.commands = SimpleNamespace(
            get_channel=get_channel,
            set_channel=set_channel,
            send_chan_msg=send_chan_msg,
        )
        ok = await client.send_channel("#dungeon", "hi")
        assert ok is True
        set_channel.assert_awaited_once()
        args = set_channel.await_args.args
        assert args[0] == 0
        assert args[1] == "#dungeon"

    asyncio.run(_run())


def test_apply_advert_policy_never_auto_sends():
    async def _run() -> None:
        client = CompanionClient(
            _settings(companion_advert_enabled=True, companion_advert_flood=True),
            AsyncMock(),
        )
        client.meshcore = MagicMock()
        client.connected = True
        client.send_advert = AsyncMock(return_value=True)
        await client._apply_advert_policy()
        client.send_advert.assert_not_called()

    asyncio.run(_run())


def test_content_dedupe_collapses_retry_storm():
    async def _run() -> None:
        handler = AsyncMock()
        client = CompanionClient(_settings(command_dedupe_seconds=20), handler)
        sender = "ab" * 32
        client.resolve_sender_key = AsyncMock(return_value=sender)

        from meshcore import EventType

        async def fire(ts: int, text: str = "New game") -> None:
            event = SimpleNamespace(
                type=EventType.CONTACT_MSG_RECV,
                payload={
                    "text": text,
                    "timestamp": ts,
                    "pubkey_prefix": sender,
                    "sender_name": "Player",
                },
                attributes={},
            )
            await client._handle_event(event)

        await fire(1)
        await fire(2)  # retry, new timestamp, same text
        await fire(3)
        assert handler.await_count == 1
        await fire(4, text="look")
        assert handler.await_count == 2

    asyncio.run(_run())


def test_ensure_contact_cooldown_after_no_event():
    """A no_event_received timeout cools the key so sync stops re-hammering it."""

    async def _run() -> None:
        client = CompanionClient(_settings(), AsyncMock())
        client.meshcore = MagicMock()
        client.list_contact_pubkeys = AsyncMock(return_value=set())
        calls = {"n": 0}

        async def add_contact(contact):
            calls["n"] += 1
            return SimpleNamespace(
                is_error=lambda: True,
                payload={"reason": "no_event_received"},
            )

        client.meshcore.commands = SimpleNamespace(add_contact=add_contact)
        key = "ab" * 32

        assert await client.ensure_contact(key) == "failed"
        assert calls["n"] == 1
        assert key in client._add_cooldown
        assert client._radio_timeout_streak == 1

        # Second attempt within cooldown must not touch the radio again.
        assert await client.ensure_contact(key) == "failed"
        assert calls["n"] == 1

    asyncio.run(_run())


def test_radio_breaker_trips_after_repeated_timeouts():
    """Consecutive timeouts pause the radio so a stuck companion is left alone."""

    async def _run() -> None:
        client = CompanionClient(_settings(), AsyncMock())
        client.meshcore = MagicMock()
        client.list_contact_pubkeys = AsyncMock(return_value=set())

        async def add_contact(contact):
            return SimpleNamespace(
                is_error=lambda: True,
                payload={"reason": "no_event_received"},
            )

        client.meshcore.commands = SimpleNamespace(add_contact=add_contact)

        assert not client.radio_paused
        for i in range(RADIO_FAILURE_THRESHOLD):
            key = f"{i:02x}" + "c" * 62
            assert await client.ensure_contact(key) == "failed"
        assert client.radio_paused

        # While paused, a brand-new key is skipped without touching the radio.
        called = {"n": 0}

        async def add_contact2(contact):
            called["n"] += 1
            return SimpleNamespace(is_error=lambda: False)

        client.meshcore.commands = SimpleNamespace(add_contact=add_contact2)
        assert await client.ensure_contact("ff" * 32) == "failed"
        assert called["n"] == 0

    asyncio.run(_run())


def test_successful_dm_clears_radio_breaker():
    """A DM that gets its ACK proves the radio recovered and clears the breaker."""

    async def _run() -> None:
        client = CompanionClient(_settings(), AsyncMock())
        client.meshcore = MagicMock()
        client._radio_timeout_streak = RADIO_FAILURE_THRESHOLD
        client._radio_paused_until = time.time() + 999
        assert client.radio_paused

        async def send_msg(dst, text):
            return SimpleNamespace(is_error=lambda: False)

        client.meshcore.commands = SimpleNamespace(send_msg=send_msg)
        assert await client.send_parts("aa" * 32, ["hello"]) is True
        assert not client.radio_paused
        assert client._radio_timeout_streak == 0

    asyncio.run(_run())
