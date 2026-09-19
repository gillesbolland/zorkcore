"""MeshCore companion TCP client: DMs, chunking, ACK policy, optional advert."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from zorcore.config import Settings, autochannel_mesh_name
from zorcore.contact_sync import normalize_pubkey, resolve_pubkey
from zorcore.text import format_multipart, normalize_command

logger = logging.getLogger(__name__)

MessageHandler = Callable[[str, str, str], Awaitable[None]]
# sender_key, text, display_name



@dataclass
class RateLimiter:
    max_per_minute: int
    hits: deque[float] = field(default_factory=deque)

    def allow(self, now: float | None = None) -> bool:
        now = now or time.time()
        while self.hits and now - self.hits[0] > 60:
            self.hits.popleft()
        if len(self.hits) >= self.max_per_minute:
            return False
        self.hits.append(now)
        return True


@dataclass
class DuplicateCache:
    ttl: float
    _seen: dict[str, float] = field(default_factory=dict)

    def seen(self, key: str, now: float | None = None) -> bool:
        """Return True if key was already seen within TTL; otherwise record and return False."""
        now = now or time.time()
        expired = [k for k, t in self._seen.items() if now - t > self.ttl]
        for k in expired:
            del self._seen[k]
        if key in self._seen:
            return True
        self._seen[key] = now
        return False


class CompanionClient:
    """Thin wrapper around meshcore.MeshCore for DMs, chunking, and ACK handling."""

    def __init__(
        self,
        settings: Settings,
        on_message: MessageHandler,
        companion_name: str = "Zork🕹️",
    ) -> None:
        self.settings = settings
        self.on_message = on_message
        self.companion_name = companion_name
        self.meshcore: Any = None
        self.connected = False
        self.self_public_key = ""
        self.self_name = ""
        self._rate = RateLimiter(settings.max_commands_per_minute)
        self._dupes = DuplicateCache(float(settings.duplicate_ttl_seconds))
        self._cmd_dupes = DuplicateCache(float(settings.command_dedupe_seconds))
        self._lock = asyncio.Lock()
        self._table_full = False

    async def connect(self) -> None:
        from meshcore import EventType, MeshCore

        self.meshcore = await MeshCore.create_tcp(
            self.settings.meshcore_host,
            self.settings.meshcore_port,
            auto_reconnect=True,
        )
        self.connected = True
        await self._refresh_self_info()
        try:
            if hasattr(self.meshcore, "ensure_contacts"):
                await self.meshcore.ensure_contacts()
        except Exception:
            logger.debug("ensure_contacts failed", exc_info=True)
        await self._apply_advert_policy()
        await self._apply_path_and_region()
        await self._apply_multi_acks()

        async def _handler(event: Any) -> None:
            try:
                await self._handle_event(event)
            except Exception:
                logger.exception("Error handling companion event")

        try:
            self.meshcore.dispatcher.subscribe(EventType.CONTACT_MSG_RECV, _handler)
        except Exception:
            logger.debug("subscribe CONTACT_MSG_RECV failed; will poll", exc_info=True)

        try:
            if hasattr(self.meshcore, "start_auto_message_fetching"):
                await self.meshcore.start_auto_message_fetching()
        except Exception:
            logger.debug("start_auto_message_fetching failed", exc_info=True)

        logger.info(
            "Connected to companion %s:%s advert=%s key=%s contacts=%s",
            self.settings.meshcore_host,
            self.settings.meshcore_port,
            self.settings.advert_mode,
            (self.self_public_key or "")[:16],
            len(getattr(self.meshcore, "contacts", {}) or {}),
        )

    async def _refresh_self_info(self) -> None:
        assert self.meshcore is not None
        try:
            info = getattr(self.meshcore, "self_info", None) or getattr(
                self.meshcore, "_self_info", None
            ) or {}
            if not isinstance(info, dict) or not info.get("public_key"):
                # SELF_INFO is returned by appstart (get_self_info may not exist)
                result = await self.meshcore.commands.send_appstart()
                if result is not None and not getattr(result, "is_error", lambda: False)():
                    info = getattr(result, "payload", None) or {}
            if isinstance(info, dict):
                key = info.get("public_key") or info.get("pub_key") or ""
                if isinstance(key, (bytes, bytearray)):
                    key = key.hex()
                key = normalize_pubkey(key)
                if len(key) == 64:
                    self.self_public_key = key
                self.self_name = str(info.get("name") or info.get("node_name") or self.self_name or "")
        except Exception:
            logger.debug("refresh self info unavailable", exc_info=True)

    async def send_advert(self, *, flood: bool = False) -> bool:
        """One-shot companion advert (local or flood)."""
        if not self.meshcore:
            return False
        try:
            cmds = self.meshcore.commands
            if hasattr(cmds, "send_advert"):
                await cmds.send_advert(flood=flood)
            elif hasattr(cmds, "advert"):
                await cmds.advert(flood=flood)
            elif hasattr(self.meshcore, "advertise"):
                await self.meshcore.advertise(flood=flood)
            else:
                logger.warning("No advertise command found on meshcore client")
                return False
            logger.warning(
                "Companion advert SENT mode=%s — visible on mesh",
                "flood" if flood else "local",
            )
            return True
        except Exception:
            logger.exception("Failed to send companion advert")
            return False

    async def _apply_advert_policy(self) -> None:
        """Never auto-advert. Companion stays silent unless an admin queues send_advert."""
        logger.info("Companion advert policy: silent (manual Local/Flood only)")

    async def apply_radio_policy(self) -> None:
        """Re-apply name / path / region / advert after a soft settings reload."""
        if not self.connected or not self.meshcore:
            return
        await self._apply_path_and_region()
        await self._apply_multi_acks()
        await self._apply_advert_policy()

    async def _apply_multi_acks(self) -> None:
        """Enable MeshCore double ACK so receive ACKs are sent twice for reliability."""
        assert self.meshcore is not None
        cmds = self.meshcore.commands
        try:
            if hasattr(cmds, "set_multi_acks"):
                await cmds.set_multi_acks(1)
                logger.info("Companion multi_acks=1 (double ACK on receive)")
            else:
                logger.warning("meshcore client has no set_multi_acks")
        except Exception:
            logger.exception("Failed to set multi_acks")

    async def _apply_path_and_region(self) -> None:
        """Force companion display name, 3-byte path hashes, and optional region scope."""
        assert self.meshcore is not None
        cmds = self.meshcore.commands
        try:
            if hasattr(cmds, "set_name"):
                await cmds.set_name(self.companion_name)
                self.self_name = self.companion_name
                logger.info("Companion radio name=%s", self.companion_name)
        except Exception:
            logger.exception("Failed to set companion radio name")

        mode = int(getattr(self.settings, "path_hash_mode", 2) or 2)
        try:
            if hasattr(cmds, "set_path_hash_mode"):
                await cmds.set_path_hash_mode(mode)
                logger.info("Companion path_hash_mode=%s (%s-byte)", mode, mode + 1)
            else:
                logger.warning("meshcore client has no set_path_hash_mode")
        except Exception:
            logger.exception("Failed to set path_hash_mode")

        scope = (getattr(self.settings, "region_scope", "") or "").strip()
        try:
            if hasattr(cmds, "set_default_flood_scope"):
                if scope:
                    await cmds.set_default_flood_scope(scope)
                    if hasattr(cmds, "set_flood_scope"):
                        await cmds.set_flood_scope(scope)
                    logger.info("Companion region_scope=%s", scope)
                else:
                    await cmds.set_default_flood_scope(None)
                    logger.info("Companion region_scope cleared (unscoped default)")
            elif scope:
                logger.warning("meshcore client has no set_default_flood_scope; cannot set %s", scope)
        except Exception:
            logger.exception("Failed to set region_scope")

    async def resolve_sender_key(self, raw: str) -> str:
        """Return full 64-hex pubkey, resolving MeshCore pubkey_prefix via contacts."""
        key = normalize_pubkey(raw)
        if len(key) == 64:
            return key
        if not key:
            return ""
        # Prefer in-memory MeshCore contact index (fast, authoritative)
        try:
            if self.meshcore and hasattr(self.meshcore, "get_contact_by_key_prefix"):
                if getattr(self.meshcore, "contacts_dirty", False) and hasattr(
                    self.meshcore, "ensure_contacts"
                ):
                    await self.meshcore.ensure_contacts(follow=True)
                contact = self.meshcore.get_contact_by_key_prefix(key)
                if contact and contact.get("public_key"):
                    return normalize_pubkey(contact["public_key"])
        except Exception:
            logger.debug("get_contact_by_key_prefix failed", exc_info=True)
        known = await self.list_contact_pubkeys()
        resolved = resolve_pubkey(key, known)
        if resolved:
            return resolved
        try:
            contacts = getattr(self.meshcore, "contacts", None) or {}
            if isinstance(contacts, dict):
                resolved = resolve_pubkey(key, set(contacts.keys()))
                if resolved:
                    return resolved
        except Exception:
            pass
        return ""

    async def _handle_event(self, event: Any) -> None:
        from meshcore import EventType

        et = getattr(event, "type", None)
        # Ignore channel / non-contact traffic (poll used to false-match CHANNEL_MSG_RECV)
        if et is not None and et != EventType.CONTACT_MSG_RECV and "CONTACT_MSG" not in str(et):
            return
        payload = getattr(event, "payload", None) or {}
        attrs = getattr(event, "attributes", None) or {}
        if not isinstance(payload, dict):
            return
        text = payload.get("text") or payload.get("msg") or payload.get("message") or ""
        if not text:
            return
        raw_key = (
            payload.get("pubkey_prefix")
            or attrs.get("pubkey_prefix")
            or payload.get("public_key")
            or payload.get("sender")
            or ""
        )
        if isinstance(raw_key, (bytes, bytearray)):
            raw_key = raw_key.hex()
        sender = await self.resolve_sender_key(str(raw_key))
        if not sender:
            logger.warning(
                "Unresolved sender pubkey (raw=%s…); skipping until contact sync",
                str(raw_key)[:16],
            )
            return
        name = str(payload.get("sender_name") or payload.get("name") or "")
        text_s = str(text)
        ts = payload.get("timestamp", payload.get("sender_timestamp", ""))
        dupe_key = f"{sender}:{ts}:{text_s}"
        if self._dupes.seen(dupe_key):
            logger.debug("Drop mesh redelivery dupe from %s…", sender[:16])
            return
        # Phone retry storms often get new timestamps — collapse by normalized text.
        cmd_key = f"{sender}:{normalize_command(text_s)}"
        if cmd_key.endswith(":"):
            pass  # empty command after normalize — still process once via timestamp key
        elif self._cmd_dupes.seen(cmd_key):
            logger.debug(
                "Drop content-dupe command from %s… (%r)",
                sender[:16],
                normalize_command(text_s)[:40],
            )
            return
        if not self._rate.allow():
            await self.send_parts(sender, ["Slow down — too many commands. Try again shortly."])
            return
        await self.on_message(sender, text_s, name)

    async def poll_messages_once(self) -> None:
        if not self.meshcore:
            return
        try:
            from meshcore import EventType

            result = await self.meshcore.commands.get_msg(timeout=0.3)
            if result is None:
                return
            et = getattr(result, "type", None)
            if et in (EventType.NO_MORE_MSGS, EventType.ERROR) or et is None:
                return
            if et == EventType.CONTACT_MSG_RECV or "CONTACT_MSG_RECV" in str(et):
                await self._handle_event(result)
        except Exception:
            logger.debug("poll get_msg failed", exc_info=True)

    async def send_dm(
        self,
        dst: str,
        text: str,
        *,
        scene_emoji: str = "",
        timer_prefix: str = "",
    ) -> bool:
        """Format as multipart and send ACK-gated. Returns True if all parts ACK'd."""
        parts = format_multipart(
            text,
            self.settings.max_chunk_bytes,
            self.settings.max_chunks,
            scene_emoji=scene_emoji,
            timer_prefix=timer_prefix,
        )
        return await self.send_parts(dst, parts)

    async def send_parts(self, dst: str, parts: list[str]) -> bool:
        """Send each part only after prior ACK. Abort on first failure."""
        if not self.meshcore or not dst:
            return False
        if not parts:
            return True
        async with self._lock:
            for i, part in enumerate(parts):
                ok = await self._send_one(dst, part)
                if not ok:
                    logger.warning(
                        "DM part %s/%s failed for %s… — aborting remaining",
                        i + 1,
                        len(parts),
                        dst[:16],
                    )
                    return False
                if i + 1 < len(parts) and self.settings.inter_chunk_delay_ms > 0:
                    await asyncio.sleep(self.settings.inter_chunk_delay_ms / 1000.0)
        return True

    async def _send_one(self, dst: str, text: str) -> bool:
        assert self.meshcore is not None
        cmds = self.meshcore.commands
        try:
            if hasattr(cmds, "send_msg_with_retry"):
                result = await cmds.send_msg_with_retry(
                    dst,
                    text,
                    max_attempts=3,
                    max_flood_attempts=1,
                    flood_after=2,
                )
                if result is None:
                    return False
            else:
                result = await cmds.send_msg(dst, text)
            if result is None:
                return False
            if getattr(result, "is_error", lambda: False)():
                payload = getattr(result, "payload", result)
                logger.warning("send_msg error: %s", payload)
                if "TABLE_FULL" in str(payload):
                    self._table_full = True
                return False
            return True
        except Exception:
            logger.exception("send_dm failed")
            return False

    async def resolve_autochannel_index(self, name: str) -> int | None:
        """Find or create a companion channel slot for an autochannel #name."""
        mesh_name = autochannel_mesh_name(name)
        if not mesh_name or not self.meshcore or not self.connected:
            return None
        cmds = self.meshcore.commands
        if not hasattr(cmds, "get_channel") or not hasattr(cmds, "set_channel"):
            logger.warning("Companion lacks get_channel/set_channel")
            return None
        free: int | None = None
        try:
            for idx in range(40):
                result = await cmds.get_channel(idx)
                if result is None or getattr(result, "is_error", lambda: False)():
                    continue
                payload = getattr(result, "payload", None) or {}
                if not isinstance(payload, dict):
                    continue
                existing = str(payload.get("channel_name") or "").strip()
                if existing == mesh_name:
                    return idx
                if not existing and free is None:
                    free = idx
            if free is None:
                logger.warning("No free companion channel slot for %s", mesh_name)
                return None
            set_result = await cmds.set_channel(free, mesh_name)
            if set_result is None or getattr(set_result, "is_error", lambda: False)():
                logger.warning(
                    "set_channel failed idx=%s name=%s: %s",
                    free,
                    mesh_name,
                    getattr(set_result, "payload", set_result),
                )
                return None
            logger.info("Autochannel %s installed at companion slot %s", mesh_name, free)
            return free
        except Exception:
            logger.exception("resolve_autochannel_index failed for %s", mesh_name)
            return None

    async def send_channel(self, name: str, text: str) -> bool:
        """Send one unchunked line to a MeshCore autochannel by name (#…)."""
        if not self.meshcore or not self.connected:
            return False
        msg = (text or "").strip()
        if not msg:
            return False
        chan = await self.resolve_autochannel_index(name)
        if chan is None:
            return False
        cmds = self.meshcore.commands
        try:
            if hasattr(cmds, "send_chan_msg"):
                result = await cmds.send_chan_msg(int(chan), msg)
            elif hasattr(cmds, "send_channel_msg"):
                result = await cmds.send_channel_msg(int(chan), msg)
            else:
                logger.warning("No send_chan_msg on meshcore client")
                return False
            if result is None:
                return False
            if getattr(result, "is_error", lambda: False)():
                logger.warning(
                    "send_channel error name=%s idx=%s: %s",
                    name,
                    chan,
                    getattr(result, "payload", result),
                )
                return False
            return True
        except Exception:
            logger.exception("send_channel failed name=%s", name)
            return False

    async def list_contact_pubkeys(self) -> set[str]:
        contacts = await self.get_contacts_map()
        return set(contacts.keys())

    async def get_contacts_map(self) -> dict[str, dict]:
        """Return pubkey -> contact dict from companion."""
        if not self.meshcore:
            return {}
        try:
            result = await self.meshcore.commands.get_contacts()
            payload = getattr(result, "payload", None)
            out: dict[str, dict] = {}
            if isinstance(payload, dict):
                for item in payload.values():
                    if not isinstance(item, dict):
                        continue
                    key = item.get("public_key") or item.get("pubkey") or ""
                    if isinstance(key, (bytes, bytearray)):
                        key = key.hex()
                    key = normalize_pubkey(key)
                    if len(key) == 64:
                        out[key] = item
            return out
        except Exception:
            logger.debug("get contacts failed", exc_info=True)
            return {}

    async def get_path_len(self, pubkey_hex: str) -> int:
        """Hop count from companion contact; -1 if flood/unknown."""
        key = normalize_pubkey(pubkey_hex)
        contacts = await self.get_contacts_map()
        item = contacts.get(key)
        if not item:
            return -1
        raw = item.get("out_path_len")
        if raw is None:
            raw = item.get("path_len")
        try:
            n = int(raw)
        except (TypeError, ValueError):
            return -1
        if n < 0 or n >= 255:
            return -1
        return n

    def contact_display_name(self, pubkey_hex: str) -> str:
        """Best MeshCore contact name for a pubkey, if the companion knows them."""
        key = normalize_pubkey(pubkey_hex)
        if len(key) != 64 or not self.meshcore:
            return ""
        contacts = getattr(self.meshcore, "contacts", None) or {}
        rows: list = []
        if isinstance(contacts, dict):
            rows = list(contacts.values())
        elif isinstance(contacts, list):
            rows = contacts
        for row in rows:
            if not isinstance(row, dict):
                continue
            pk = normalize_pubkey(
                row.get("public_key")
                or row.get("publicKey")
                or row.get("pubkey")
                or ""
            )
            if pk != key and not (pk and key.startswith(pk)):
                continue
            name = str(
                row.get("adv_name")
                or row.get("name")
                or row.get("advName")
                or row.get("node_name")
                or ""
            ).strip()
            if name:
                return name
        return ""

    async def ensure_contact(self, pubkey_hex: str, display_name: str = "") -> str:
        """Add chat contact if missing. Returns 'added', 'exists', or 'failed'."""
        from zorcore.contact_sync import contact_dict_for_pubkey

        key = normalize_pubkey(pubkey_hex)
        if len(key) != 64 or not self.meshcore:
            return "failed"
        known = await self.list_contact_pubkeys()
        if key in known:
            return "exists"
        if self._table_full:
            logger.warning("Skipping add_contact %s… — contact table full", key[:16])
            return "failed"
        contact = contact_dict_for_pubkey(key, display_name)
        try:
            result = await self.meshcore.commands.add_contact(contact)
            if getattr(result, "is_error", lambda: False)():
                payload = getattr(result, "payload", result)
                logger.warning("add_contact failed for %s: %s", key[:16], payload)
                if "TABLE_FULL" in str(payload):
                    self._table_full = True
                return "failed"
            self._table_full = False
            logger.info("Added contact %s (%s)", key[:16], display_name or "player")
            return "added"
        except Exception:
            logger.exception("add_contact error for %s", key[:16])
            return "failed"

    async def remove_contact(self, pubkey_hex: str) -> bool:
        key = normalize_pubkey(pubkey_hex)
        if len(key) != 64 or not self.meshcore:
            return False
        cmds = self.meshcore.commands
        try:
            if hasattr(cmds, "remove_contact"):
                result = await cmds.remove_contact(key)
            elif hasattr(cmds, "delete_contact"):
                result = await cmds.delete_contact(key)
            else:
                logger.warning("No remove_contact command on meshcore client")
                return False
            if getattr(result, "is_error", lambda: False)():
                logger.warning("remove_contact failed for %s: %s", key[:16], getattr(result, "payload", result))
                return False
            logger.info("Removed contact %s…", key[:16])
            self._table_full = False
            return True
        except Exception:
            logger.exception("remove_contact error for %s", key[:16])
            return False

    async def disconnect(self) -> None:
        if self.meshcore:
            try:
                await self.meshcore.disconnect()
            except Exception:
                logger.debug("disconnect error", exc_info=True)
        self.connected = False
