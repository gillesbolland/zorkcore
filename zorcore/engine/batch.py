"""Multi-object / ALL / EXCEPT helpers for inventory verbs."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zorcore.engine.parser import ParsedCommand
    from zorcore.engine.sessions import Session
    from zorcore.engine.world import World

BATCH_CAP = 20


def expand_pronoun(token: str, session: Session) -> list[str]:
    t = (token or "").strip().lower()
    if t == "it":
        return [session.last_it] if session.last_it else []
    if t == "them":
        return list(session.last_them) if session.last_them else []
    return [token] if token else []


def collect_take_targets(
    session: Session,
    world: World,
    cmd: ParsedCommand,
    *,
    npc_ids: set[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Return (object_ids, missing_phrases) for take, applying ALL/EXCEPT/lists."""
    npc_ids = npc_ids or set()
    room_objs = list(session.room_objects.get(session.room_id, []))
    candidates = list(room_objs)
    for cid, contents in session.containers.items():
        if cid in room_objs and cid in session.open_objects:
            candidates.extend(contents)

    if cmd.batch:
        ids: list[str] = []
        for oid in candidates:
            if oid in npc_ids:
                continue
            obj = world.objects.get(oid)
            if not obj or not obj.takeable or obj.fixture:
                continue
            if cmd.batch == "valuables" and not obj.score_on_deposit:
                continue
            ids.append(oid)
        if cmd.except_nouns:
            excl: set[str] = set()
            for phrase in cmd.except_nouns:
                o = world.find_object(phrase, ids + candidates)
                if o:
                    excl.add(o.id)
            ids = [i for i in ids if i not in excl]
        return ids[:BATCH_CAP], []

    tokens = list(cmd.nouns) if len(cmd.nouns) > 1 else ([cmd.noun] if cmd.noun else [])
    if not tokens and cmd.noun:
        tokens = [cmd.noun]
    out: list[str] = []
    missing: list[str] = []
    for tok in tokens:
        expanded = expand_pronoun(tok, session)
        if tok.lower() in {"it", "them"} and not expanded:
            continue
        found_any = False
        for piece in expanded or [tok]:
            if piece in world.objects and piece in candidates and piece not in out:
                out.append(piece)
                found_any = True
                continue
            o = world.find_object(piece, candidates)
            if o and o.id not in out:
                out.append(o.id)
                found_any = True
        if not found_any:
            missing.append(tok)
    return out[:BATCH_CAP], missing


def collect_inventory_targets(
    session: Session,
    world: World,
    cmd: ParsedCommand,
) -> tuple[list[str], list[str]]:
    """Return (object_ids, missing_phrases) from inventory for drop/put."""
    if cmd.batch:
        ids = list(session.inventory)
        if cmd.batch == "valuables":
            ids = [
                i
                for i in ids
                if (world.objects.get(i) and world.objects[i].score_on_deposit)
            ]
        if cmd.except_nouns:
            excl: set[str] = set()
            for phrase in cmd.except_nouns:
                o = world.find_object(phrase, ids)
                if o:
                    excl.add(o.id)
            ids = [i for i in ids if i not in excl]
        return ids[:BATCH_CAP], []

    tokens = list(cmd.nouns) if len(cmd.nouns) > 1 else ([cmd.noun] if cmd.noun else [])
    out: list[str] = []
    missing: list[str] = []
    for tok in tokens:
        expanded = expand_pronoun(tok, session)
        if tok.lower() in {"it", "them"} and not expanded:
            continue
        found_any = False
        for piece in expanded or [tok]:
            if piece in session.inventory and piece not in out:
                out.append(piece)
                found_any = True
                continue
            o = world.find_object(piece, session.inventory)
            if o and o.id not in out:
                out.append(o.id)
                found_any = True
        if not found_any:
            missing.append(tok)
    return out[:BATCH_CAP], missing


def remember_objects(session: Session, ids: list[str]) -> None:
    if not ids:
        return
    if len(ids) == 1:
        session.last_it = ids[0]
        session.last_them = [ids[0]]
    else:
        session.last_them = list(ids)
        session.last_it = ids[-1]
