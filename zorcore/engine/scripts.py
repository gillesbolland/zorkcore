"""Declarative content scripts: match verb/object/room/flags → effects."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from zorcore.engine.sessions import Session
from zorcore.engine.world import World


@dataclass
class ScriptResult:
    matched: bool = False
    story: list[str] = field(default_factory=list)
    ui: list[str] = field(default_factory=list)
    stop: bool = False  # skip default verb handler
    milestone: str | None = None
    # Outbound side-effects for the plugin layer (radio sends).
    broadcasts: list[str] = field(default_factory=list)
    room_notices: list[dict[str, str]] = field(default_factory=list)
    teleported_to: str | None = None


def _as_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    return [str(x) for x in raw]


def _flag_ok(
    session: Session,
    when: dict[str, Any] | None,
    *,
    world_state: Any = None,
) -> bool:
    if not when:
        return True
    flags = when.get("flags") or {}
    for key, want in flags.items():
        have = bool(session.flags.get(str(key), False))
        if have != bool(want):
            return False
    if "room" in when and session.room_id != when["room"]:
        return False
    if "alive" in when and session.alive != bool(when["alive"]):
        return False

    need = _as_list(when.get("has"))
    if need and not all(oid in session.inventory for oid in need):
        return False
    forbid = _as_list(when.get("missing"))
    if forbid and any(oid in session.inventory for oid in forbid):
        return False
    if "max_carried" in when:
        try:
            if len(session.inventory) > int(when["max_carried"]):
                return False
        except (TypeError, ValueError):
            pass

    # Shared-NPC conditions (no-ops when no world state is injected).
    npc_alive = when.get("npc_alive")
    if npc_alive is not None:
        if world_state is None:
            return False
        want = True
        npc_id = npc_alive
        if isinstance(npc_alive, dict):
            npc_id = str(npc_alive.get("npc", ""))
            want = bool(npc_alive.get("alive", True))
        if bool(world_state.is_alive(str(npc_id))) != want:
            return False
    npc_here = when.get("npc_here")
    if npc_here is not None:
        if world_state is None:
            return False
        if world_state.room_of(str(npc_here)) != session.room_id:
            return False
        if not world_state.is_alive(str(npc_here)):
            return False
    return True


def apply_effects(
    session: Session,
    world: World,
    effects: list[dict[str, Any]],
    result: ScriptResult,
    *,
    world_state: Any = None,
    rand: Callable[[], float] | None = None,
) -> None:
    import random as _random

    roll = rand or _random.random
    for eff in effects:
        op = eff.get("op")
        if op == "move_npc":
            if world_state is None:
                continue
            npc = str(eff.get("npc", ""))
            rooms = _as_list(eff.get("rooms"))
            if not npc or not rooms:
                continue
            idx = min(len(rooms) - 1, int(roll() * len(rooms)))
            world_state.move_npc(npc, rooms[idx])
            continue
        if op == "kill_npc":
            if world_state is None:
                continue
            world_state.kill_npc(str(eff.get("npc", "")))
            continue
        if op == "revive_npc":
            if world_state is None:
                continue
            world_state.revive_npc(str(eff.get("npc", "")))
            continue
        if op == "teleport":
            # Records the destination only; the caller runs room entry so
            # scoring, darkness and enter-scripts all fire normally.
            dest = str(eff.get("room", "")).strip()
            if dest and dest in world.rooms:
                result.teleported_to = dest
            continue
        if op == "broadcast":
            text = str(eff.get("text", "")).strip()
            if text:
                result.broadcasts.append(text)
            continue
        if op == "notify_room":
            if world_state is None:
                continue
            npc = str(eff.get("npc", ""))
            text = str(eff.get("text", "")).strip()
            room = world_state.room_of(npc) if npc else session.room_id
            if text and room:
                result.room_notices.append({"room": room, "text": text})
            continue
        if op == "steal":
            if world_state is None:
                continue
            npc = str(eff.get("npc", "thief"))
            loot = [
                oid
                for oid in session.inventory
                if (world.objects.get(oid) and getattr(world.objects[oid], "score_on_deposit", 0))
            ]
            if not loot:
                continue
            idx = min(len(loot) - 1, int(roll() * len(loot)))
            oid = loot[idx]
            session.inventory.remove(oid)
            world_state.stash_add(npc, oid, session.sender_key)
            continue
        if op == "message":
            result.story.append(str(eff.get("text", "")))
        elif op == "ui":
            result.ui.append(str(eff.get("text", "")))
        elif op == "set_flag":
            session.flags[str(eff["flag"])] = True
        elif op == "clear_flag":
            session.flags[str(eff["flag"])] = False
        elif op == "score":
            n = int(eff.get("amount", 0))
            session.score += n
            if n:
                result.ui.append(f"[+{n} points]" if n > 0 else f"[{n} points]")
        elif op == "remove_object":
            oid = str(eff["object"])
            for rid, objs in session.room_objects.items():
                if oid in objs:
                    objs.remove(oid)
            if oid in session.inventory:
                session.inventory.remove(oid)
            for contents in session.containers.values():
                if oid in contents:
                    contents.remove(oid)
        elif op == "move_object":
            oid = str(eff["object"])
            dest = str(eff.get("room", session.room_id))
            for rid, objs in session.room_objects.items():
                if oid in objs:
                    objs.remove(oid)
            if oid in session.inventory:
                session.inventory.remove(oid)
            session.room_objects.setdefault(dest, []).append(oid)
        elif op == "add_inventory":
            oid = str(eff["object"])
            for rid, objs in list(session.room_objects.items()):
                if oid in objs:
                    objs.remove(oid)
            if oid not in session.inventory:
                session.inventory.append(oid)
        elif op == "open":
            session.open_objects.add(str(eff["object"]))
        elif op == "close":
            session.open_objects.discard(str(eff["object"]))
        elif op == "light":
            session.lit_objects.add(str(eff["object"]))
        elif op == "extinguish":
            session.lit_objects.discard(str(eff["object"]))
        elif op == "kill":
            session.alive = False
            session.active_timers.clear()
            result.milestone = "death"
        elif op == "milestone":
            result.milestone = str(eff.get("id", "script"))


def run_scripts(
    scripts: list[dict[str, Any]],
    *,
    session: Session,
    world: World,
    verb: str,
    noun: str = "",
    indirect: str = "",
    object_id: str | None = None,
    trigger: str = "verb",
    world_state: Any = None,
    rand: Callable[[], float] | None = None,
) -> ScriptResult:
    result = ScriptResult()
    for script in scripts:
        script_trigger = str(script.get("trigger", "verb")).lower()
        if script_trigger != trigger:
            continue
        if trigger == "verb":
            if str(script.get("verb", "")).lower() != verb:
                continue
        if not _flag_ok(session, script.get("when"), world_state=world_state):
            continue
        if trigger != "verb":
            apply_effects(
                session,
                world,
                list(script.get("effects") or []),
                result,
                world_state=world_state,
                rand=rand,
            )
            result.matched = True
            if bool(script.get("stop", False)):
                result.stop = True
                break
            continue
        want_obj = script.get("object")
        if want_obj:
            if object_id != want_obj and noun:
                # also allow match by noun alias resolved earlier
                if object_id is None:
                    continue
                if object_id != want_obj:
                    continue
            elif object_id != want_obj:
                continue
        want_indirect = script.get("indirect")
        if want_indirect and indirect:
            ind = world.find_object(indirect, _all_reachable(session))
            if not ind or ind.id != want_indirect:
                continue
        elif want_indirect and not indirect:
            continue

        apply_effects(
            session,
            world,
            list(script.get("effects") or []),
            result,
            world_state=world_state,
            rand=rand,
        )
        result.matched = True
        result.stop = bool(script.get("stop", True))
        if result.stop:
            break
    return result


def _all_reachable(session: Session) -> list[str]:
    ids = list(session.inventory)
    ids.extend(session.room_objects.get(session.room_id, []))
    for oid, contents in session.containers.items():
        if oid in ids or oid in session.room_objects.get(session.room_id, []):
            ids.extend(contents)
    return ids
