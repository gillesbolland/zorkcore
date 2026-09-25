"""Game engine: step commands, timers, and content scripts."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Any

from zorcore.engine.parser import parse
from zorcore.engine.scripts import ScriptResult, _flag_ok, apply_effects, run_scripts
from zorcore.engine.sessions import ActiveTimer, Session
from zorcore.engine.verbs import VerbTable, load_verb_table
from zorcore.engine.world import ExitDest, RoomDef, World
from zorcore.engine.batch import (
    collect_inventory_targets,
    collect_take_targets,
    remember_objects,
)
from zorcore.text import normalize_command

if TYPE_CHECKING:
    from zorcore.content_loader import DarkPolicy, GameData
else:
    DarkPolicy = None  # set via from_game_data / __init__

META_ALWAYS = {"hello?", "new", "help", "again", "quit", "save", "restore", "score", "version", "brief", "verbose"}
TIMER_EMOJI = "⏱️"

# Classic stub replies for catalogued verbs with no deeper handler.
VERB_STUBS: dict[str, str] = {
    "yell": "Aaaarrrrgggghhhh!",
    "scream": "Aaaarrrrgggghhhh!",
    "jump": "You jump on the spot.",
    "dig": "Digging here would be pointless.",
    "smell": "You smell nothing unexpected.",
    "listen": "You hear nothing special.",
    "drink": "You can't drink that.",
    "swim": "You can't swim here.",
    "wake": "You aren't asleep.",
    "knock": "Nobody answers.",
    "kick": "That would be unwise.",
    "break": "You can't break that.",
    "cut": "You lack the tools — or the will.",
    "tie": "You can't tie that.",
    "untie": "It isn't tied.",
    "lock": "You can't lock that.",
    "unlock": "You can't unlock that.",
    "wave": "You wave your hand.",
    "rub": "You feel nothing special.",
    "melt": "You can't melt that.",
    "pray": "If you pray enough, your prayers may be answered.",
    "curse": "Such language in a high-class establishment like this!",
    "repent": "It could very well be too late!",
    "diagnose": "You are in perfect health.",
    "find": "I can't help you there.",
    "where": "I can't help you there.",
    "blow": "You blow, and nothing happens.",
    "inflate": "How?",
    "deflate": "How?",
    "board": "You can't board that.",
    "disembark": "You aren't in a vehicle.",
    "throw": "You can't throw that usefully.",
    "give": "There is no one to give it to.",
    "eat": "I don't think that would be wise.",
    "fill": "You can't fill that.",
    "pour": "You can't pour that.",
    "push": "Nothing happens.",
    "ring": "There is no ring.",
    "skip": "Not now.",
    "tell": "Talk to whom?",
    "follow": "You can't follow that.",
    "answer": "No one answers.",
    "say": "Talk is cheap.",
    "count": "There are a number of things here.",
    "touch": "You feel nothing special.",
    "search": "You find nothing special.",
    "climb": "You can't climb that.",
    "cross": "You can't cross that.",
    "launch": "You can't launch that here.",
    "land": "You aren't flying.",
    "back": "You can't go back that way.",
    "time": "Time passes.",
    "info": "This is ZorCore — a MeshCore telling of Dungeon.",
    "zork": "At your service!",
    "frobozz": "A magic word — nothing happens.",
    "odysseus": "Nothing happens.",
    "ulysses": "Nothing happens.",
    "plugh": "Nothing happens.",
    "xyzzy": "Nothing happens.",
    "win": "Naturally!",
    "chomp": "Chomp, chomp!",
    "well": "Well, well…",
    "exorcise": "What a concept!",
    "temple": "Nothing happens.",
    "treasure": "Nothing happens.",
    "geronimo": "Nothing happens.",
    "blast": "Nothing happens.",
    "brush": "Nothing happens.",
    "granite": "Nothing happens.",
    "mumble": "You mumble to yourself.",
    "dungeon": "This is it.",
    "plug": "This has no effect.",
    "jargon": "Such language!",
    "superbrief": "Superbrief descriptions.",
    "unbrief": "Brief descriptions.",
    "unsuperbrief": "Verbose descriptions.",
    "script": "Scripting is not available on MeshCore.",
    "unscript": "Scripting is not available on MeshCore.",
}

# World daemons act on shared state, not on any one player's session.
_WORLD_SESSION = Session(sender_key="", room_id="")

SHORT_HELP = (
    "DM `new game` to start. "
    "Try: look, n/s/e/w, take all, inventory, examine, save, quit. "
    "`?` repeats my last reply · `again`/`g` redos your last command. "
    f"{TIMER_EMOJI} means act soon."
)


@dataclass
class StepResult:
    text: str
    session: Session
    milestone: str | None = None
    resend_only: bool = False
    story: str = ""
    ui: str = ""
    is_timer: bool = False
    scene_emoji: str = ""
    opt_out: bool = False

    def __post_init__(self) -> None:
        if not self.story and not self.ui and self.text:
            self.story = self.text
        if not self.text:
            parts = [p for p in (self.story, self.ui) if p]
            self.text = "\n".join(parts)


class Game:
    def __init__(
        self,
        world: World,
        *,
        verbs: VerbTable | None = None,
        scripts: list | None = None,
        dark: Any = None,
        initial_flags: dict[str, bool] | None = None,
        content_id: str = "",
        content_version: str = "",
        now: Callable[[], float] | None = None,
        world_state: Any = None,
        rand: Callable[[], float] | None = None,
        daemons: list | None = None,
        checkpoint_store: Any = None,
    ) -> None:
        self.world = world
        self.verbs = verbs or load_verb_table({})
        self.scripts = list(scripts or [])
        if dark is None:
            from zorcore.content_loader import DarkPolicy as _DarkPolicy

            dark = _DarkPolicy()
        self.dark = dark
        self.initial_flags = dict(initial_flags or {})
        self.content_id = content_id
        self.content_version = content_version
        self.now = now or time.time
        self.world_state = world_state
        self.daemons = list(daemons or [])
        self.checkpoint_store = checkpoint_store
        if rand is None:
            import random as _random

            rand = _random.random
        self.rand = rand
        # Outbound side-effects collected during a step, drained by the plugin.
        self._pending_broadcasts: list[str] = []
        self._pending_room_notices: list[dict[str, str]] = []

    def drain_outbound(self) -> tuple[list[str], list[dict[str, str]]]:
        """Take broadcasts / room notices produced since the last drain."""
        bc = list(self._pending_broadcasts)
        rn = list(self._pending_room_notices)
        self._pending_broadcasts.clear()
        self._pending_room_notices.clear()
        return bc, rn

    @classmethod
    def from_game_data(
        cls,
        data: GameData,
        now: Callable[[], float] | None = None,
        *,
        world_state: Any = None,
        rand: Callable[[], float] | None = None,
        checkpoint_store: Any = None,
    ) -> Game:
        return cls(
            data.world,
            verbs=data.verbs,
            scripts=data.scripts,
            dark=data.dark,
            initial_flags=data.initial_flags,
            content_id=data.id,
            content_version=data.version,
            now=now,
            world_state=world_state,
            rand=rand,
            daemons=getattr(data, "daemons", []),
            checkpoint_store=checkpoint_store,
        )

    def short_help(self) -> str:
        return SHORT_HELP

    def session_matches_content(self, session: Session) -> bool:
        if not self.content_id:
            return True
        return (
            session.content_id == self.content_id
            and session.content_version == self.content_version
        )

    def new_session(self, sender_key: str, display_name: str = "") -> Session:
        room_objects = {rid: list(r.objects) for rid, r in self.world.rooms.items()}
        open_objects = {oid for oid, o in self.world.objects.items() if o.start_open}
        containers = {
            oid: list(o.contains)
            for oid, o in self.world.objects.items()
            if o.container
        }
        # Objects seeded only inside containers should not also sit in rooms.
        contained = {c for contents in containers.values() for c in contents}
        for rid, objs in room_objects.items():
            room_objects[rid] = [o for o in objs if o not in contained]
        session = Session(
            sender_key=sender_key,
            room_id=self.world.start_room,
            visited=[self.world.start_room],
            room_objects=room_objects,
            display_name=display_name,
            flags=dict(self.initial_flags),
            open_objects=open_objects,
            lit_objects=set(),
            containers=containers,
            content_id=self.content_id,
            content_version=self.content_version,
            brief_mode=False,
        )
        result = self._compose_start(session)
        session.last_output = result.text
        session.last_story = result.story
        session.last_ui = result.ui
        return session

    def _compose_start(self, session: Session) -> StepResult:
        story, ui, emoji = self._describe_room_parts(session, force_full=True)
        welcome = (self.world.welcome or "").strip()
        # Classic packs set welcome == start-room description; don't double it
        # or MeshCore splits into a near-duplicate 1/2… / .2/2 pair.
        if welcome and not (story or "").startswith(welcome):
            story = f"{welcome}\n\n{story}" if story else welcome
        text = "\n".join(p for p in (story, ui) if p)
        return StepResult(text=text, session=session, story=story, ui=ui, scene_emoji=emoji)

    def _object_gives_light(self, session: Session, oid: str) -> bool:
        obj = self.world.objects.get(oid)
        if not obj or not obj.provides_light:
            return False
        if obj.light_always:
            return True
        return oid in session.lit_objects

    def has_light(self, session: Session) -> bool:
        for oid in session.inventory:
            if self._object_gives_light(session, oid):
                return True
        return False

    def _visible_object_ids(self, session: Session) -> list[str]:
        ids = list(session.room_objects.get(session.room_id, []))
        # Shared NPCs are not in room_objects; every player sees the same ones.
        if self.world_state is not None:
            ids.extend(self.world_state.npcs_in_room(session.room_id))
        ids.extend(session.inventory)
        for oid in list(ids):
            if oid in session.open_objects or (
                oid in self.world.objects
                and self.world.objects[oid].container
                and oid in session.open_objects
            ):
                ids.extend(session.containers.get(oid, []))
        return ids

    def _describe_room_parts(
        self, session: Session, force_full: bool = False
    ) -> tuple[str, str, str]:
        room = self.world.rooms[session.room_id]
        if room.dark and not self.has_light(session):
            return self.dark.message, "", room.emoji
        # Verbose by default: only brief when the player explicitly asked for it.
        use_brief = (session.brief_mode is True) and (not force_full)
        story_lines = [room.brief if use_brief else room.description]
        objs = session.room_objects.get(session.room_id, [])
        visible = []
        for oid in objs:
            obj = self.world.objects.get(oid)
            if not obj or obj.fixture:
                continue
            # Shared NPCs are listed via world_state presence, not as loot.
            if self.world_state is not None and oid in self.world_state.npcs:
                if not self.world_state.is_alive(oid):
                    continue
                continue
            visible.append(obj.name)
        if visible and not use_brief:
            story_lines.append("You see: " + ", ".join(visible) + ".")
        for line in self._npc_presence_lines(session):
            story_lines.append(line)
        ui_lines: list[str] = []
        exits = ", ".join(self.traversable_exit_labels(session, room))
        if exits and not use_brief:
            ui_lines.append(f"Exits: {exits}.")
        return "\n".join(story_lines), "\n".join(ui_lines), room.emoji

    def _npc_presence_lines(self, session: Session) -> list[str]:
        """Shared NPCs standing in this room, seen the same way by every player."""
        if self.world_state is None:
            return []
        out: list[str] = []
        for npc_id in self.world_state.npcs_in_room(session.room_id):
            line = self.world_state.presence_line(npc_id)
            if line:
                out.append(line)
        return out

    def run_enter_scripts(self, session: Session):
        """Fire `trigger: "enter"` scripts for the room just entered."""
        if not self.scripts:
            return None
        return run_scripts(
            self.scripts,
            session=session,
            world=self.world,
            verb="",
            trigger="enter",
            world_state=self.world_state,
            rand=self.rand,
        )

    def hello(self, session: Session) -> StepResult:
        if session.last_output:
            return StepResult(
                session.last_output,
                session,
                resend_only=True,
                story=session.last_story or session.last_output,
                ui=session.last_ui,
            )
        text = self.short_help()
        return StepResult(text, session, resend_only=True, ui=text)

    def apply_due_timers(self, session: Session) -> StepResult | None:
        if not session.alive:
            return None
        now = self.now()
        due = [t for t in session.active_timers if t.deadline_unix <= now]
        if not due:
            return None
        due.sort(key=lambda t: t.deadline_unix)
        timer = due[0]
        session.active_timers = [t for t in session.active_timers if t.id != timer.id]
        text = timer.message
        if timer.death:
            session.alive = False
            session.active_timers.clear()
        session.last_output = text
        session.last_story = text
        session.last_ui = ""
        milestone = "death" if timer.death else "timer"
        return StepResult(text, session, milestone=milestone, story=text, is_timer=True)

    def apply_world_daemons(
        self,
        *,
        min_interval_seconds: float = 60.0,
    ) -> tuple[list[str], list[dict[str, str]]]:
        """Tick scope=world daemons against shared state. Returns (broadcasts, notices)."""
        if self.world_state is None or not self.daemons:
            return [], []
        due = self.world_state.due_daemons(
            self.daemons, scope="world", min_interval_seconds=min_interval_seconds
        )
        broadcasts: list[str] = []
        notices: list[dict[str, str]] = []
        for d in due:
            chance = float(d.get("chance", 1.0) or 1.0)
            if chance < 1.0 and self.rand() > chance:
                continue
            if not self._world_when_ok(d.get("when")):
                continue
            res = ScriptResult()
            apply_effects(
                _WORLD_SESSION,
                self.world,
                list(d.get("effects") or []),
                res,
                world_state=self.world_state,
                rand=self.rand,
            )
            broadcasts.extend(res.broadcasts)
            notices.extend(res.room_notices)
        return broadcasts, notices

    def apply_respawns(self, npc_specs: dict[str, Any]) -> list[str]:
        """Revive NPCs whose cooldown elapsed. Thief waits until his stash is empty."""
        if self.world_state is None:
            return []
        revived: list[str] = []
        now = self.now()
        for npc_id, spec in (npc_specs or {}).items():
            row = self.world_state.npcs.get(npc_id)
            if not row or row.get("alive"):
                continue
            secs = float(spec.get("respawn_seconds") or 0)
            if not secs:
                continue
            dead_since = row.get("dead_since")
            if not dead_since or (now - float(dead_since)) < secs:
                continue
            if spec.get("respawn_requires_empty_stash") and not self.world_state.stash_empty(
                npc_id
            ):
                continue
            self.world_state.revive_npc(npc_id)
            revived.append(npc_id)
        return revived

    def apply_player_daemons(
        self,
        session: Session,
        *,
        min_interval_seconds: float = 60.0,
    ) -> ScriptResult | None:
        """Tick scope=player daemons for one active session."""
        if self.world_state is None or not self.daemons:
            return None
        due = self.world_state.due_daemons(
            self.daemons,
            scope="player",
            min_interval_seconds=min_interval_seconds,
            key_prefix=f"{session.sender_key[:16]}:",
        )
        if not due:
            return None
        res = ScriptResult()
        for d in due:
            chance = float(d.get("chance", 1.0) or 1.0)
            if chance < 1.0 and self.rand() > chance:
                continue
            if not _flag_ok(session, d.get("when"), world_state=self.world_state):
                continue
            apply_effects(
                session,
                self.world,
                list(d.get("effects") or []),
                res,
                world_state=self.world_state,
                rand=self.rand,
            )
            res.matched = True
        return res if res.matched else None

    def _world_when_ok(self, when: Any) -> bool:
        if not when:
            return True
        ws = self.world_state
        if ws is None:
            return False
        npc_alive = when.get("npc_alive")
        if npc_alive is not None and not ws.is_alive(str(npc_alive)):
            return False
        npc_dead = when.get("npc_dead")
        if npc_dead is not None and ws.is_alive(str(npc_dead)):
            return False
        return True

    def clear_room_timer(self, session: Session, timer_id: str | None = None) -> None:
        tid = timer_id or self.dark.timer_id
        session.active_timers = [t for t in session.active_timers if t.id != tid]

    def _resolve_exit(self, session: Session, dest: ExitDest) -> tuple[str | None, str | None]:
        """Return (room_id, error_message)."""
        if dest.nexit is not None:
            return None, dest.nexit or "You can't go that way."
        if dest.flag:
            if not session.flags.get(dest.flag, False):
                return None, dest.fail or "You can't go that way."
            return dest.room, None
        if dest.room:
            return dest.room, None
        return None, "You can't go that way."

    def traversable_exit_labels(self, session: Session, room: RoomDef) -> list[str]:
        """Directions that currently lead somewhere for this player (no nexits / locked cexits)."""
        labels: list[str] = []
        for direction, dest in room.exits.items():
            room_id, _err = self._resolve_exit(session, dest)
            if room_id:
                labels.append(direction)
        return sorted(labels)

    def _enter_room(self, session: Session, room_id: str) -> tuple[list[str], list[str], str]:
        story: list[str] = []
        ui: list[str] = []
        room = self.world.rooms[room_id]
        session.room_id = room_id
        self.clear_room_timer(session)
        if room_id not in session.visited:
            session.visited.append(room_id)
            if room.score_on_enter:
                session.score += room.score_on_enter
                ui.append(f"[+{room.score_on_enter} points]")
        else:
            session.visited.append(room_id)
        enter = self.run_enter_scripts(session)
        if enter is not None and enter.matched:
            story.extend(enter.story)
            ui.extend(enter.ui)
            self._pending_broadcasts.extend(enter.broadcasts)
            self._pending_room_notices.extend(enter.room_notices)
        s, u, emoji = self._describe_room_parts(session)
        if s:
            story.append(s)
        if u:
            ui.append(u)
        if room.dark and not self.has_light(session):
            t = room.timer_on_enter
            seconds = t.seconds if t else self.dark.timer_seconds
            message = t.message if t else self.dark.timer_message
            death = t.death if t else self.dark.timer_death
            tid = t.id if t else self.dark.timer_id
            session.active_timers.append(
                ActiveTimer(
                    id=tid,
                    deadline_unix=self.now() + seconds,
                    message=message,
                    death=death,
                )
            )
            ui.append(f"{TIMER_EMOJI} {self.dark.timer_hint} ~{int(seconds)}s.")
        return story, ui, emoji

    def _find(self, session: Session, token: str, scope: str = "here") -> object:
        if scope == "inv":
            return self.world.find_object(token, session.inventory)
        if scope == "room":
            room_ids = list(session.room_objects.get(session.room_id, []))
            if self.world_state is not None:
                room_ids.extend(self.world_state.npcs_in_room(session.room_id))
            return self.world.find_object(token, room_ids)
        return self.world.find_object(token, self._visible_object_ids(session))

    def step(self, session: Session, command_text: str) -> StepResult:
        raw = normalize_command(command_text)
        cmd = parse(raw, self.verbs)

        due = self.apply_due_timers(session)
        if due is not None:
            if cmd.verb in META_ALWAYS:
                pass
            elif not session.alive and cmd.verb != "new":
                return due

        if cmd.verb == "hello?":
            return self.hello(session)
        if cmd.verb == "new" and cmd.noun == "game":
            fresh = self.new_session(session.sender_key, session.display_name)
            return StepResult(
                fresh.last_output,
                fresh,
                milestone="start",
                story=fresh.last_story,
                ui=fresh.last_ui,
                scene_emoji=self.world.rooms[fresh.room_id].emoji,
            )
        if cmd.verb == "help":
            text = self.short_help()
            session.last_output = text
            session.last_ui = text
            session.last_story = ""
            return StepResult(text, session, ui=text)

        if cmd.verb == "again":
            prev = (session.last_command or "").strip()
            if not prev:
                text = "Nothing to repeat."
                session.last_output = text
                return StepResult(text, session, ui=text)
            return self.step(session, prev)

        if cmd.verb in {"quit", "q", "bye"}:
            text = f"Farewell. Score: {session.score}. Moves: {session.moves}."
            session.opted_out = True
            session.last_output = text
            return StepResult(text, session, ui=text, opt_out=True, milestone="quit")

        if cmd.verb == "save":
            if self.checkpoint_store is not None:
                self.checkpoint_store.save_checkpoint(session)
            text = "Saved."
            session.last_output = text
            return StepResult(text, session, ui=text)

        if cmd.verb == "restore":
            if self.checkpoint_store is None or not self.checkpoint_store.has_checkpoint(
                session.sender_key
            ):
                text = "No saved game."
                session.last_output = text
                return StepResult(text, session, ui=text)
            loaded = self.checkpoint_store.load_checkpoint(session.sender_key)
            if loaded is None:
                text = "No saved game."
                session.last_output = text
                return StepResult(text, session, ui=text)
            loaded.sender_key = session.sender_key
            loaded.display_name = session.display_name or loaded.display_name
            loaded.opted_out = False
            s, u, scene_emoji = self._describe_room_parts(loaded, force_full=True)
            story = "Restored.\n" + (s or "")
            text = "\n".join(p for p in (story, u) if p)
            loaded.last_output = text
            loaded.last_story = story
            loaded.last_ui = u or ""
            return StepResult(
                text, loaded, story=story, ui=u or "", scene_emoji=scene_emoji, milestone="restore"
            )

        if cmd.verb == "brief":
            session.brief_mode = True
            text = "Brief descriptions."
            session.last_output = text
            return StepResult(text, session, ui=text)
        if cmd.verb == "verbose":
            session.brief_mode = False
            text = "Verbose descriptions."
            session.last_output = text
            return StepResult(text, session, ui=text)
        if cmd.verb == "version":
            from zorcore import __version__

            text = f"ZorCore {__version__} · content {self.content_id} {self.content_version}"
            session.last_output = text
            return StepResult(text, session, ui=text)

        if not session.alive:
            text = "You have died. DM `new game` to begin again."
            session.last_output = text
            return StepResult(text, session, story=text)

        if self.content_id and not self.session_matches_content(session):
            text = "Game data changed. DM `new game` to continue."
            session.last_output = text
            return StepResult(text, session, ui=text)

        if not cmd.verb:
            text = "I beg your pardon?"
            session.last_output = text
            return StepResult(text, session, ui=text)

        session.moves += 1
        story_lines: list[str] = []
        ui_lines: list[str] = []
        scene_emoji = ""
        is_timer = False
        milestone: str | None = None

        obj = self._find(session, cmd.noun) if cmd.noun else None
        script = run_scripts(
            self.scripts,
            session=session,
            world=self.world,
            verb=cmd.verb,
            noun=cmd.noun,
            indirect=cmd.indirect,
            object_id=obj.id if obj else None,
            world_state=self.world_state,
            rand=self.rand,
        )
        if script.matched:
            story_lines.extend(script.story)
            ui_lines.extend(script.ui)
            self._pending_broadcasts.extend(script.broadcasts)
            self._pending_room_notices.extend(script.room_notices)
            milestone = script.milestone
            if script.teleported_to:
                s, u, scene_emoji = self._enter_room(session, script.teleported_to)
                story_lines.extend(s)
                ui_lines.extend(u)
                return self._finish(
                    session, story_lines, ui_lines, scene_emoji, is_timer, milestone, raw
                )
            if script.stop:
                return self._finish(
                    session, story_lines, ui_lines, scene_emoji, is_timer, milestone, raw
                )

        if cmd.verb == "look":
            s, u, scene_emoji = self._describe_room_parts(session, force_full=True)
            if s:
                story_lines.append(s)
            if u:
                ui_lines.append(u)
        elif cmd.verb == "score":
            ui_lines.append(f"Score: {session.score}. Moves: {session.moves}.")
        elif cmd.verb == "inventory":
            if not session.inventory:
                ui_lines.append("You are empty-handed.")
            else:
                names = [self.world.objects[o].name for o in session.inventory if o in self.world.objects]
                ui_lines.append("You are carrying: " + ", ".join(names) + ".")
        elif cmd.verb == "wait":
            story_lines.append("Time passes.")
        elif cmd.verb == "go":
            room = self.world.rooms[session.room_id]
            dest = room.exits.get(cmd.noun)
            if not dest:
                ui_lines.append("You can't go that way.")
            elif (
                session.room_id == "carou"
                and dest.flag == "carousel_flip"
                and not session.flags.get("carousel_flip", False)
            ):
                # MDL CAROUSEL-EXIT: spin to a random CEXIT destination.
                story_lines.append(
                    "Unfortunately, it is impossible to tell directions in here."
                )
                candidates = [
                    d.room
                    for d in room.exits.values()
                    if d.room and d.flag == "carousel_flip"
                ]
                if candidates:
                    idx = int(self.rand() * len(candidates)) % len(candidates)
                    s_lines, u_lines, scene_emoji = self._enter_room(
                        session, candidates[idx]
                    )
                    story_lines.extend(s_lines)
                    ui_lines.extend(u_lines)
                    if any(TIMER_EMOJI in x for x in u_lines):
                        is_timer = True
                else:
                    ui_lines.append("You can't go that way.")
            else:
                room_id, err = self._resolve_exit(session, dest)
                if err:
                    ui_lines.append(err)
                elif room_id:
                    s_lines, u_lines, scene_emoji = self._enter_room(session, room_id)
                    story_lines.extend(s_lines)
                    ui_lines.extend(u_lines)
                    if any(TIMER_EMOJI in x for x in u_lines):
                        is_timer = True
        elif cmd.verb == "take":
            npc_ids = set()
            if self.world_state is not None:
                npc_ids = set(self.world_state.npcs.keys())
            targets, missing = collect_take_targets(
                session, self.world, cmd, npc_ids=npc_ids
            )
            if cmd.noun in {"it", "them"} and not targets and not missing:
                ui_lines.append(f"I don't know what you mean by '{cmd.noun}'.")
            elif not targets and not missing:
                if cmd.batch:
                    ui_lines.append("I couldn't find anything.")
                else:
                    ui_lines.append("You don't see that here.")
            else:
                for tok in missing:
                    story_lines.append(f"{tok}: You don't see that here.")
                taken: list[str] = []
                room_objs = session.room_objects.get(session.room_id, [])
                for oid in targets:
                    obj = self.world.objects.get(oid)
                    if not obj or not obj.takeable:
                        story_lines.append(f"{obj.name if obj else oid}: You can't take that.")
                        continue
                    if oid in room_objs:
                        room_objs.remove(oid)
                        session.room_objects[session.room_id] = room_objs
                    else:
                        for contents in session.containers.values():
                            if oid in contents:
                                contents.remove(oid)
                                break
                    session.inventory.append(oid)
                    taken.append(oid)
                    story_lines.append(f"{obj.name}: Taken.")
                    if obj.score_on_take:
                        session.score += obj.score_on_take
                        ui_lines.append(f"[+{obj.score_on_take} points]")
                    if self._object_gives_light(session, oid):
                        self.clear_room_timer(session)
                remember_objects(session, taken)
        elif cmd.verb == "drop":
            if cmd.prep == "in" and cmd.indirect:
                put_milestone = self._put_held(session, cmd, story_lines, ui_lines)
                if put_milestone:
                    milestone = put_milestone
            else:
                targets, missing = collect_inventory_targets(session, self.world, cmd)
                if cmd.noun in {"it", "them"} and not targets and not missing:
                    ui_lines.append(f"I don't know what you mean by '{cmd.noun}'.")
                elif not targets and not missing:
                    if cmd.batch:
                        ui_lines.append("You are empty-handed.")
                    else:
                        ui_lines.append("You aren't carrying that.")
                else:
                    for tok in missing:
                        story_lines.append(f"{tok}: You aren't carrying that.")
                    dropped: list[str] = []
                    for oid in targets:
                        if oid not in session.inventory:
                            continue
                        obj = self.world.objects[oid]
                        session.inventory.remove(oid)
                        session.room_objects.setdefault(session.room_id, []).append(oid)
                        dropped.append(oid)
                        story_lines.append(f"{obj.name}: Dropped.")
                    remember_objects(session, dropped)
                    room = self.world.rooms[session.room_id]
                    if room.dark and not self.has_light(session):
                        t = room.timer_on_enter
                        seconds = t.seconds if t else self.dark.timer_seconds
                        message = t.message if t else self.dark.timer_message
                        death = t.death if t else self.dark.timer_death
                        tid = t.id if t else self.dark.timer_id
                        if not any(x.id == tid for x in session.active_timers):
                            session.active_timers.append(
                                ActiveTimer(
                                    id=tid,
                                    deadline_unix=self.now() + seconds,
                                    message=message,
                                    death=death,
                                )
                            )
                            ui_lines.append(f"{TIMER_EMOJI} The darkness presses in…")
                            is_timer = True
        elif cmd.verb == "look_under" or (cmd.verb == "look_prep" and cmd.look_mode == "under"):
            obj = self._find(session, cmd.noun)
            if not obj:
                story_lines.append("You see nothing special.")
            elif obj.id == "rug" and not session.flags.get("rug_moved") and not session.flags.get(
                "trap_door"
            ):
                story_lines.append("Underneath the rug is a closed trap door.")
                remember_objects(session, [obj.id])
            elif obj.id == "leave" or "leaf" in (obj.id or "") or "leaves" in " ".join(obj.aliases):
                if session.room_id == "clear" and not session.flags.get("key_flag"):
                    story_lines.append("Underneath the pile of leaves is a grating.")
                else:
                    story_lines.append("You see nothing special.")
                remember_objects(session, [obj.id])
            else:
                story_lines.append("You see nothing special.")
                remember_objects(session, [obj.id])
        elif cmd.verb == "look_prep":
            story_lines.append("You see nothing special.")
        elif cmd.verb == "examine":
            tokens = list(cmd.nouns) if len(cmd.nouns) > 1 else ([cmd.noun] if cmd.noun else [])
            if cmd.noun in {"it", "them"}:
                from zorcore.engine.batch import expand_pronoun

                tokens = expand_pronoun(cmd.noun, session)
                if not tokens:
                    story_lines.append(f"I don't know what you mean by '{cmd.noun}'.")
                    tokens = []
            seen: list[str] = []
            for tok in tokens or []:
                if tok in self.world.objects and (
                    tok in session.inventory
                    or tok in session.room_objects.get(session.room_id, [])
                    or tok in self._visible_object_ids(session)
                ):
                    obj = self.world.objects[tok]
                else:
                    obj = self._find(session, tok)
                if not obj:
                    story_lines.append(f"{tok}: You see nothing special.")
                else:
                    story_lines.append(obj.description)
                    seen.append(obj.id)
            if seen:
                remember_objects(session, seen)
            elif not tokens and not story_lines:
                story_lines.append("You see nothing special.")
        elif cmd.verb == "read":
            obj = self._find(session, cmd.noun)
            if not obj:
                ui_lines.append("You don't see that here.")
            elif obj.readable:
                story_lines.append(obj.readable)
            else:
                story_lines.append(obj.description)
            if obj:
                remember_objects(session, [obj.id])
        elif cmd.verb == "open":
            obj = self._find(session, cmd.noun)
            if not obj:
                ui_lines.append("You don't see that here.")
            elif not obj.openable:
                ui_lines.append(f"You can't open the {obj.name}.")
            elif obj.id in session.open_objects:
                ui_lines.append("Already open.")
            else:
                session.open_objects.add(obj.id)
                story_lines.append(f"Opened: {obj.name}.")
                remember_objects(session, [obj.id])
                if obj.id in session.containers and session.containers[obj.id]:
                    names = [
                        self.world.objects[i].name
                        for i in session.containers[obj.id]
                        if i in self.world.objects
                    ]
                    if names:
                        story_lines.append("Inside: " + ", ".join(names) + ".")
        elif cmd.verb == "close":
            obj = self._find(session, cmd.noun)
            if not obj:
                ui_lines.append("You don't see that here.")
            elif not obj.openable:
                ui_lines.append(f"You can't close the {obj.name}.")
            elif obj.id not in session.open_objects:
                ui_lines.append("Already closed.")
            else:
                session.open_objects.discard(obj.id)
                story_lines.append(f"Closed: {obj.name}.")
                remember_objects(session, [obj.id])
        elif cmd.verb == "light":
            obj = self.world.find_object(cmd.noun, session.inventory + session.room_objects.get(session.room_id, []))
            if not obj:
                ui_lines.append("You don't have that.")
            elif not obj.provides_light:
                ui_lines.append(f"You can't light the {obj.name}.")
            elif obj.id in session.lit_objects:
                ui_lines.append("Already lit.")
            else:
                session.lit_objects.add(obj.id)
                story_lines.append(f"Lit: {obj.name}.")
                remember_objects(session, [obj.id])
                self.clear_room_timer(session)
        elif cmd.verb == "extinguish":
            obj = self.world.find_object(cmd.noun, session.inventory)
            if not obj:
                ui_lines.append("You aren't carrying that.")
            elif obj.id not in session.lit_objects:
                ui_lines.append("It isn't lit.")
            else:
                session.lit_objects.discard(obj.id)
                story_lines.append(f"Extinguished: {obj.name}.")
                remember_objects(session, [obj.id])
                room = self.world.rooms[session.room_id]
                if room.dark and not self.has_light(session):
                    ui_lines.append(f"{TIMER_EMOJI} {self.dark.timer_hint}")
                    is_timer = True
                    session.active_timers.append(
                        ActiveTimer(
                            id=self.dark.timer_id,
                            deadline_unix=self.now() + self.dark.timer_seconds,
                            message=self.dark.timer_message,
                            death=self.dark.timer_death,
                        )
                    )
        elif cmd.verb == "move":
            obj = self._find(session, cmd.noun, "room")
            if not obj:
                ui_lines.append("You don't see that here.")
            else:
                story_lines.append(f"Moving the {obj.name} doesn't help.")
        elif cmd.verb == "put":
            put_milestone = self._put_held(session, cmd, story_lines, ui_lines)
            if put_milestone:
                milestone = put_milestone
        elif cmd.verb == "attack":
            target = self._find(session, cmd.noun, "room")
            weapon = (
                self.world.find_object(cmd.indirect, session.inventory)
                if cmd.indirect
                else None
            )
            if not target:
                ui_lines.append("Attack what?")
            elif not cmd.indirect:
                ui_lines.append("What do you want to attack with?")
            elif not weapon or not weapon.weapon:
                ui_lines.append("You need a weapon.")
            else:
                story_lines.append(f"You attack the {target.name}, but nothing happens.")
                remember_objects(session, [target.id])
        elif cmd.verb in VERB_STUBS:
            story_lines.append(VERB_STUBS[cmd.verb])
        else:
            stub = VERB_STUBS.get(cmd.verb)
            if stub:
                story_lines.append(stub)
            else:
                ui_lines.append("I don't understand that.")

        return self._finish(session, story_lines, ui_lines, scene_emoji, is_timer, milestone, raw)

    def _put_held(
        self,
        session: Session,
        cmd: Any,
        story_lines: list[str],
        ui_lines: list[str],
    ) -> str | None:
        held_ids, missing = collect_inventory_targets(session, self.world, cmd)
        target = self._find(session, cmd.indirect) if cmd.indirect else None
        milestone: str | None = None
        if cmd.noun in {"it", "them"} and not held_ids and not missing:
            ui_lines.append(f"I don't know what you mean by '{cmd.noun}'.")
            return None
        for tok in missing:
            story_lines.append(f"{tok}: You aren't carrying that.")
        if not held_ids:
            if not missing:
                ui_lines.append("You aren't carrying that.")
            return None
        if not target:
            ui_lines.append("Put it where?")
            return None
        if not target.container:
            ui_lines.append(f"You can't put things in the {target.name}.")
            return None
        if target.openable and target.id not in session.open_objects:
            ui_lines.append(f"The {target.name} is closed.")
            return None
        put_ids: list[str] = []
        for oid in held_ids:
            if oid not in session.inventory:
                continue
            held = self.world.objects[oid]
            session.inventory.remove(oid)
            session.containers.setdefault(target.id, []).append(oid)
            put_ids.append(oid)
            story_lines.append(f"{held.name}: Put in {target.name}.")
            if (
                held.score_on_deposit
                and held.deposit_target == target.id
                and held.id not in session.deposited
            ):
                session.deposited.append(held.id)
                session.score += held.score_on_deposit
                ui_lines.append(f"[+{held.score_on_deposit} points]")
                milestone = "treasure"
        remember_objects(session, put_ids)
        return milestone

    def _finish(
        self,
        session: Session,
        story_lines: list[str],
        ui_lines: list[str],
        scene_emoji: str,
        is_timer: bool,
        milestone: str | None,
        raw: str = "",
    ) -> StepResult:
        story = "\n".join(p for p in story_lines if p)
        ui = "\n".join(p for p in ui_lines if p)
        text = "\n".join(p for p in (story, ui) if p)
        session.last_output = text
        session.last_story = story
        session.last_ui = ui
        if raw and raw not in {"again", "repeat", "g", "say again"}:
            session.last_command = raw
        return StepResult(
            text,
            session,
            milestone=milestone,
            story=story,
            ui=ui,
            is_timer=is_timer,
            scene_emoji=scene_emoji,
        )
