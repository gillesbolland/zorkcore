"""Tiny parser driven by a VerbTable — supports ALL/EXCEPT, AND lists, LOOK UNDER."""

from __future__ import annotations

import re
from dataclasses import dataclass

from zorcore.engine.verbs import VerbTable, load_verb_table
from zorcore.text import normalize_command

# Module-level defaults for tests that call parse() without a table.
_DEFAULT_VERBS = load_verb_table({})

# Re-exports used by main.py first-message gate.
START_PHRASES = _DEFAULT_VERBS.start_phrases
# Radio reaction-repeat only — never again/g/repeat (those are OG redo).
RESEND_PHRASES = frozenset({"hello?", "hello ?", "?", "hello"})

BATCH_NOUNS = frozenset({"all", "everything", "every", "valuables", "treasures", "treasure"})
LOOK_PREPS = ("under", "behind", "through", "inside", "in", "at")


@dataclass(frozen=True)
class ParsedCommand:
    verb: str
    noun: str = ""
    prep: str = ""
    indirect: str = ""
    raw: str = ""
    # Multi-object / ALL support
    nouns: tuple[str, ...] = ()
    except_nouns: tuple[str, ...] = ()
    batch: str = ""  # "" | "all" | "valuables"
    look_mode: str = ""  # "" | under | behind | through | inside | at


def _split_object_list(phrase: str) -> list[str]:
    """Split 'lamp, sword and paper' into tokens."""
    phrase = (phrase or "").strip()
    if not phrase:
        return []
    # Protect "but not" / except handled elsewhere
    parts = re.split(r"\s*,\s*|\s+and\s+", phrase, flags=re.I)
    return [p.strip() for p in parts if p.strip()]


def _split_except(phrase: str) -> tuple[str, list[str]]:
    """Split 'all except lamp and sword' → ('all', ['lamp', 'sword'])."""
    phrase = (phrase or "").strip()
    if not phrase:
        return "", []
    m = re.search(r"\s+(?:except|but(?:\s+not)?)\s+", phrase, flags=re.I)
    if not m:
        return phrase, []
    head = phrase[: m.start()].strip()
    tail = phrase[m.end() :].strip()
    return head, _split_object_list(tail)


def parse(text: str, verbs: VerbTable | None = None) -> ParsedCommand:
    verbs = verbs or _DEFAULT_VERBS
    raw = normalize_command(text)
    if not raw:
        return ParsedCommand(verb="", raw=raw)

    # OG redo first (must not fall through to hello?)
    if raw in {"again", "repeat", "g", "say again"}:
        return ParsedCommand(verb="again", raw=raw)

    # Radio reaction-repeat only
    if raw in RESEND_PHRASES:
        return ParsedCommand(verb="hello?", raw=raw)

    if raw in verbs.start_phrases:
        return ParsedCommand(verb="new", noun="game", raw=raw)
    if raw in verbs.single_verbs:
        return ParsedCommand(verb=verbs.single_verbs[raw], raw=raw)
    if raw in verbs.directions:
        return ParsedCommand(verb="go", noun=verbs.directions[raw], raw=raw)

    parts = raw.split()
    verb = verbs.synonyms.get(parts[0], parts[0])
    rest = parts[1:]

    # "pick up X"
    if verb == "take" and rest and rest[0] == "up":
        rest = rest[1:]
    # "turn on/off lamp"
    if verb == "turn" and rest:
        if rest[0] == "on":
            verb = "light"
            rest = rest[1:]
        elif rest[0] == "off":
            verb = "extinguish"
            rest = rest[1:]

    # LOOK UNDER / AT / BEHIND / THROUGH / INSIDE
    look_mode = ""
    if verb == "look" and rest and rest[0] in LOOK_PREPS:
        look_mode = "inside" if rest[0] in {"in", "inside"} else rest[0]
        rest = rest[1:]
        if look_mode == "at":
            verb = "examine"
            look_mode = ""
        else:
            verb = "look_under" if look_mode == "under" else "look_prep"

    # "put X in/on Y" / "attack X with Y" / "throw X at Y" / "tie X to Y"
    prep = ""
    indirect = ""
    noun = ""
    nouns: tuple[str, ...] = ()
    except_nouns: tuple[str, ...] = ()
    batch = ""

    def _finish_noun_phrase(np: str) -> tuple[str, tuple[str, ...], tuple[str, ...], str]:
        head, excl = _split_except(np)
        tokens = _split_object_list(head) if head else []
        b = ""
        if len(tokens) == 1 and tokens[0] in BATCH_NOUNS:
            b = "valuables" if tokens[0] in {"valuables", "treasures", "treasure"} else "all"
            return tokens[0], (), tuple(excl), b
        if not tokens and head in BATCH_NOUNS:
            b = "valuables" if head in {"valuables", "treasures", "treasure"} else "all"
            return head, (), tuple(excl), b
        if len(tokens) > 1:
            return tokens[0], tuple(tokens), tuple(excl), ""
        if len(tokens) == 1:
            return tokens[0], (tokens[0],), tuple(excl), ""
        return head, ((head,) if head else ()), tuple(excl), ""

    joined = " ".join(rest)
    if "with" in rest:
        i = rest.index("with")
        noun, nouns, except_nouns, batch = _finish_noun_phrase(" ".join(rest[:i]))
        prep = "with"
        indirect = " ".join(rest[i + 1 :]).strip()
    elif verb in {"put", "pour", "fill", "drop"} and any(p in rest for p in ("into", "in", "on")):
        for p in ("into", "in", "on"):
            if p in rest:
                i = rest.index(p)
                noun, nouns, except_nouns, batch = _finish_noun_phrase(" ".join(rest[:i]))
                prep = "in" if p in {"in", "into"} else "on"
                indirect = " ".join(rest[i + 1 :]).strip()
                break
    elif "at" in rest and verb in {"throw", "look_prep"}:
        i = rest.index("at")
        noun, nouns, except_nouns, batch = _finish_noun_phrase(" ".join(rest[:i]))
        prep = "at"
        indirect = " ".join(rest[i + 1 :]).strip()
    elif "to" in rest and verb in {"give", "tie", "hand"}:
        i = rest.index("to")
        noun, nouns, except_nouns, batch = _finish_noun_phrase(" ".join(rest[:i]))
        prep = "to"
        indirect = " ".join(rest[i + 1 :]).strip()
    else:
        noun, nouns, except_nouns, batch = _finish_noun_phrase(joined)

    if verb == "go" and noun in verbs.directions:
        noun = verbs.directions[noun]
    elif verb == "go" and noun.startswith("to "):
        rest_n = noun[3:].strip()
        if rest_n in verbs.directions:
            noun = verbs.directions[rest_n]
    elif verb in {"enter"}:
        verb = "go"
        noun = noun or "in"
        if noun in verbs.directions:
            noun = verbs.directions[noun]

    if look_mode == "under":
        verb = "look_under"
    elif look_mode and verb == "look_prep":
        pass  # look_behind etc.

    return ParsedCommand(
        verb=verb,
        noun=noun,
        prep=prep,
        indirect=indirect,
        raw=raw,
        nouns=nouns,
        except_nouns=except_nouns,
        batch=batch,
        look_mode=look_mode if look_mode != "under" else "under",
    )
