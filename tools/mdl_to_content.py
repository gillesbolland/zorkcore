#!/usr/bin/env python3
"""Offline authoring tool: MIT Zork MDL dung.56 → ZorCore content/.

Parses #ROOM / #OBJECT / <SOBJECT> / <AOBJECT> / <ADD-OBJECT> forms and writes:
  zorcore/content/world.json
  tools/reports/zork-full-conversion.json
plus stub meta.json / verbs.json / scripts.json when missing.

MDL sources are NOT vendored in this plugin repository. Clone
https://github.com/MITDDC/zork separately and either:

  - place it at ``./zork`` beside this repo (local checkout layout), or
  - set env ``ZORCORE_MDL_ROOT`` to that clone (expects ``dung.56`` inside).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
_MDL_ROOT = Path(os.environ["ZORCORE_MDL_ROOT"]) if os.environ.get("ZORCORE_MDL_ROOT") else ROOT / "zork"
DUNG = _MDL_ROOT / "dung.56"
OUT_DIR = ROOT / "zorcore" / "content"
# The conversion report is a build artifact, kept out of the shipped wheel.
REPORT_PATH = ROOT / "tools" / "reports" / "zork-full-conversion.json"
VERBS_PATH = OUT_DIR / "verbs.json"

BUDGET = 133  # effective single-message budget (bytes)
SOFT_MAX = 170  # still try to fit one message
HARD_MAX = 400  # never exceed for room descriptions

DIR_MAP = {
    "NORTH": "north",
    "SOUTH": "south",
    "EAST": "east",
    "WEST": "west",
    "NE": "northeast",
    "NW": "northwest",
    "SE": "southeast",
    "SW": "southwest",
    "UP": "up",
    "DOWN": "down",
    "ENTER": "in",
    "EXIT": "out",
    "CROSS": "cross",
    "CLIMB": "climb",
    "LAUNC": "launch",
    "LAND": "land",
}

# LOOK overlays for rooms whose LDESC is empty in dung.56
# (prose from room-action LOOK handlers / default closed state).
EMPTY_LDESC_OVERLAY: dict[str, str] = {
    'bats': 'You are in a small room which has only one door, to the east.',
    'carou': (
        'You are in a circular room with passages off in eight directions. '
        "Your compass needle spins wildly, and you can't get your bearings."
    ),
    'cella': 'You are in a dark and damp cellar with a narrow passageway leading east, and a crawlway to the south. On the west is the bottom of a steep metal ramp which is unclimbable.',
    'clear': 'You are in a clearing, with a forest surrounding you on the west and south.',
    'cmach': "You are in a large room full of assorted heavy machinery. The room smells of burned resistors. The room is noisy from the whirring sounds of the machines. Along one wall of the room are three buttons which are, respectively, round, triangular, and square. Naturally, above these buttons are instructions written in EBCDIC. A large sign in English above all the buttons says 'DANGER -- HIGH VOLTAGE '. There are exits to the west and the south.",
    'cyclo': 'You are in a room with an exit on the west side, and a staircase leading up.',
    'dam': 'You are standing on the top of the Flood Control Dam #3, which was quite a tourist attraction in times far distant. There are paths to the north, south, east, and down.',
    'dome': 'You are at the periphery of a large dome, which forms the ceiling of another room below. Protecting you from a precipitous drop is a wooden railing which circles the dome.',
    'ehous': 'You are behind the white house.  In one corner of the house there is a small window which is slightly ajar.',
    'falls': 'You are at the top of Aragain Falls, an enormous waterfall with a drop of about 450 feet. The only path here is on the north end. There is a man-sized barrel here which you could fit into.',
    'fchmp': 'Oh dear, you seem to have gone over Aragain Falls.  Not a very smart thing to do, apparently.',
    'icy': 'You are in a large room, with giant icicles hanging from the walls and ceiling.  There are passages to the north and east.',
    'kitch': 'You are in the kitchen of the white house.  A table seems to have been used recently for the preparation of food.  A passage leads to the west and a dark staircase can be seen leading upward.  To the east is a small window which is slightly ajar.',
    'ledg4': 'You are on a wide ledge high into the volcano. The rim of the volcano is about 200 feet above and there is a precipitous drop below to the bottom.',
    'lld1': 'You are outside a large gateway, on which is inscribed "Abandon every hope, all ye who enter here." The gate is open; through it you can see a desolation, with a pile of mangled corpses in one corner. Thousands of voices, lamenting some hideous fate, can be heard.',
    'lld2': 'You have entered the Land of the Living Dead, a large desolate room. Although it is apparently uninhabited, you can hear the sounds of thousands of lost souls weeping and moaning. In the east corner are stacked the remains of dozens of previous adventurers who were less fortunate than yourself. To the east is an ornate passage, apparently recently constructed.',
    'lroom': 'You are in the living room.  There is a door to the east, a wooden door with strange gothic lettering to the west, which appears to be nailed shut, and a large oriental rug in the center of the room.',
    'machi': "You are in a large room which seems to be air-conditioned. In one corner there is a machine (?) which is shaped somewhat like a clothes dryer. On the 'panel' there is a switch which is labelled in a dialect of Swahili. Fortunately, I know this dialect and the label translates to START. The switch does not appear to be manipulable by any human hand (unless the fingers are about 1/16 by 1/4 inch). On the front of the machine is a large lid.",
    'magne': 'You are in a room with a low ceiling which is circular in shape. There are exits to the east and the southeast.',
    'mgrat': 'You are in a small room near the maze. There are twisty passages in the immediate vicinity.',
    'mirr1': 'You are in a large square room with tall ceilings. On the south wall is an enormous mirror which fills the entire wall. There are exits on the other three sides of the room.',
    'mirr2': 'You are in a large square room with tall ceilings. On the south wall is an enormous mirror which fills the entire wall. There are exits on the other three sides of the room.',
    'mtorc': 'You are in a large room with a prominent doorway leading to a down staircase. To the west is a narrow twisting tunnel. Above you is a large dome painted with scenes depicting elfin hacking rites. Up around the edge of the dome (20 feet up) is a wooden railing. In the center of the room there is a white marble pedestal.',
    'resen': 'You are in the north end of a large cavernous room which was formerly a reservoir.',
    'reses': 'You are in the south end of a large cavernous room which was formerly a reservoir.',
    'safe': 'You are in a dusty old room which is virtually featureless, except for an exit on the north side.',
}

AREA_EMOJI: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^(fore|clear|cltop|path)"), "🌲"),
    (re.compile(r"^(whous|nhous|shous|ehous|kitch|attic|lroom|blroo)"), "🏡"),
    (re.compile(r"^(cella|mtrol|craw|chas|pass|ravi)"), "🌑"),
    (re.compile(r"^(maze|maz|dead|mgrat|cyclo)"), "🌀"),
    (re.compile(r"^(mine|tlant|bant|ladder|shaft)"), "⛏️"),
    (re.compile(r"^(rivr|dock|falls|beach|shore|wclf|poc|sawi|rboat)"), "🌊"),
    (re.compile(r"^(temp|lld|tomb|mgrai|pray)"), "⛪"),
    (re.compile(r"^(dome|mtorc|cairn|egypt|atlan)"), "🏛️"),
    (re.compile(r"^(dam|lobby|maint|res)"), "🏗️"),
    (re.compile(r"^(vair|lava|volc|ledg|safe|cage|bubb)"), "🌋"),
    (re.compile(r"^(tree|clbot|clmid)"), "🌳"),
]


def utf8_len(s: str) -> int:
    return len(s.encode("utf-8"))


def lid(s: str) -> str:
    return str(s).strip().lower()


def flag_id(s: str) -> str:
    s = str(s).strip()
    s = re.sub(r"!-FLAG$", "", s, flags=re.I)
    s = s.replace("-", "_").replace("!", "")
    return s.lower()


def normalize_ws(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]*\n[ \t]*", " ", text)
    text = re.sub(r" +", " ", text).strip()
    return text


def shorten_filler(text: str) -> str:
    """Drop common Zork filler while keeping meaning."""
    reps = [
        (r"^You are in an? ", ""),
        (r"^You are standing in an? ", ""),
        (r"^You are at the ", "At the "),
        (r"^You are on the ", "On the "),
        (r"^You are facing the ", ""),
        (r"^You are ", ""),
        (r"^This is an? ", ""),
        (r"^This is ", ""),
        (r"^There is an? ", ""),
        (r"^There is ", ""),
        (r"^There are ", ""),
        (r" which is ", " — "),
        (r" that is ", " — "),
        (r" of the ", " of "),
        (r" to the ", " to "),
        (r" from the ", " from "),
        (r"  +", " "),
    ]
    out = text
    for pat, rep in reps:
        out = re.sub(pat, rep, out, count=1 if pat.startswith("^") else 0)
        if not pat.startswith("^"):
            out = re.sub(pat, rep, out)
    return normalize_ws(out)


def trim_to_bytes(text: str, limit: int) -> str:
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text
    # Prefer sentence/word boundary
    cut = limit
    while cut > 0 and (data[cut - 1] & 0xC0) == 0x80:
        cut -= 1
    piece = data[:cut].decode("utf-8", errors="ignore").rstrip()
    for sep in (". ", "! ", "? ", "; ", ", ", " "):
        idx = piece.rfind(sep)
        if idx >= max(20, len(piece) // 4):
            piece = piece[: idx + (1 if sep.strip() else 0)].rstrip()
            break
    if utf8_len(piece) > limit - 1:
        while piece and utf8_len(piece) > limit - 1:
            piece = piece[:-1]
    if not piece.endswith((".", "!", "?", "…")):
        piece = piece.rstrip(" ,;:") + "…"
    while utf8_len(piece) > limit and piece:
        piece = piece[:-2] + "…"
    return piece


def fit_prose(text: str, *, hard_max: int = HARD_MAX) -> tuple[str, str]:
    """Normalize whitespace only — radio multipart handles length at runtime."""
    text = normalize_ws(text or "")
    if not text:
        return "", "empty"
    return text, "keep"



def area_emoji(room_id: str) -> str:
    for pat, emoji in AREA_EMOJI:
        if pat.search(room_id):
            return emoji
    return "🕯️"


# ---------------------------------------------------------------------------
# Minimal MDL tokenizer / value parser
# ---------------------------------------------------------------------------


class ParseError(Exception):
    pass


class Parser:
    def __init__(self, text: str):
        self.text = text
        self.n = len(text)
        self.i = 0

    def peek(self) -> str:
        self._skip()
        return self.text[self.i] if self.i < self.n else ""

    def _skip(self) -> None:
        while self.i < self.n:
            c = self.text[self.i]
            if c in " \t\n\r\f":
                self.i += 1
                continue
            if c == ";":
                while self.i < self.n and self.text[self.i] not in "\n\r":
                    self.i += 1
                continue
            # form-feed page breaks
            if c == "\x0c":
                self.i += 1
                continue
            break

    def parse_value(self) -> Any:
        self._skip()
        if self.i >= self.n:
            raise ParseError("EOF")
        c = self.text[self.i]
        if c == '"':
            return self._string()
        if c == "%":
            return self._percent()
        if c == "#":
            return self._hash()
        if c == "<":
            return self._form()
        if c == "(":
            return self._list("()")
        if c == "[":
            return self._list("[]")
        if c == "'":
            self.i += 1
            return ("quote", self.parse_value())
        # MDL GVAL / LVAL: ,ATOM or .ATOM
        if c in ",." and self.i + 1 < self.n and (
            self.text[self.i + 1].isalnum() or self.text[self.i + 1] in "_!"
        ):
            sigil = c
            self.i += 1
            return ("gval" if sigil == "," else "lval", self._atom())
        if c in "-0123456789":
            return self._number_or_atom()
        if c.isalpha() or c in "!.$_*?":
            return self._atom()
        raise ParseError(f"unexpected {c!r} at {self.i}")

    def try_value(self) -> Any | None:
        self._skip()
        if self.i >= self.n:
            return None
        c = self.text[self.i]
        if c in ")}]>":
            return None
        try:
            return self.parse_value()
        except ParseError:
            return None

    def _string(self) -> str:
        assert self.text[self.i] == '"'
        self.i += 1
        out: list[str] = []
        while self.i < self.n:
            c = self.text[self.i]
            if c == "\\":
                self.i += 1
                if self.i < self.n:
                    out.append(self.text[self.i])
                    self.i += 1
                continue
            if c == '"':
                self.i += 1
                return "".join(out)
            out.append(c)
            self.i += 1
        raise ParseError("unterminated string")

    def _atom(self) -> str:
        start = self.i
        while self.i < self.n:
            c = self.text[self.i]
            if c.isalnum() or c in "-_!.*$?+":
                self.i += 1
                continue
            break
        if self.i == start:
            raise ParseError(f"bad atom at {self.i}")
        return self.text[start : self.i]

    def _number_or_atom(self) -> Any:
        start = self.i
        if self.text[self.i] == "-":
            self.i += 1
        if self.i < self.n and self.text[self.i].isdigit():
            while self.i < self.n and self.text[self.i].isdigit():
                self.i += 1
            return int(self.text[start : self.i])
        self.i = start
        return self._atom()

    def _percent(self) -> Any:
        # %<> | %,ATOM | %<FORM ...>
        assert self.text[self.i] == "%"
        self.i += 1
        if self.i < self.n and self.text[self.i] == ",":
            self.i += 1
            name = self._atom()
            return ("var", name)
        if self.i < self.n and self.text[self.i] == "<":
            # %<> or %<+ ...> or %<FIND-OBJ ...>
            return ("pctform", self._form_inner())
        raise ParseError(f"bad % form at {self.i}")

    def _form(self) -> Any:
        assert self.text[self.i] == "<"
        return ("form", *self._form_inner())

    def _form_inner(self) -> tuple[str, list[Any]]:
        assert self.text[self.i] == "<"
        self.i += 1
        self._skip()
        if self.i < self.n and self.text[self.i] == ">":
            self.i += 1
            return ("", [])  # <>
        name = self._atom() if self.peek() not in '">()[]' else ""
        if name == "" and self.peek() == "+":
            name = "+"
            self.i += 1
        args: list[Any] = []
        while True:
            self._skip()
            if self.i >= self.n:
                raise ParseError("unterminated form")
            if self.text[self.i] == ">":
                self.i += 1
                break
            args.append(self.parse_value())
        return (name, args)

    def _hash(self) -> Any:
        assert self.text[self.i] == "#"
        self.i += 1
        name = self._atom()
        self._skip()
        if self.i < self.n and self.text[self.i] == "{":
            body = self._brace_values()
            return ("type", name, body)
        if self.i < self.n and self.text[self.i] == '"':
            return ("type", name, [self._string()])
        # bare #TYPE followed by value (rare)
        return ("type", name, [self.parse_value()])

    def _brace_values(self) -> list[Any]:
        assert self.text[self.i] == "{"
        self.i += 1
        vals: list[Any] = []
        while True:
            self._skip()
            if self.i >= self.n:
                raise ParseError("unterminated {")
            if self.text[self.i] == "}":
                self.i += 1
                break
            vals.append(self.parse_value())
        return vals

    def _list(self, kind: str) -> Any:
        open_c, close_c = kind[0], kind[1]
        assert self.text[self.i] == open_c
        self.i += 1
        vals: list[Any] = []
        while True:
            self._skip()
            if self.i >= self.n:
                raise ParseError(f"unterminated {kind}")
            if self.text[self.i] == close_c:
                self.i += 1
                break
            vals.append(self.parse_value())
        return ("list", vals) if open_c == "(" else ("uvector", vals)


def is_false(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, tuple) and v and v[0] == "pctform" and v[1] == ("", []):
        return True
    return False


def is_true(v: Any) -> bool:
    return v == "T" or v is True


def resolve_string(v: Any, psetg: dict[str, Any]) -> str | None:
    if isinstance(v, str):
        return v
    if isinstance(v, tuple) and v[0] == "var":
        ref = psetg.get(v[1])
        if isinstance(ref, str):
            return ref
        if isinstance(ref, tuple) and ref[0] == "type" and ref[1] == "NEXIT":
            body = ref[2]
            if body and isinstance(body[0], str):
                return body[0]
        return None
    if isinstance(v, tuple) and v[0] == "gval":
        ref = psetg.get(v[1])
        if isinstance(ref, str):
            return ref
        return None
    return None


def extract_flags(v: Any) -> set[str]:
    flags: set[str] = set()
    if isinstance(v, str) and v.endswith("BIT"):
        flags.add(v)
        return flags
    if isinstance(v, tuple):
        if v[0] in ("gval", "var") and isinstance(v[1], str):
            if v[1].endswith("BIT"):
                flags.add(v[1])
            return flags
        if v[0] == "pctform":
            name, args = v[1]
            if name in ("+", "ORB", "OR"):
                for a in args:
                    flags |= extract_flags(a)
            elif name == "":
                pass
            else:
                for a in args:
                    flags |= extract_flags(a)
        elif v[0] == "form" and v[1] == "+":
            for a in v[2]:
                flags |= extract_flags(a)
    return flags


def find_obj_ids(v: Any) -> list[str]:
    ids: list[str] = []
    if isinstance(v, tuple):
        if v[0] == "type" and v[1] == "FIND-OBJ":
            for item in v[2]:
                if isinstance(item, str):
                    ids.append(lid(item))
                elif isinstance(item, (list, tuple)):
                    ids.extend(find_obj_ids(item))
        elif v[0] in ("form", "pctform"):
            name = v[1] if v[0] == "form" else v[1][0]
            args = v[2] if v[0] == "form" else v[1][1]
            if name == "FIND-OBJ":
                for a in args:
                    if isinstance(a, str):
                        ids.append(lid(a.strip('"')))
            else:
                for a in args:
                    ids.extend(find_obj_ids(a))
        elif v[0] == "list":
            for a in v[1]:
                ids.extend(find_obj_ids(a))
        elif v[0] == "uvector":
            for a in v[1]:
                ids.extend(find_obj_ids(a))
        elif v[0] == "type":
            for a in v[2]:
                ids.extend(find_obj_ids(a))
    elif isinstance(v, list):
        for a in v:
            ids.extend(find_obj_ids(a))
    return ids


def uvector_strings(v: Any) -> list[str]:
    if isinstance(v, tuple) and v[0] == "uvector":
        return [lid(x) for x in v[1] if isinstance(x, str)]
    if isinstance(v, list):
        return [lid(x) for x in v if isinstance(x, str)]
    return []


# ---------------------------------------------------------------------------
# High-level extraction
# ---------------------------------------------------------------------------


def load_dung() -> str:
    raw = DUNG.read_bytes().replace(b"\x00", b"")
    return raw.decode("latin-1")


def collect_psetg(text: str) -> dict[str, Any]:
    """Collect <PSETG> and exit-like <SETG NAME #CEXIT|#NEXIT|#EXIT ...> bindings."""
    psetg: dict[str, Any] = {}
    p = Parser(text)
    while p.i < p.n:
        p._skip()
        if p.i >= p.n:
            break
        idx_candidates = []
        for tag in ("<PSETG", "<SETG"):
            j = text.find(tag, p.i)
            if j >= 0:
                idx_candidates.append(j)
        if not idx_candidates:
            break
        idx = min(idx_candidates)
        p.i = idx
        try:
            form = p.parse_value()
        except ParseError:
            p.i = idx + 5
            continue
        if not (isinstance(form, tuple) and form[0] == "form" and form[1] in ("PSETG", "SETG")):
            continue
        if len(form[2]) < 2 or not isinstance(form[2][0], str):
            continue
        name, val = form[2][0], form[2][1]
        if form[1] == "PSETG":
            psetg[name] = val
        elif isinstance(val, tuple) and val[0] == "type" and val[1] in ("CEXIT", "NEXIT", "EXIT"):
            psetg[name] = val
    return psetg


def collect_add_descs(text: str) -> dict[str, str]:
    descs: dict[str, str] = {}
    p = Parser(text)
    while True:
        idx = text.find("<ADD-DESC", p.i)
        if idx < 0:
            break
        p.i = idx
        try:
            form = p.parse_value()
        except ParseError:
            p.i = idx + 9
            continue
        if not (isinstance(form, tuple) and form[0] == "form" and form[1] == "ADD-DESC"):
            continue
        args = form[2]
        if len(args) < 2:
            continue
        oid = None
        target = args[0]
        if isinstance(target, tuple) and target[0] == "form" and target[1] == "FIND-OBJ":
            if target[2] and isinstance(target[2][0], str):
                oid = lid(target[2][0])
        text_v = args[1]
        if oid and isinstance(text_v, str):
            descs[oid] = normalize_ws(text_v)
    return descs


def parse_exit_value(v: Any, psetg: dict[str, Any], failures: list[str]) -> Any:
    """Return world.json exit value (str | {nexit} | {cexit})."""
    # Variable that resolves to NEXIT
    if isinstance(v, tuple) and v[0] == "var":
        ref = psetg.get(v[1])
        if ref is None:
            failures.append(f"unresolved exit var {v[1]}")
            return {"nexit": "You can't go that way."}
        return parse_exit_value(ref, psetg, failures)

    if isinstance(v, str):
        return lid(v)

    if isinstance(v, tuple) and v[0] == "type":
        tname, body = v[1], v[2]
        if tname == "NEXIT":
            msg = body[0] if body else "You can't go that way."
            if isinstance(msg, str):
                msg, _ = fit_prose(msg, hard_max=BUDGET)
                return {"nexit": msg}
            failures.append(f"bad NEXIT body {body!r}")
            return {"nexit": "You can't go that way."}
        if tname == "CEXIT":
            # {"FLAG" "ROOM" ["msg"] [action]}
            flag = flag_id(body[0]) if body else "unknown"
            room = lid(body[1]) if len(body) > 1 and isinstance(body[1], str) else ""
            fail = "You can't go that way."
            if len(body) > 2:
                msg = resolve_string(body[2], psetg)
                if msg is not None and msg.strip():
                    fail, _ = fit_prose(msg, hard_max=BUDGET)
            if not room:
                failures.append(f"CEXIT missing room: {body!r}")
            return {"cexit": {"room": room, "flag": flag, "fail": fail}}
        if tname == "EXIT":
            # nested full exit table used as value — unusual
            failures.append(f"nested EXIT as exit target")
            return {"nexit": "You can't go that way."}

    failures.append(f"unparsed exit value {v!r}")
    return {"nexit": "You can't go that way."}


def parse_exit_table(v: Any, psetg: dict[str, Any], failures: list[str]) -> dict[str, Any]:
    exits: dict[str, Any] = {}
    body: list[Any] = []
    if isinstance(v, tuple) and v[0] == "var":
        ref = psetg.get(v[1])
        if ref is None:
            # SETG NULEXIT etc. may not be in PSETG
            failures.append(f"unresolved exit table var {v[1]}")
            return exits
        return parse_exit_table(ref, psetg, failures)
    if isinstance(v, tuple) and v[0] == "type" and v[1] == "EXIT":
        body = list(v[2])
    elif isinstance(v, list):
        body = v
    else:
        failures.append(f"bad exit table {v!r}")
        return exits

    i = 0
    while i < len(body):
        direction = body[i]
        i += 1
        if i >= len(body):
            failures.append(f"exit table trailing direction {direction!r}")
            break
        dest = body[i]
        i += 1
        if not isinstance(direction, str):
            failures.append(f"non-string direction {direction!r}")
            continue
        if direction.startswith("#") or direction == "!":
            continue
        key = DIR_MAP.get(direction.upper(), direction.lower())
        exits[key] = parse_exit_value(dest, psetg, failures)
    return exits


def parse_room_body(
    body: list[Any],
    psetg: dict[str, Any],
    failures: list[str],
) -> dict[str, Any] | None:
    if not body or not isinstance(body[0], str):
        failures.append(f"room missing id: {body[:2]!r}")
        return None
    rid = lid(body[0])
    if rid in {"#####", "!"}:
        return None

    idx = 1
    ldesc_raw = resolve_string(body[idx], psetg) if idx < len(body) else ""
    if ldesc_raw is None:
        ldesc_raw = ""
    idx += 1

    sdesc_raw = resolve_string(body[idx], psetg) if idx < len(body) else rid
    if sdesc_raw is None:
        sdesc_raw = rid
    idx += 1

    lit = True
    if idx < len(body):
        lit_v = body[idx]
        if is_false(lit_v):
            lit = False
            idx += 1
        elif is_true(lit_v):
            lit = True
            idx += 1
        elif isinstance(lit_v, tuple) and lit_v[0] == "type" and lit_v[1] == "EXIT":
            pass  # no explicit lit — default dark? MDL ROOM defaults lit as passed
        elif isinstance(lit_v, tuple) and lit_v[0] == "var":
            # %,NULEXIT style skipped — actually this would be SDESC already consumed
            pass
        else:
            # Could be exit already if lit omitted — rare
            if not (isinstance(lit_v, tuple) and lit_v[0] in ("type", "var")):
                idx += 1

    exits: dict[str, Any] = {}
    if idx < len(body):
        exits = parse_exit_table(body[idx], psetg, failures)
        idx += 1

    objects: list[str] = []
    action = None
    score = 0
    if idx < len(body):
        maybe = body[idx]
        if isinstance(maybe, tuple) and maybe[0] == "list":
            objects = find_obj_ids(maybe)
            idx += 1
        elif is_false(maybe):
            idx += 1

    if idx < len(body):
        maybe = body[idx]
        if isinstance(maybe, str) and not maybe.isdigit():
            action = maybe
            idx += 1
        elif is_false(maybe):
            idx += 1

    if idx < len(body) and isinstance(body[idx], int):
        score = body[idx]
        idx += 1

    # trailing RBITS etc. ignored

    empty_ldesc = not (ldesc_raw or "").strip()
    if empty_ldesc and rid in EMPTY_LDESC_OVERLAY:
        description = EMPTY_LDESC_OVERLAY[rid]
        desc_src = "overlay"
    else:
        description = ldesc_raw or sdesc_raw or rid
        desc_src = "ldesc" if ldesc_raw.strip() else "sdesc"

    description, fit_status = fit_prose(description)
    name = normalize_ws(sdesc_raw) or rid
    if utf8_len(name) > BUDGET:
        name, _ = fit_prose(name, hard_max=BUDGET)

    room: dict[str, Any] = {
        "name": name,
        "description": description,
        "brief": name if name.endswith(".") else f"{name}.",
        "exits": exits,
    }
    emoji = area_emoji(rid)
    if emoji:
        room["emoji"] = emoji
    if not lit:
        room["dark"] = True
    if objects:
        room["objects"] = objects
    if score:
        room["score_on_enter"] = score

    room["_meta"] = {
        "empty_ldesc": empty_ldesc,
        "desc_src": desc_src,
        "fit": fit_status,
        "action": action,
        "raw_ldesc_bytes": utf8_len(normalize_ws(ldesc_raw)) if ldesc_raw else 0,
    }
    return {"id": rid, "room": room}


def parse_object_fields(
    body: list[Any],
    psetg: dict[str, Any],
    failures: list[str],
) -> dict[str, Any] | None:
    """Parse #OBJECT {id desc1 desc2 desco action contents can flags [light ofval otval size capac]}."""
    if not body or not isinstance(body[0], str):
        failures.append(f"object missing id: {body[:2]!r}")
        return None
    oid = lid(body[0])
    if oid in {"#####", "!"}:
        return None

    def take(i: int) -> tuple[Any, int]:
        if i >= len(body):
            return None, i
        return body[i], i + 1

    i = 1
    desc1, i = take(i)
    desc2, i = take(i)
    desco, i = take(i)
    # desco may actually be action if desco omitted — heuristic:
    # If desco looks like action atom (not string/false) and next looks like list, shift.
    # Standard OBJECT always has DESCO slot (string or false).

    action, i = take(i)
    contents_v, i = take(i)
    can_v, i = take(i)
    flags_v, i = take(i)

    # Heuristic repair: some objects omit ODESCO (jump to action). If `desco` is an atom
    # like ROPE-FUNCTION and `action` is a list, shift fields.
    if (
        isinstance(desco, str)
        and not is_false(desco)
        and desco == desco.upper().replace("_", "-")  # rough
        and "-" in desco
        and isinstance(action, tuple)
        and action[0] == "list"
    ):
        # Actually desco was action; contents was can... This is messy.
        # Prefer trusting positional layout from makstr OBJECT.
        pass

    # Another common pattern: desco is %<> (false), fine.
    # If flags_v looks wrong (e.g. still a list), we may have misaligned — try to find flags.
    nums: list[int] = []
    while i < len(body):
        v = body[i]
        i += 1
        if isinstance(v, int):
            nums.append(v)
        elif isinstance(v, tuple) and v[0] == "var" and v[1] == "BIGFIX":
            nums.append(10**9)  # sentinel immovable size
        elif is_false(v):
            nums.append(0)
        else:
            # trailing junk
            break

    light = nums[0] if len(nums) > 0 else 0
    ofval = nums[1] if len(nums) > 1 else 0
    otval = nums[2] if len(nums) > 2 else 0
    size = nums[3] if len(nums) > 3 else 5
    capac = nums[4] if len(nums) > 4 else 0

    flags = extract_flags(flags_v)
    # Also flags may appear as ,BIT atoms in pctform — already handled.
    if light and light != 10**9:
        flags.add("LIGHTBIT")

    name = resolve_string(desc2, psetg) or oid
    name = normalize_ws(name)

    # Prefer untouched/initial description, then ground desc, then name
    desco_s = resolve_string(desco, psetg) if not is_false(desco) else None
    desc1_s = resolve_string(desc1, psetg) if not is_false(desc1) else None
    description = desco_s or desc1_s or name
    # If description is a var that pointed at TROLLDESC etc.
    if isinstance(desc1, tuple) and desc1[0] == "var":
        vs = resolve_string(desc1, psetg)
        if vs:
            description = vs

    description, fit_status = fit_prose(description, hard_max=SOFT_MAX if utf8_len(description) <= 300 else HARD_MAX)

    contains = find_obj_ids(contents_v) if contents_v is not None else []
    can_ids = find_obj_ids(can_v) if can_v is not None else []

    obj: dict[str, Any] = {
        "name": name,
        "aliases": [],
        "description": description,
    }
    if "TAKEBIT" in flags:
        obj["takeable"] = True
    if "CONTBIT" in flags:
        obj["container"] = True
        obj["openable"] = True
        if capac and capac < 10**8:
            obj["capacity"] = capac
        elif capac >= 10**8:
            obj["capacity"] = 20
    if "LIGHTBIT" in flags:
        obj["provides_light"] = True
    if "DOORBIT" in flags:
        obj["openable"] = True
        obj["fixture"] = True
        obj["fixed"] = True
    if "NDESCBIT" in flags:
        obj["fixture"] = True
        obj["fixed"] = True
    if "WEAPONBIT" in flags:
        obj["weapon"] = True
    if "READBIT" in flags:
        obj["readable"] = description  # may be replaced by ADD-DESC
    if ofval and ofval < 10**8:
        obj["score_on_take"] = ofval
    if otval and otval < 10**8:
        obj["score_on_deposit"] = otval
        obj["deposit_target"] = "tcase"
    if contains:
        obj["contains"] = contains
    if not obj.get("takeable") and (obj.get("fixture") or size >= 10**8):
        obj["fixed"] = True

    return {
        "id": oid,
        "object": obj,
        "can": can_ids[0] if can_ids else None,
        "flags": sorted(flags),
        "fit": fit_status,
        "action": action if isinstance(action, str) else None,
    }


def parse_sobject_aobject(form: Any, kind: str, psetg: dict[str, Any], failures: list[str]) -> dict[str, Any] | None:
    # <SOBJECT "ID" "name" flags...>
    # <AOBJECT "ID" "name"|var ACTION flags...>
    if not (isinstance(form, tuple) and form[0] == "form" and form[1] == kind):
        return None
    args = form[2]
    if not args or not isinstance(args[0], str):
        failures.append(f"bad {kind}: {args!r}")
        return None
    oid = lid(args[0])
    if kind == "SOBJECT":
        name = resolve_string(args[1], psetg) if len(args) > 1 else oid
        flag_args = args[2:]
        action = None
    else:
        name = resolve_string(args[1], psetg) if len(args) > 1 else oid
        action = args[2] if len(args) > 2 and isinstance(args[2], str) else None
        flag_args = args[3:] if action else args[2:]
    name = normalize_ws(name or oid)
    flags: set[str] = set()
    for a in flag_args:
        flags |= extract_flags(a)

    description, fit_status = fit_prose(name)
    obj: dict[str, Any] = {
        "name": name,
        "aliases": [],
        "description": description,
    }
    if "TAKEBIT" in flags:
        obj["takeable"] = True
    if "CONTBIT" in flags:
        obj["container"] = True
        obj["openable"] = True
    if "LIGHTBIT" in flags:
        obj["provides_light"] = True
    if "DOORBIT" in flags:
        obj["openable"] = True
        obj["fixture"] = True
        obj["fixed"] = True
    if "NDESCBIT" in flags:
        obj["fixture"] = True
        obj["fixed"] = True
    if "WEAPONBIT" in flags:
        obj["weapon"] = True
    if "READBIT" in flags:
        obj["readable"] = description
    if not obj.get("takeable"):
        obj.setdefault("fixed", True)

    return {
        "id": oid,
        "object": obj,
        "can": None,
        "flags": sorted(flags),
        "fit": fit_status,
        "action": action,
    }


def merge_aliases(obj: dict[str, Any], syns: list[str], adjs: list[str]) -> None:
    aliases: list[str] = []
    name = obj.get("name", "")
    for s in [name, *syns, *adjs]:
        s = lid(s).strip()
        if not s or s in aliases:
            continue
        # Expand truncated 5-letter MDL atoms lightly: keep as-is (parser matches substrings)
        aliases.append(s)
    obj["aliases"] = aliases


def scan_definitions(text: str, psetg: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Walk the file for rooms and objects."""
    failures: list[str] = []
    rooms: dict[str, Any] = {}
    objects: dict[str, Any] = {}
    obj_meta: dict[str, Any] = {}

    p = Parser(text)
    n = len(text)

    def ingest_object(parsed: dict[str, Any] | None, syns: list[str] | None = None, adjs: list[str] | None = None) -> None:
        if not parsed:
            return
        oid = parsed["id"]
        obj = parsed["object"]
        merge_aliases(obj, syns or [], adjs or [])
        if oid in objects:
            # Merge aliases / keep richer description
            prev = objects[oid]
            prev_aliases = list(dict.fromkeys(prev.get("aliases", []) + obj.get("aliases", [])))
            for k, v in obj.items():
                if k == "aliases":
                    continue
                if k not in prev or prev[k] in ("", [], None):
                    prev[k] = v
            prev["aliases"] = prev_aliases
            objects[oid] = prev
        else:
            objects[oid] = obj
        obj_meta[oid] = {k: parsed[k] for k in ("can", "flags", "fit", "action") if k in parsed}

    while p.i < n:
        p._skip()
        if p.i >= n:
            break
        # Fast-forward to next interesting marker
        slice_ = text[p.i :]
        markers = []
        for m in ("#ROOM", "#OBJECT", "<ADD-OBJECT", "<SOBJECT", "<AOBJECT"):
            j = slice_.find(m)
            if j >= 0:
                markers.append((j, m))
        if not markers:
            break
        markers.sort()
        off, kind = markers[0]
        p.i += off

        try:
            if kind == "#ROOM":
                val = p.parse_value()
                if isinstance(val, tuple) and val[0] == "type" and val[1] == "ROOM":
                    parsed = parse_room_body(val[2], psetg, failures)
                    if parsed:
                        rooms[parsed["id"]] = parsed["room"]
                else:
                    failures.append(f"expected #ROOM, got {val!r}")
            elif kind == "#OBJECT":
                val = p.parse_value()
                syns: list[str] = []
                adjs: list[str] = []
                # Trailing uvectors for synonyms/adjectives
                while True:
                    p._skip()
                    if p.i < n and text[p.i] == "[":
                        uv = p.parse_value()
                        strs = uvector_strings(uv)
                        if not syns:
                            syns = strs
                        else:
                            adjs = strs
                    else:
                        break
                if isinstance(val, tuple) and val[0] == "type" and val[1] == "OBJECT":
                    ingest_object(parse_object_fields(val[2], psetg, failures), syns, adjs)
                else:
                    failures.append(f"expected #OBJECT, got {type(val)}")
            elif kind == "<ADD-OBJECT":
                form = p.parse_value()
                if not (isinstance(form, tuple) and form[0] == "form" and form[1] == "ADD-OBJECT"):
                    failures.append("bad ADD-OBJECT")
                    continue
                args = form[2]
                if not args:
                    continue
                first = args[0]
                syns = uvector_strings(args[1]) if len(args) > 1 else []
                adjs = uvector_strings(args[2]) if len(args) > 2 else []
                if isinstance(first, tuple) and first[0] == "type" and first[1] == "OBJECT":
                    ingest_object(parse_object_fields(first[2], psetg, failures), syns, adjs)
                elif isinstance(first, tuple) and first[0] == "form" and first[1] in ("SOBJECT", "AOBJECT"):
                    ingest_object(parse_sobject_aobject(first, first[1], psetg, failures), syns, adjs)
                else:
                    failures.append(f"ADD-OBJECT unhandled first arg {first[:2] if isinstance(first, tuple) else first!r}")
            elif kind == "<SOBJECT":
                form = p.parse_value()
                ingest_object(parse_sobject_aobject(form, "SOBJECT", psetg, failures))
            elif kind == "<AOBJECT":
                form = p.parse_value()
                ingest_object(parse_sobject_aobject(form, "AOBJECT", psetg, failures))
            else:
                p.i += len(kind)
        except ParseError as e:
            failures.append(f"parse error at {p.i}: {e}")
            p.i += 1

    return rooms, objects, failures


def apply_readable(objects: dict[str, Any], add_descs: dict[str, str]) -> None:
    for oid, text in add_descs.items():
        if oid not in objects:
            continue
        fitted, _ = fit_prose(text, hard_max=HARD_MAX)
        objects[oid]["readable"] = fitted
        # Prefer readable text as richer description when current is tiny
        if utf8_len(objects[oid].get("description", "")) < 8:
            objects[oid]["description"] = fitted[:200]


def strip_meta(rooms: dict[str, Any]) -> dict[str, dict[str, Any]]:
    clean = {}
    for rid, room in rooms.items():
        r = {k: v for k, v in room.items() if not k.startswith("_")}
        clean[rid] = r
    return clean


def collect_flags(rooms: dict[str, Any]) -> dict[str, bool]:
    flags: dict[str, bool] = {}
    for room in rooms.values():
        for dest in room.get("exits", {}).values():
            if isinstance(dest, dict) and "cexit" in dest:
                flags[dest["cexit"]["flag"]] = False
    # Common puzzle flags from demo / dung globals
    for f in (
        "kitchen_window",
        "trap_door",
        "rug_moved",
        "troll_flag",
        "key_flag",
        "low_tide",
        "cyclops_flag",
        "magic_flag",
        "dome_flag",
        "rainbow",
        "deflate",
        "grunlock",
        "lld_flag",
        "riddle_flag",
        "glacier_flag",
        "egypt_flag",
        "carousel_flip",
        "carousel_zoom",
        "cage_solve",
        "bucket_top",
        "safe_flag",
        "mirror_mung",
        "gate_flag",
        "buoy_flag",
        "echo_flag",
    ):
        flags.setdefault(f, False)
    return flags


def unresolved_refs(rooms: dict[str, Any], objects: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    room_ids = set(rooms)
    obj_ids = set(objects)
    for rid, room in rooms.items():
        for direction, dest in room.get("exits", {}).items():
            if isinstance(dest, str):
                if dest not in room_ids:
                    issues.append(f"room {rid} exit {direction} → missing room {dest}")
            elif isinstance(dest, dict) and "cexit" in dest:
                target = dest["cexit"].get("room")
                if target and target not in room_ids:
                    issues.append(f"room {rid} cexit {direction} → missing room {target}")
        for oid in room.get("objects", []):
            if oid not in obj_ids:
                issues.append(f"room {rid} lists missing object {oid}")
        for oid in room.get("objects", []):
            obj = objects.get(oid, {})
            for cid in obj.get("contains", []):
                if cid not in obj_ids:
                    issues.append(f"object {oid} contains missing {cid}")
    return issues


def over_budget_report(rooms: dict[str, Any], objects: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for rid, room in rooms.items():
        desc = room.get("description", "")
        n = utf8_len(desc)
        if n > BUDGET:
            items.append(
                {
                    "kind": "room",
                    "id": rid,
                    "field": "description",
                    "bytes": n,
                    "status": room.get("_meta", {}).get("fit", "unknown"),
                    "preview": desc[:80],
                }
            )
    for oid, obj in objects.items():
        for field in ("description", "readable"):
            if field not in obj or not isinstance(obj[field], str):
                continue
            n = utf8_len(obj[field])
            if n > BUDGET:
                items.append(
                    {
                        "kind": "object",
                        "id": oid,
                        "field": field,
                        "bytes": n,
                        "preview": obj[field][:80],
                    }
                )
    items.sort(key=lambda x: -x["bytes"])
    return items


def write_stubs(start_room: str, welcome: str, flags: dict[str, bool]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    meta_path = OUT_DIR / "meta.json"
    if not meta_path.exists():
        meta = {
            "schema": 1,
            "id": "zork-full",
            "name": "Zork",
            "version": "1.1.0-mesh",
            "companion_name": "Zork",
            "description": (
                "Full MIT 1977 dungeon from dung.56; original prose delivered via radio multipart."
            ),
            "start_room": start_room,
            "welcome": welcome,
            "help": (
                "DM `new game` to start. Try: look, n/s/e/w, open, take, inventory, examine, "
                "light, move, attack, put, help, ?. `1/3…` = more coming, wait · `.3/3` = done. "
                "⏱️ means act soon."
            ),
            "flags": flags,
            "dark": {
                "message": "It is pitch black. You are likely to be eaten by a grue.",
                "timer_id": "dark",
                "timer_seconds": 45,
                "timer_message": "Oh no! A lurking grue gobbles you up.",
                "timer_death": True,
                "timer_hint": "Something stirs in the dark…",
            },
        }
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    else:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["flags"] = flags
        meta["start_room"] = start_room
        meta["welcome"] = welcome
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if not VERBS_PATH.exists():
        VERBS_PATH.write_text(
            json.dumps(
                {
                    "directions": {
                        "land": "land",
                        "climb": "climb",
                        "launch": "launch",
                        "cross": "cross",
                    },
                    "synonyms": {},
                    "start_phrases": [],
                    "resend_phrases": [],
                    "single_verbs": [],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    scripts_path = OUT_DIR / "scripts.json"
    if not scripts_path.exists():
        scripts_path.write_text(
            json.dumps({"scripts": [], "daemons": []}, indent=2) + "\n",
            encoding="utf-8",
        )


def main() -> int:
    if not DUNG.exists():
        print(f"Missing source: {DUNG}")
        return 1

    text = load_dung()
    psetg = collect_psetg(text)

    add_descs = collect_add_descs(text)
    rooms, objects, failures = scan_definitions(text, psetg)
    apply_readable(objects, add_descs)

    # Richer stubs for scenery whose MDL name is the only prose.
    SCENERY_DESC = {
        "door": "A wooden door.",
        "tdoor": "A wooden trap door in the floor.",
        "wind1": "A small window, slightly ajar.",
        "wind2": "A small window looking out behind the house.",
        "fdoor": "The front door is boarded shut.",
        "wdoor": "A wooden door with strange engravings.",
        "sdoor": "A great stone door.",
    }
    for oid, desc in SCENERY_DESC.items():
        if oid in objects and objects[oid].get("description", "").lower() in {
            oid,
            objects[oid].get("name", "").lower(),
            "door",
            "window",
        }:
            objects[oid]["description"] = desc

    stubs_added: list[str] = []
    # Known dung.56 references with no #OBJECT (classic Hades gates).
    MISSING_OBJECT_STUBS = {
        "gates": {
            "name": "gate",
            "aliases": ["gate", "gates"],
            "description": "A massive gate bars the way into Hades.",
            "fixture": True,
            "fixed": True,
        },
    }
    for oid, stub in MISSING_OBJECT_STUBS.items():
        if oid not in objects:
            objects[oid] = stub
            stubs_added.append(oid)

    # Trap door phrases: MDL synonyms are truncated ("TRAPD"); add full forms.
    for oid in ("door", "tdoor"):
        if oid not in objects:
            continue
        obj = objects[oid]
        aliases = list(obj.get("aliases") or [])
        for extra in ("trap", "trap door", "trapdoor", oid):
            if extra not in aliases:
                aliases.append(extra)
        obj["aliases"] = aliases
        if oid == "door":
            obj["name"] = "trap door"
            if (obj.get("description") or "").lower() in {"door", "a wooden door."}:
                obj["description"] = "A wooden trap door in the floor."

    for obj in objects.values():
        for cid in list(obj.get("contains", [])):
            if cid not in objects:
                objects[cid] = {
                    "name": cid,
                    "aliases": [cid],
                    "description": cid,
                    "takeable": True,
                }
                stubs_added.append(cid)

    empty_ldesc = sorted(
        rid for rid, room in rooms.items() if room.get("_meta", {}).get("empty_ldesc")
    )
    still_empty = [
        rid
        for rid in empty_ldesc
        if not (rooms[rid].get("description") or "").strip()
    ]

    over = over_budget_report(rooms, objects)
    unresolved = unresolved_refs(rooms, objects)

    clean_rooms = strip_meta(rooms)
    start_room = "whous" if "whous" in clean_rooms else next(iter(clean_rooms), "whous")
    welcome = clean_rooms.get(start_room, {}).get("description", "Welcome to Zork.")
    welcome, _ = fit_prose(welcome, hard_max=BUDGET)

    flags = collect_flags(rooms)

    world = {
        "start_room": start_room,
        "welcome": welcome,
        "help": (
            "DM `new game` to start. Try: look, n/s/e/w, open, take, inventory, examine, "
            "light, move, attack, put, help, ?. `1/3…` = more coming, wait · `.3/3` = done."
        ),
        "rooms": dict(sorted(clean_rooms.items())),
        "objects": dict(sorted(objects.items())),
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "world.json").write_text(
        json.dumps(world, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    report = {
        "source": str(DUNG.relative_to(ROOT)),
        "budget_bytes": BUDGET,
        "rooms": len(clean_rooms),
        "objects": len(objects),
        "empty_ldesc_count": len(empty_ldesc),
        "empty_ldesc": empty_ldesc,
        "empty_ldesc_unfilled": still_empty,
        "over_budget_count": len(over),
        "over_budget": over,
        "unresolved_refs_count": len(unresolved),
        "unresolved_refs": unresolved,
        "parse_failures_count": len(failures),
        "parse_failures": failures[:200],
        "stubs_added": stubs_added,
        "psetg_strings": sorted(k for k, v in psetg.items() if isinstance(v, str)),
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    write_stubs(start_room, welcome, flags)

    print(f"Rooms: {len(clean_rooms)}")
    print(f"Objects: {len(objects)}")
    print(f"Empty LDESC (overlayed): {len(empty_ldesc)} (still empty: {len(still_empty)})")
    print(f"Over budget strings: {len(over)}")
    print(f"Unresolved refs: {len(unresolved)}")
    print(f"Parse failures: {len(failures)}")
    print(f"Wrote {OUT_DIR / 'world.json'}")
    print(f"Wrote {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
