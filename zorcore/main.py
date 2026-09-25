"""ZorCore OpenHop plugin entrypoint."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import time
from pathlib import Path

from zorcore.access import (
    MSG_BANNED,
    MSG_BUSY,
    MSG_QUEUE_FULL,
    MSG_RESUME,
    MSG_WAIT_TURN,
    AccessController,
    QueueEntry,
)
from zorcore.content_loader import load_game_data
from zorcore.companion_client import CompanionClient
from zorcore.config import configure_logging, load_settings
from zorcore.contact_sync import (
    AdvertContactSync,
    excluded_sync_keys,
    filter_player_keys,
    keys_to_add,
    normalize_pubkey,
)
from zorcore.engine.game import TIMER_EMOJI, Game, StepResult
from zorcore.engine.checkpoints import CheckpointStore
from zorcore.engine.parser import RESEND_PHRASES
from zorcore.engine.sessions import SessionStore
from zorcore.quiet import QuietMonitor
from zorcore.repeater_api import api_headers
from zorcore.text import format_multipart, format_reply, normalize_command, utf8_len
from zorcore.worldstate import WorldState

logger = logging.getLogger(__name__)

CONTACT_SYNC_INTERVAL_SECONDS = 20.0
COMPANION_RECONNECT_SECONDS = 15.0
CONFIG_WATCH_SECONDS = 2.0
# Cap contact imports per sync tick so one cycle cannot monopolize the single
# companion radio for minutes on a busy mesh (GitHub #1).
MAX_ADDS_PER_SYNC = 5


class PluginApp:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.settings = load_settings(data_dir)
        configure_logging(self.settings.log_level)
        self.game_data = load_game_data()
        self.world_state = WorldState(data_dir / "npcs.json")
        self.world_state.sync_content(
            self.game_data.id, self.game_data.version, self.game_data.npcs
        )
        self.checkpoints = CheckpointStore(data_dir / "checkpoints")
        self.game = Game.from_game_data(
            self.game_data,
            world_state=self.world_state,
            checkpoint_store=self.checkpoints,
        )
        self.world = self.game_data.world
        self.sessions = SessionStore(data_dir / "sessions")
        self.access = AccessController(data_dir / "access.json")
        self.quiet = QuietMonitor(self.settings)
        self.contact_sync = AdvertContactSync(self.settings)
        self.stop = asyncio.Event()
        self.companion: CompanionClient | None = None
        self._cleaned_contacts = False
        self._config_mtime: float = 0.0
        self._was_quiet: bool | None = None
        self._last_broadcast_at: float = 0.0
        self._pending_adverts: list[bool] = []  # flood flags to send
        self._pending_radio_policy: bool = False
        self.stats = {
            "started_at": time.time(),
            "messages_in": 0,
            "messages_out": 0,
            "ack_failures": 0,
            "contacts_imported": 0,
            "adverts_seen": 0,
            "contact_sync_error": "",
            "unresolved_senders": 0,
        }
        self._refresh_config_mtime()

    @property
    def companion_name(self) -> str:
        return self.game_data.companion_name

    @property
    def start_phrases(self) -> frozenset[str]:
        return self.game_data.verbs.start_phrases

    @property
    def resend_phrases(self) -> frozenset[str]:
        # Radio reaction-repeat only — never again/g/repeat (OG redo).
        return RESEND_PHRASES | self.game_data.verbs.resend_phrases


    def _looks_like_key(self, name: str) -> bool:
        raw = (name or "").strip().lower()
        return bool(raw) and all(c in "0123456789abcdef" for c in raw) and len(raw) >= 8

    def _player_label(self, pubkey: str, *candidates: str) -> str:
        """Prefer a real MeshCore / session username over a pubkey stub."""
        key = normalize_pubkey(pubkey)
        for cand in candidates:
            name = str(cand or "").strip()
            if name and not self._looks_like_key(name):
                return name
        advert = (getattr(self.contact_sync, "advert_names", None) or {}).get(key) or ""
        if advert and not self._looks_like_key(advert):
            return advert
        if self.companion:
            contact = self.companion.contact_display_name(key)
            if contact and not self._looks_like_key(contact):
                return contact
        for cand in candidates:
            name = str(cand or "").strip()
            if name:
                return name
        return key[:8] if key else "—"

    def _channel_player_name(self, pubkey: str, display_name: str = "") -> str:
        """Nickname safe for public MeshCore channels — never a pubkey stub."""
        name = self._player_label(pubkey, display_name)
        if not name or self._looks_like_key(name):
            return "someone"
        return name

    def write_runtime(self) -> None:
        adventurer_key = self._adventurer_key()
        players = []
        for s in self.sessions.list_sessions():
            if not s.sender_key:
                continue
            air = (self.access.airtime or {}).get(normalize_pubkey(s.sender_key)) or {}
            players.append(
                {
                    "sender_key": s.sender_key[:16],
                    "pubkey": normalize_pubkey(s.sender_key),
                    "display_name": self._player_label(
                        s.sender_key, s.display_name, air.get("display_name")
                    ),
                    "score": s.score,
                    "moves": s.moves,
                    "alive": s.alive,
                    "room_id": s.room_id,
                    "last_send_failed": s.last_send_failed,
                    "updated_at": s.updated_at,
                }
            )
        adverts_seen = int(self.stats.get("adverts_seen", 0) or 0)
        snapshot = {
            "plugin_id": os.environ.get("OPENHOP_PLUGIN_ID", "zorcore"),
            "heartbeat_at": time.time(),
            "companion_connected": bool(self.companion and self.companion.connected),
            "advert_mode": self.settings.advert_mode,
            "companion_advert_enabled": self.settings.companion_advert_enabled,
            "companion_advert_local": self.settings.companion_advert_local,
            "companion_advert_flood": self.settings.companion_advert_flood,
            "session_count": len(players),
            "players": players,
            "leaderboard": self.sessions.leaderboard(),
            "adventurer_public_key": adventurer_key,
            "adventurer_url": self.settings.meshcore_url(
                self.companion_name, adventurer_key, 1
            ),
            "companion_name": self.companion_name,
            "content_id": self.game_data.id,
            "content_version": self.game_data.version,
            "content_name": self.game_data.meta.name,
            "meshcore_host": self.settings.meshcore_host,
            "meshcore_port": self.settings.meshcore_port,
            "advert_sync_hours": self.settings.advert_sync_hours,
            "advert_sync_limit": self.settings.advert_sync_limit,
            "path_hash_mode": self.settings.path_hash_mode,
            "region_scope": self.settings.region_scope,
            "contacts_imported": self.stats.get("contacts_imported", 0),
            "adverts_seen": adverts_seen,
            "contact_sync_error": self.stats.get("contact_sync_error", ""),
            "unresolved_senders": int(
                self.stats.get("unresolved_senders")
                or getattr(self.companion, "_unresolved_senders", 0)
                or 0
            ),
            "stats": self.stats,
            "safety": {
                "safety_enabled": self.settings.safety_enabled,
                "bans_enabled": self.settings.bans_enabled,
                "single_player_enabled": self.settings.single_player_enabled,
                "play_max_tier": self.settings.play_max_tier,
                "quiet_hold_seconds": self.settings.quiet_hold_seconds,
                "quiet_poll_seconds": self.settings.quiet_poll_seconds,
                "active_grace_seconds": self.settings.active_grace_seconds,
                "offer_timeout_seconds": self.settings.offer_timeout_seconds,
                "queue_max": self.settings.queue_max,
                "local_max_hops": self.settings.local_max_hops,
                "max_local_players": self.settings.max_local_players,
                "active_idle_seconds": self.settings.active_idle_seconds,
                "daemons_enabled": self.settings.daemons_enabled,
                "daemon_idle_pause_seconds": self.settings.daemon_idle_pause_seconds,
                "world_events_enabled": self.settings.world_events_enabled,
                "world_events_channel_enabled": self.settings.world_events_channel_enabled,
                "world_events_channel_name": self.settings.world_events_channel_name,
                "reply_settle_ms": self.settings.reply_settle_ms,
                "inter_chunk_delay_ms": self.settings.inter_chunk_delay_ms,
            },
            "available_regions": self.stats.get("available_regions")
            or self._fetch_available_regions(),
            "dungeon": self.world_state.to_runtime(self.game_data.npcs),
            "quiet": self.quiet.to_runtime(),
            "access": self.access.to_runtime(),
            "active_player": self.access.to_runtime().get("active"),
            "active_players": self.access.to_runtime().get("actives"),
            "queue": self.access.to_runtime().get("queue"),
            "bans": self.access.to_runtime().get("bans"),
            "offers": self.access.to_runtime().get("offer"),
            "airtime_leaderboard": self._airtime_leaderboard(),
        }
        tmp = self.data_dir / ".runtime.json.tmp"
        tmp.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        tmp.replace(self.data_dir / "runtime.json")

    def _adventurer_key(self) -> str:
        if self.companion and self.companion.self_public_key:
            return self.companion.self_public_key
        return self.settings.adventurer_public_key

    def _refresh_config_mtime(self) -> None:
        cfg = self.data_dir / "config.json"
        try:
            self._config_mtime = cfg.stat().st_mtime if cfg.exists() else 0.0
        except OSError:
            self._config_mtime = 0.0

    def _airtime_leaderboard(self) -> list[dict]:
        score_rows = self.sessions.leaderboard(limit=100)
        scores_by_short = {
            normalize_pubkey(r.get("sender_key") or ""): r for r in score_rows
        }
        access_rt = self.access.to_runtime()
        rows = []
        seen_short: set[str] = set()
        sessions_by_short = {
            normalize_pubkey(s.sender_key)[:16]: s for s in self.sessions.list_sessions()
        }
        for row in access_rt.get("airtime") or []:
            key = normalize_pubkey(row.get("pubkey") or "")
            short = key[:16]
            seen_short.add(short)
            score_row = scores_by_short.get(short) or {}
            session = sessions_by_short.get(short)
            rows.append(
                {
                    **row,
                    "display_name": self._player_label(
                        key,
                        row.get("display_name"),
                        score_row.get("display_name"),
                        getattr(session, "display_name", ""),
                    ),
                    "score": score_row.get("score", getattr(session, "score", 0) or 0),
                    "moves": score_row.get("moves", getattr(session, "moves", 0) or 0),
                    "alive": score_row.get(
                        "alive", True if session is None else session.alive
                    ),
                    "room_id": getattr(session, "room_id", "") or "",
                    "last_send_failed": bool(
                        getattr(session, "last_send_failed", False)
                    ),
                }
            )
        for short, score_row in scores_by_short.items():
            if short in seen_short:
                continue
            rows.append(
                {
                    "pubkey": short,
                    "pubkey_short": short,
                    "display_name": self._player_label(
                        short, score_row.get("display_name")
                    ),
                    "bytes_out": 0,
                    "parts_out": 0,
                    "commands": 0,
                    "airtime_units": 0,
                    "last_seen": score_row.get("updated_at") or 0,
                    "score": score_row.get("score", 0),
                    "moves": score_row.get("moves", 0),
                    "alive": score_row.get("alive", True),
                    "room_id": getattr(sessions_by_short.get(short), "room_id", "") or "",
                    "last_send_failed": bool(
                        getattr(sessions_by_short.get(short), "last_send_failed", False)
                    ),
                }
            )
        rows.sort(key=lambda r: (-int(r.get("score") or 0), -int(r.get("bytes_out") or 0)))
        return rows

    def _reload_settings_soft(self) -> None:
        """Reload config without restarting companion (thresholds / admin_actions)."""
        self.settings = load_settings(self.data_dir)
        configure_logging(self.settings.log_level)
        self.quiet.settings = self.settings
        self.contact_sync.settings = self.settings
        if self.companion:
            self.companion.settings = self.settings
            self._pending_radio_policy = True
        self._refresh_config_mtime()

    def _clear_admin_actions(self) -> None:
        cfg_path = self.data_dir / "config.json"
        try:
            raw = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
            if "admin_actions" not in raw:
                return
            raw.pop("admin_actions", None)
            tmp = cfg_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(raw, indent=2), encoding="utf-8")
            tmp.replace(cfg_path)
            self._refresh_config_mtime()
        except Exception:
            logger.exception("failed to clear admin_actions")

    def _apply_admin_actions(self, actions: list) -> list[tuple[str, str]]:
        """Apply admin ops; return DM notifications to send."""
        timeout = self.settings.offer_timeout_seconds
        notifies: list[tuple[str, str]] = []
        for action in actions:
            if not isinstance(action, dict):
                continue
            op = str(action.get("op") or "").strip().lower()
            pubkey = normalize_pubkey(action.get("pubkey") or "")
            reason = str(action.get("reason") or "")
            display_name = str(action.get("display_name") or "")
            try:
                if op == "promote" and pubkey:
                    msg = self.access.promote(pubkey, timeout)
                    if msg:
                        notifies.append((pubkey, msg))
                elif op == "drop_queue" and pubkey:
                    self.access.drop_queue(pubkey)
                elif op == "ban" and pubkey:
                    self.access.ban(pubkey, reason=reason, display_name=display_name)
                elif op == "unban" and pubkey:
                    self.access.unban(pubkey)
                elif op == "reset_session" and pubkey:
                    self.sessions.delete(pubkey)
                    if self.access.is_active(pubkey):
                        self.access.clear_active(pubkey)
                elif op == "clear_queue":
                    self.access.clear_queue()
                elif op == "reset_npcs":
                    self.world_state.reset()
                elif op == "forget_local" and pubkey:
                    self.access.forget_local(pubkey)
                elif op == "send_advert":
                    # Queued for the async config-watch loop — flood flag preserved.
                    self._pending_adverts.append(bool(action.get("flood")))
                elif op == "apply_radio_policy":
                    self._pending_radio_policy = True
                else:
                    logger.warning("unknown admin_action op=%s", op)
            except Exception:
                logger.exception("admin_action failed op=%s", op)
        return notifies

    def msg(self, key: str, default: str) -> str:
        """Content-overridable safety string; engine default when absent."""
        return (self.game_data.system_messages or {}).get(key) or default

    def _voiced(self, reply: str, pubkey: str = "") -> str:
        """Swap an engine safety string for the adventure's own wording."""
        if not reply:
            return reply
        if reply == MSG_BUSY:
            slot = self.access.active_for(pubkey) if pubkey else None
            key = "paused" if slot is not None else "busy"
            return self.msg(key, reply)
        if reply == MSG_WAIT_TURN:
            return self.msg("wait_turn", reply)
        if reply == MSG_RESUME:
            return self.msg("resume", reply)
        if reply == MSG_BANNED:
            return self.msg("banned", reply)
        if reply == MSG_QUEUE_FULL:
            return self.msg("queue_full", reply)
        if reply.startswith("Your turn"):
            override = (self.game_data.system_messages or {}).get("offer")
            if override:
                mins = max(1, self.settings.offer_timeout_seconds // 60)
                try:
                    return override.format(mins=mins, s="" if mins == 1 else "s")
                except (KeyError, IndexError, ValueError):
                    return reply
        return reply

    async def _tick_player_daemons(self, session) -> None:
        """scope=player daemons for one active session."""
        if not self.settings.daemons_enabled or not self.game_data.daemons:
            return
        if self.world_state.is_idle(self.settings.daemon_idle_pause_seconds):
            return
        res = self.game.apply_player_daemons(
            session, min_interval_seconds=self.settings.daemon_min_interval_seconds
        )
        if res is None:
            return
        self.sessions.save(session)
        text = "\n".join(t for t in res.story if t)
        if text and self._may_send_to(session.sender_key):
            await self._notify(session.sender_key, text)
        await self._emit_world_events(
            res.broadcasts, res.room_notices, actor_key=session.sender_key
        )

    async def _tick_world_daemons(self) -> None:
        """scope=world daemons + respawns; frozen while the dungeon is empty."""
        if not self.settings.daemons_enabled:
            return
        if self.world_state.is_idle(self.settings.daemon_idle_pause_seconds):
            # Nobody is playing — freeze the clock rather than run the dungeon
            # for an empty room. Deadlines are shifted on the next DM.
            self.world_state.pause_clock()
            return
        revived = self.game.apply_respawns(self.game_data.npcs)
        del revived  # respawns are deliberately silent
        broadcasts, notices = self.game.apply_world_daemons(
            min_interval_seconds=self.settings.daemon_min_interval_seconds
        )
        if broadcasts or notices:
            await self._emit_world_events(broadcasts, notices)

    def _sessions_in_room(self, room_id: str) -> list[str]:
        """Active players standing in a room (shared-dungeon fan-out target)."""
        out: list[str] = []
        for s in self.sessions.list_sessions():
            if not s.sender_key or not s.alive:
                continue
            if s.room_id != room_id:
                continue
            if self.settings.safety_enabled and not self.access.is_active(s.sender_key):
                continue
            out.append(s.sender_key)
        return out

    def _may_send_to(self, pubkey: str) -> bool:
        """Never DM a queued, banned, or paused player."""
        key = normalize_pubkey(pubkey)
        if self.settings.bans_enabled and self.access.is_banned(key):
            return False
        if not self.settings.safety_enabled:
            return True
        slot = self.access.active_for(key)
        if slot is None or slot.paused:
            return False
        if self.quiet.is_quiet:
            return True
        return self.access.is_local(key, local_max_hops=self.settings.local_max_hops)

    async def _announce_player_enter(self, display_name: str, pubkey: str = "") -> None:
        """One-shot MeshCore channel line when a player starts (if channel enabled)."""
        if not self.companion or not self.settings.world_events_channel_enabled:
            return
        chan = (self.settings.world_events_channel_name or "").strip()
        if not chan:
            return
        name = self._channel_player_name(pubkey, display_name)
        if name.startswith("@"):
            name = name[1:].strip() or "someone"
        suffix = " has entered the dungeon."
        max_b = int(self.settings.max_chunk_bytes or 145)
        # Truncate display name so "@name" + suffix fits one radio message.
        budget = max(1, max_b - 1 - utf8_len(suffix))
        encoded = name.encode("utf-8")
        if len(encoded) > budget:
            name = encoded[:budget].decode("utf-8", "ignore")
        text = f"@{name}{suffix}"
        await self._settle_before_tx()
        ok = await self.companion.send_channel(chan, text)
        if ok:
            self.stats["messages_out"] = int(self.stats.get("messages_out", 0)) + 1

    def _death_channel_line(self, session, death_story: str = "") -> str:
        """Public-channel death line — nickname only, never a pubkey stub."""
        name = self._channel_player_name(
            session.sender_key, getattr(session, "display_name", "") or ""
        )
        if name.startswith("@"):
            name = name[1:].strip() or "someone"
        room = self.world.rooms.get(session.room_id)
        room_label = (room.name if room else session.room_id or "the dungeon").strip()
        # Prefer "in the attic" over "in the Attic."
        room_phrase = room_label.rstrip(".")
        if room_phrase and not room_phrase.lower().startswith("the "):
            room_phrase = room_phrase[:1].lower() + room_phrase[1:]
            room_loc = f"in the {room_phrase}"
        else:
            room_loc = f"in {room_phrase}" if room_phrase else "in the dungeon"
        story = (death_story or "").strip()
        grue_msg = (getattr(self.game.dark, "timer_message", "") or "").strip()
        if grue_msg and story == grue_msg:
            body = f"{name} has been gobbled by a lurking grue {room_loc}!"
        else:
            body = f"{name} has died {room_loc}!"
        return f"🪦{body}"

    async def _announce_player_death(self, session, death_story: str = "") -> None:
        """One-shot MeshCore channel line when a player dies (if channel enabled)."""
        if not self.companion or not self.settings.world_events_channel_enabled:
            return
        chan = (self.settings.world_events_channel_name or "").strip()
        if not chan:
            return
        text = self._death_channel_line(session, death_story)
        max_b = int(self.settings.max_chunk_bytes or 145)
        encoded = text.encode("utf-8")
        if len(encoded) > max_b:
            text = encoded[:max_b].decode("utf-8", "ignore")
        await self._settle_before_tx()
        ok = await self.companion.send_channel(chan, text)
        if ok:
            self.stats["messages_out"] = int(self.stats.get("messages_out", 0)) + 1

    async def _emit_world_events(
        self,
        broadcasts: list[str],
        notices: list[dict[str, str]],
        *,
        actor_key: str = "",
    ) -> None:
        """Fan out shared-dungeon events; single message each, rate-limited."""
        if not self.companion:
            return
        for notice in notices:
            text = (notice.get("text") or "").strip()
            room = notice.get("room") or ""
            if not text or not room:
                continue
            for key in self._sessions_in_room(room):
                if key == actor_key or not self._may_send_to(key):
                    continue
                await self._notify(key, text)

        if not broadcasts:
            return
        dm_on = bool(self.settings.world_events_enabled)
        channel_on = bool(self.settings.world_events_channel_enabled)
        if not dm_on and not channel_on:
            return
        now = time.time()
        gap = float(self.settings.broadcast_min_interval_seconds or 0)
        if gap and (now - self._last_broadcast_at) < gap:
            return
        targets = (
            [
                k
                for k in self.access.actives
                if k != normalize_pubkey(actor_key) and self._may_send_to(k)
            ]
            if dm_on
            else []
        )
        if not targets and not channel_on:
            return
        for text in broadcasts[:1]:
            for key in targets:
                await self._notify(key, text)
            if channel_on:
                chan_name = (self.settings.world_events_channel_name or "").strip()
                if not chan_name:
                    logger.warning(
                        "world_events_channel_enabled but world_events_channel_name empty"
                    )
                else:
                    await self._settle_before_tx()
                    ok = await self.companion.send_channel(chan_name, text)
                    if ok:
                        self.stats["messages_out"] = (
                            int(self.stats.get("messages_out", 0)) + 1
                        )
            self._last_broadcast_at = time.time()

    async def _drain_game_outbound(self, actor_key: str = "") -> None:
        broadcasts, notices = self.game.drain_outbound()
        if broadcasts or notices:
            await self._emit_world_events(broadcasts, notices, actor_key=actor_key)

    async def _notify(self, pubkey: str, text: str) -> bool:
        if not self.companion or not text:
            return False
        try:
            await self._settle_before_tx()
            parts = format_multipart(
                text, self.settings.max_chunk_bytes, self.settings.max_chunks
            )
            ok = await self.companion.send_parts(pubkey, parts)
            self._record_send_quality(pubkey, ok, parts if ok else None)
            if ok:
                self.stats["messages_out"] = int(self.stats.get("messages_out", 0)) + 1
            return bool(ok)
        except Exception:
            logger.exception("notify failed for %s…", pubkey[:16])
            return False

    def _record_airtime(self, pubkey: str, parts: list[str]) -> None:
        total = sum(len(p.encode("utf-8")) for p in parts)
        self.access.record_outbound(pubkey, total, parts=len(parts))

    def _record_send_quality(
        self, pubkey: str, ok: bool, parts: list[str] | None = None
    ) -> None:
        hops = self.settings.local_max_hops
        self.access.update_path_quality(
            pubkey, ack_ok=ok, local_max_hops=hops
        )
        if ok and parts:
            self._record_airtime(pubkey, parts)

    async def _refresh_path_len(self, pubkey: str) -> int:
        if not self.companion:
            return self.access.path_len_of(pubkey)
        try:
            path_len = await self.companion.get_path_len(pubkey)
        except Exception:
            logger.debug("get_path_len failed", exc_info=True)
            return self.access.path_len_of(pubkey)
        self.access.update_path_quality(
            pubkey, path_len=path_len, local_max_hops=self.settings.local_max_hops
        )
        return path_len

    def _active_is_local(self) -> bool:
        """True when any current player is near enough to keep playing."""
        hops = self.settings.local_max_hops
        return any(self.access.is_local(k, local_max_hops=hops) for k in self.access.actives)

    def _within_active_grace(self, pubkey: str) -> bool:
        """True when pubkey holds an active slot and last session save is within grace."""
        key = normalize_pubkey(pubkey)
        if not self.access.is_active(key):
            return False
        grace = int(getattr(self.settings, "active_grace_seconds", 0) or 0)
        if grace <= 0:
            return False
        session = self.sessions.load(key)
        if session is None or not float(session.updated_at or 0):
            return False
        return (time.time() - float(session.updated_at)) <= grace

    def _fetch_available_regions(self) -> list[str]:
        """Region codes from OpenHop transport keys (#be → be)."""
        try:
            import urllib.request

            url = f"{self.settings.repeater_api_base}/api/transport_keys"
            req = urllib.request.Request(
                url, headers=api_headers(self.settings), method="GET"
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                body = json.loads(resp.read().decode("utf-8") or "{}")
            rows = body.get("data") if isinstance(body, dict) else None
            if not isinstance(rows, list):
                return []
            out: list[str] = []
            seen: set[str] = set()
            for row in rows:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("name") or "").strip()
                if not name:
                    continue
                if name.startswith("#"):
                    name = name[1:].strip()
                if not name or name == "*":
                    continue
                key = name.lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(name)
            out.sort(key=lambda s: s.lower())
            self.stats["available_regions"] = out
            return out
        except Exception:
            logger.debug("transport_keys fetch failed", exc_info=True)
            return list(self.stats.get("available_regions") or [])

    def _persist_adventurer_key(self, key: str) -> None:
        """Keep config.json aligned with the live companion identity."""
        key = normalize_pubkey(key)
        if len(key) != 64:
            return
        old = normalize_pubkey(self.settings.adventurer_public_key)
        if key == old:
            return
        cfg_path = self.data_dir / "config.json"
        try:
            raw = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
            raw["adventurer_public_key"] = key
            tmp = cfg_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(raw, indent=2), encoding="utf-8")
            tmp.replace(cfg_path)
        except Exception:
            logger.exception("failed to persist adventurer_public_key")
            return
        self.settings = load_settings(self.data_dir)
        self.contact_sync.settings = self.settings
        if self.companion:
            self.companion.settings = self.settings
        logger.warning(
            "Corrected adventurer_public_key %s… → %s…",
            old[:16],
            key[:16],
        )

    def _sync_adventurer_key_from_api(self) -> str:
        """Authoritative OpenHop identity pubkey for the game companion."""
        try:
            import urllib.request

            url = f"{self.settings.repeater_api_base}/api/identities"
            req = urllib.request.Request(url, headers=api_headers(self.settings), method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read().decode("utf-8") or "{}")
            rows = ((body.get("data") or {}).get("registered") or [])
            name_want = self.companion_name
            for row in rows:
                name = str(row.get("name") or "")
                short = name.split(":")[-1] if ":" in name else name
                if short == name_want or name.endswith(f":{name_want}"):
                    key = normalize_pubkey(row.get("public_key") or "")
                    if len(key) == 64:
                        return key
        except Exception:
            logger.debug("identities API lookup failed", exc_info=True)
        return ""

    async def handle_message(self, sender_key: str, text: str, display_name: str) -> None:
        self.stats["messages_in"] += 1
        raw = normalize_command(text)
        sender_key = normalize_pubkey(sender_key)
        path_len = await self._refresh_path_len(sender_key)
        self.access.note_mesh_state(self.quiet.is_quiet)
        self.world_state.note_player_activity()

        existing = self.sessions.load(sender_key)
        last_activity_at = float(existing.updated_at) if existing else None

        decision = self.access.evaluate(
            sender_key,
            display_name,
            safety_enabled=self.settings.safety_enabled,
            single_player=self.settings.single_player_enabled,
            bans_enabled=self.settings.bans_enabled,
            is_quiet=self.quiet.is_quiet,
            queue_max=self.settings.queue_max,
            offer_timeout_seconds=self.settings.offer_timeout_seconds,
            path_len=path_len,
            local_max_hops=self.settings.local_max_hops,
            max_local_players=self.settings.max_local_players,
            last_activity_at=last_activity_at,
            active_grace_seconds=self.settings.active_grace_seconds,
        )
        if not decision.allow_play:
            if decision.silent or not decision.reply:
                self.write_runtime()
                return
            reply = self._voiced(decision.reply, sender_key)
            if reply and self.companion:
                ok = await self._notify(sender_key, reply)
                if decision.reply == MSG_BUSY:
                    self.access.mark_busy_ack(sender_key, ok)
            self.write_runtime()
            return

        self.access.record_command(sender_key, display_name)
        session = existing

        if session is None:
            if raw in self.start_phrases:
                session = self.game.new_session(sender_key, display_name)
                self.sessions.save(session)
                await self._reply_result(
                    sender_key,
                    StepResult(
                        session.last_output,
                        session,
                        milestone="start",
                        story=session.last_story,
                        ui=session.last_ui,
                        scene_emoji=self.world.rooms[session.room_id].emoji,
                    ),
                )
                await self._announce_player_enter(display_name, sender_key)
                return
            help_text = self.game.short_help()
            if self.companion:
                await self._settle_before_tx()
                parts = format_multipart(
                    help_text, self.settings.max_chunk_bytes, self.settings.max_chunks
                )
                ok = await self.companion.send_parts(sender_key, parts)
                self._record_send_quality(sender_key, ok, parts if ok else None)
            return

        if display_name and not session.display_name:
            session.display_name = display_name

        due = self.game.apply_due_timers(session)
        if due is not None and due.milestone == "death":
            self.sessions.save(due.session)
            await self._reply_result(sender_key, due)
            await self._announce_player_death(due.session, due.story or due.text)
            if raw not in self.start_phrases and raw not in self.resend_phrases and raw != "help":
                return
            session = due.session

        result = self.game.step(session, text)
        self.sessions.save(result.session)
        if result.opt_out:
            self.access.clear_active(sender_key)
        await self._reply_result(sender_key, result)
        if result.milestone == "start":
            await self._announce_player_enter(
                result.session.display_name or display_name, sender_key
            )
        elif result.milestone == "death":
            await self._announce_player_death(
                result.session, result.story or result.text
            )
        await self._drain_game_outbound(actor_key=sender_key)
        if self.settings.daemons_enabled and not result.opt_out:
            await self._tick_player_daemons(result.session)

    async def _settle_before_tx(self) -> None:
        """Wait so phone retries / half-duplex RX can finish before we TX."""
        ms = int(getattr(self.settings, "reply_settle_ms", 0) or 0)
        if ms > 0:
            await asyncio.sleep(ms / 1000.0)

    async def _reply_result(self, sender_key: str, result: StepResult) -> None:
        session = result.session
        session.last_output = result.text
        session.last_story = result.story
        session.last_ui = result.ui
        if not self.companion:
            self.sessions.save(session)
            return

        await self._settle_before_tx()

        timer_prefix = TIMER_EMOJI if result.is_timer else ""
        story = result.story or (result.text if not result.ui else "")
        ui = result.ui or ""
        if not story and not ui and result.text:
            story = result.text

        parts = format_reply(
            story,
            ui,
            self.settings.max_chunk_bytes,
            self.settings.max_chunks,
            scene_emoji=result.scene_emoji,
            timer_prefix=timer_prefix,
        )
        ok = True
        sent_parts: list[str] = []
        if parts:
            self.stats["messages_out"] += 1
            ok = await self.companion.send_parts(sender_key, parts)
            if ok:
                sent_parts.extend(parts)

        if ok and sent_parts:
            self._record_send_quality(sender_key, True, sent_parts)
        else:
            self._record_send_quality(sender_key, False, None)

        session.last_send_failed = not ok
        if not ok:
            self.stats["ack_failures"] += 1
        self.sessions.save(session)
        self.write_runtime()

    async def quiet_loop(self) -> None:
        while not self.stop.is_set():
            try:
                sample = await asyncio.to_thread(self.quiet.poll_once)
                quiet_now = sample.is_quiet
                timeout = self.settings.offer_timeout_seconds
                hops = self.settings.local_max_hops
                self.access.note_mesh_state(quiet_now)

                # Free slots for players who stopped commanding (local + remote).
                for idle_key in self.access.expire_idle_actives(
                    self.settings.active_idle_seconds
                ):
                    logger.info("Freed idle active slot %s…", idle_key[:16])

                for pubkey, msg in self.access.expire_offer_if_needed(
                    timeout, mesh_open=quiet_now, local_max_hops=hops
                ):
                    await self._notify(pubkey, self._voiced(msg))

                if self.settings.safety_enabled:
                    if quiet_now:
                        if self.access.active and self.access.active.paused:
                            resume_msg = self.access.begin_resume_notice()
                            if resume_msg and self.access.active:
                                await self._notify(
                                    self.access.active.pubkey, self._voiced(resume_msg)
                                )
                        elif not self.access.active:
                            for pubkey, msg in self.access.maybe_offer_after_quiet(
                                timeout, mesh_open=True, local_max_hops=hops
                            ):
                                await self._notify(pubkey, self._voiced(msg))
                    else:
                        # Mesh above max tier: demote stale remote actives so locals can take slots.
                        # Recently active remotes keep playing through flaps (active_grace_seconds).
                        for demoted_key in list(
                            self.access.nonlocal_active_keys(local_max_hops=hops)
                        ):
                            if self._within_active_grace(demoted_key):
                                continue
                            demoted = self.access.actives.get(demoted_key)
                            if demoted is None:
                                continue
                            demoted_name = demoted.display_name
                            # Preserve busy-notice state across demotion.
                            q = QueueEntry(
                                pubkey=demoted_key,
                                display_name=demoted_name,
                                enqueued_at=time.time(),
                                notified_waiting=True,
                                busy_attempt_episode=demoted.busy_attempt_episode,
                                busy_notified_episode=demoted.busy_notified_episode,
                                busy_tries=demoted.busy_tries,
                                busy_last_try_at=demoted.busy_last_try_at,
                            )
                            self.access.queue.insert(0, q)
                            self.access.clear_active(demoted_key)
                            msg = self.access.begin_busy_notice_for_pubkey(demoted_key)
                            if msg:
                                ok = await self._notify(
                                    demoted_key, self.msg("paused", msg)
                                )
                                self.access.mark_busy_ack(demoted_key, ok)
                        if not self.access.active:
                            for pubkey, msg in self.access.maybe_offer_after_quiet(
                                timeout, mesh_open=False, local_max_hops=hops
                            ):
                                await self._notify(pubkey, self._voiced(msg))

                self._was_quiet = quiet_now
                self.write_runtime()
            except Exception:
                logger.exception("quiet loop error")
            poll = float(getattr(self.settings, "quiet_poll_seconds", 30) or 30)
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=poll)
            except asyncio.TimeoutError:
                pass

    async def config_watch_loop(self) -> None:
        while not self.stop.is_set():
            try:
                cfg = self.data_dir / "config.json"
                mtime = cfg.stat().st_mtime if cfg.exists() else 0.0
                if mtime and mtime != self._config_mtime:
                    raw = {}
                    try:
                        raw = json.loads(cfg.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, OSError):
                        logger.exception("config watch read failed")
                    actions = raw.get("admin_actions") if isinstance(raw, dict) else None
                    self._reload_settings_soft()
                    if isinstance(actions, list) and actions:
                        for pubkey, msg in self._apply_admin_actions(actions):
                            await self._notify(pubkey, msg)
                        self._clear_admin_actions()
                    if self._pending_radio_policy and self.companion:
                        self._pending_radio_policy = False
                        try:
                            await self.companion.apply_radio_policy()
                        except Exception:
                            logger.exception("apply_radio_policy failed")
                    while self._pending_adverts and self.companion:
                        flood = self._pending_adverts.pop(0)
                        try:
                            await self.companion.send_advert(flood=flood)
                        except Exception:
                            logger.exception("send_advert failed flood=%s", flood)
                    self.write_runtime()
            except Exception:
                logger.exception("config watch error")
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=CONFIG_WATCH_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def timer_loop(self) -> None:
        while not self.stop.is_set():
            try:
                allow_timers = (not self.settings.safety_enabled) or self.quiet.is_quiet or (
                    bool(self.access.actives) and self._active_is_local()
                )
                if allow_timers:
                    await self._tick_world_daemons()
                    for session in self.sessions.list_sessions():
                        if not session.sender_key or not session.alive:
                            continue
                        if (
                            self.settings.safety_enabled
                            and self.settings.single_player_enabled
                            and self.access.actives
                            and not self.access.is_active(session.sender_key)
                        ):
                            continue
                        due = self.game.apply_due_timers(session)
                        if due is not None:
                            self.sessions.save(due.session)
                            await self._reply_result(session.sender_key, due)
                            await self._drain_game_outbound(actor_key=session.sender_key)
                        if self.settings.daemons_enabled and self.access.is_active(
                            session.sender_key
                        ):
                            await self._tick_player_daemons(session)
            except Exception:
                logger.exception("timer loop error")
            self.write_runtime()
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass

    async def poll_loop(self) -> None:
        while not self.stop.is_set():
            if self.companion:
                await self.companion.poll_messages_once()
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass

    async def contact_sync_loop(self) -> None:
        while not self.stop.is_set():
            try:
                await self._sync_advert_contacts_once()
            except Exception:
                logger.exception("contact sync loop error")
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=CONTACT_SYNC_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def _try_connect_companion(self) -> bool:
        """Connect or reconnect game companion TCP. Never give up permanently."""
        if self.companion and self.companion.connected:
            return True
        if self.companion is None:
            self.companion = CompanionClient(
                self.settings, self.handle_message, companion_name=self.companion_name
            )
        else:
            self.companion.companion_name = self.companion_name
        try:
            await self.companion.connect()
            self._cleaned_contacts = False
            known = await self.companion.list_contact_pubkeys()
            self.sessions.migrate_truncated_keys(known)
            await self._cleanup_junk_contacts()
            logger.info(
                "Companion connected %s:%s name=%s content=%s",
                self.settings.meshcore_host,
                self.settings.meshcore_port,
                self.companion_name,
                self.game_data.id,
            )
            return True
        except Exception as exc:
            logger.warning(
                "Companion TCP connect failed (%s:%s): %s — retrying in %.0fs",
                self.settings.meshcore_host,
                self.settings.meshcore_port,
                type(exc).__name__,
                COMPANION_RECONNECT_SECONDS,
            )
            if self.companion:
                self.companion.connected = False
            return False

    async def companion_reconnect_loop(self) -> None:
        while not self.stop.is_set():
            try:
                if not (self.companion and self.companion.connected):
                    if await self._try_connect_companion():
                        await self._sync_advert_contacts_once()
                        self.write_runtime()
            except Exception:
                logger.exception("companion reconnect loop error")
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=COMPANION_RECONNECT_SECONDS)
            except asyncio.TimeoutError:
                pass

    def _exclude_keys(self) -> set[str]:
        self_key = ""
        if self.companion:
            self_key = self.companion.self_public_key or ""
        self_key = self_key or self.settings.adventurer_public_key
        return excluded_sync_keys(self_key=self_key)

    def _session_holder_keys(self) -> set[str]:
        keys: set[str] = set()
        for session in self.sessions.list_sessions():
            key = normalize_pubkey(session.sender_key)
            if len(key) == 64:
                keys.add(key)
        return keys

    async def _cleanup_junk_contacts(self) -> None:
        if not self.companion or self._cleaned_contacts:
            return
        excluded = self._exclude_keys()
        known = await self.companion.list_contact_pubkeys()
        for key in sorted(known & excluded):
            await self.companion.remove_contact(key)
        self._cleaned_contacts = True

    async def _prune_stale_contacts(self, advert_keys: set[str]) -> None:
        if not self.companion or not self.companion.connected:
            return
        keep = {normalize_pubkey(k) for k in advert_keys if len(normalize_pubkey(k)) == 64}
        keep |= self._session_holder_keys()
        keep |= self._exclude_keys()
        keep |= {normalize_pubkey(k) for k in self.access.actives}
        keep |= {normalize_pubkey(k) for k in self.access.local_players}
        known = await self.companion.list_contact_pubkeys()
        removed = 0
        for key in sorted(known):
            if key in keep:
                continue
            if await self.companion.remove_contact(key):
                removed += 1
        if removed:
            self.companion._table_full = False
            logger.info("Pruned %s stale contacts from companion", removed)

    async def _sync_advert_contacts_once(self) -> None:
        if not self.companion or not self.companion.connected:
            self.stats["contact_sync_error"] = "companion_disconnected"
            self.write_runtime()
            return
        if not self.companion.self_public_key:
            await self.companion._refresh_self_info()

        await self._cleanup_junk_contacts()

        known = await self.companion.list_contact_pubkeys()
        self.sessions.migrate_truncated_keys(known)

        advert_names = await asyncio.to_thread(self.contact_sync.fetch_advert_chat_names)
        excluded = self._exclude_keys()
        advert_keys = filter_player_keys(set(advert_names.keys()), excluded)
        self.stats["adverts_seen"] = len(advert_keys)
        self.stats["contact_sync_error"] = self.contact_sync.last_error
        if self.companion:
            n = int(getattr(self.companion, "_unresolved_senders", 0) or 0)
            self.stats["unresolved_senders"] = n
        if self.companion._table_full:
            self.stats["contact_sync_error"] = (
                (self.stats["contact_sync_error"] + "; " if self.stats["contact_sync_error"] else "")
                + "ERR_CODE_TABLE_FULL"
            )
        if self.contact_sync.last_error:
            logger.warning("contact sync: %s", self.contact_sync.last_error)
            # Never prune when the advert API failed — empty set would wipe contacts.
            self.write_runtime()
            return

        # Import-only: do not prune contacts absent from the freshest-N advert
        # window. A limited advert fetch previously wiped hundreds of contacts
        # and then re-pruned overheard nodes every sync cycle.
        if not advert_keys:
            self.write_runtime()
            return
        # If the companion radio is unresponsive, do not hammer it with adds —
        # the circuit breaker inside CompanionClient will clear on recovery (#1).
        if getattr(self.companion, "radio_paused", False):
            self.stats["contact_sync_error"] = (
                (self.stats["contact_sync_error"] + "; " if self.stats["contact_sync_error"] else "")
                + "RADIO_PAUSED"
            )
            self.write_runtime()
            return
        known = await self.companion.list_contact_pubkeys()
        to_add = keys_to_add(advert_keys, known)
        added_this_tick = 0
        for key in to_add:
            if added_this_tick >= MAX_ADDS_PER_SYNC:
                break
            nick = (advert_names.get(key) or "").strip()
            if self._looks_like_key(nick):
                nick = ""
            status = await self.companion.ensure_contact(key, nick)
            if status == "added":
                added_this_tick += 1
                self.stats["contacts_imported"] = int(self.stats.get("contacts_imported", 0)) + 1
                self.contact_sync.contacts_imported = int(self.stats["contacts_imported"])
                logger.info(
                    "Imported advert pubkey into companion contacts: %s… (%s)",
                    key[:16],
                    nick or "unnamed",
                )
        self.write_runtime()

    async def run(self) -> None:
        logger.info(
            "Starting zorcore data=%s content=%s@%s",
            self.data_dir,
            self.game_data.id,
            self.game_data.version,
        )
        await self._try_connect_companion()
        live = ""
        if self.companion and self.companion.self_public_key:
            live = self.companion.self_public_key
        live = live or await asyncio.to_thread(self._sync_adventurer_key_from_api)
        if live:
            self._persist_adventurer_key(live)

        self.write_runtime()

        tasks = [
            asyncio.create_task(self.timer_loop()),
            asyncio.create_task(self.poll_loop()),
            asyncio.create_task(self.contact_sync_loop()),
            asyncio.create_task(self.companion_reconnect_loop()),
            asyncio.create_task(self.quiet_loop()),
            asyncio.create_task(self.config_watch_loop()),
        ]
        await self.stop.wait()
        for t in tasks:
            t.cancel()
        if self.companion:
            await self.companion.disconnect()
        self.write_runtime()
        logger.info("Stopped zorcore")


def main() -> None:
    data = Path(os.environ.get("OPENHOP_PLUGIN_DATA", ".")).resolve()
    data.mkdir(parents=True, exist_ok=True)
    cfg = data / "config.json"
    if not cfg.exists():
        bundled = Path(__file__).resolve().parent.parent / "config.default.json"
        if bundled.exists():
            cfg.write_text(bundled.read_text(encoding="utf-8"), encoding="utf-8")

    app = PluginApp(data)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _stop(*_: object) -> None:
        app.stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _stop)
        except Exception:
            pass

    try:
        loop.run_until_complete(app.run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
