"""Radio-friendly text helpers: chunking and normalization."""

from __future__ import annotations

import re
import unicodedata


# Zero-width / bidi / BOM noise phones and messengers sometimes inject.
_INVISIBLE = dict.fromkeys(map(ord, "\ufeff\u200b\u200c\u200d\u2060\u00ad"), None)

# Treat non-breaking / narrow spaces like normal spaces before split.
_SPACE_RE = re.compile(r"[\s\u00a0\u202f\u2007\u2008\u2009\u200a]+", re.UNICODE)


def normalize_command(text: str) -> str:
    """Case-fold and clean player input for command matching.

    Phone keyboards often capitalize the first letter (``New game``). Matching
    must not depend on case or invisible Unicode whitespace.
    """
    if not text:
        return ""
    cleaned = unicodedata.normalize("NFKC", str(text)).translate(_INVISIBLE)
    cleaned = _SPACE_RE.sub(" ", cleaned).strip()
    return cleaned.casefold()


def utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


def fits_utf8(text: str, max_bytes: int) -> bool:
    return utf8_len(text) <= max_bytes


def _cut_at_boundary(data: bytes, start: int, end: int) -> int:
    """Return a cut index <= end that does not split a UTF-8 codepoint."""
    cut = end
    while cut > start and (data[cut - 1] & 0xC0) == 0x80:
        cut -= 1
    if cut > start and (data[cut - 1] & 0x80) and (data[cut - 1] & 0xC0) != 0xC0:
        while cut > start and (data[cut - 1] & 0xC0) != 0xC0:
            cut -= 1
        if cut > start:
            cut -= 1
    if cut <= start:
        return end
    return cut


def chunk_text(text: str, max_bytes: int, max_chunks: int) -> list[str]:
    """Split UTF-8 text into MeshCore-friendly chunks without mid-codepoint cuts."""
    if not text:
        return []
    data = text.encode("utf-8")
    if len(data) <= max_bytes:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(data) and len(chunks) < max_chunks:
        end = min(start + max_bytes, len(data))
        cut = _cut_at_boundary(data, start, end)
        piece = data[start:cut].decode("utf-8", errors="ignore")
        if not piece:
            break
        if cut < len(data):
            # Prefer paragraph, then sentence, then whitespace
            for sep in ("\n\n", "\n", ". ", "! ", "? ", "; ", ", ", " "):
                idx = piece.rfind(sep)
                min_keep = max(8, len(piece) // 5)
                if idx >= min_keep:
                    if sep in {". ", "! ", "? ", "; ", ", "}:
                        piece = piece[: idx + len(sep)].rstrip()
                    else:
                        piece = piece[:idx].rstrip()
                    cut = start + len(piece.encode("utf-8"))
                    break
            else:
                ws = piece.rfind(" ")
                if ws > max(8, len(piece) // 4):
                    piece = piece[:ws]
                    cut = start + len(piece.encode("utf-8"))
        remaining_after = len(data) - cut
        will_truncate = len(chunks) + 1 >= max_chunks and remaining_after > 0
        if will_truncate:
            suffix = "…"
            while piece and utf8_len(piece + suffix) > max_bytes:
                piece = piece[:-1]
            piece = piece.rstrip() + suffix
            chunks.append(piece)
            break
        chunks.append(piece)
        start = cut
        while start < len(data) and data[start : start + 1] in (b" ", b"\n"):
            start += 1
    return chunks


def part_suffix(index: int, total: int) -> str:
    """Trailing multipart label: ``1/3…`` mid-parts, ``.3/3`` on the final."""
    if total <= 1:
        return ""
    if index < total:
        return f" {index}/{total}…"
    return f" .{index}/{total}"


# Reserve for worst-case `` 99/99…`` (ellipsis is 3 UTF-8 bytes).
_LABEL_RESERVE = 10


def format_multipart(
    text: str,
    max_bytes: int,
    max_chunks: int,
    *,
    scene_emoji: str = "",
    timer_prefix: str = "",
) -> list[str]:
    """Split text into radio parts with trailing n/m labels when split."""
    body = (text or "").strip()
    if not body:
        return []

    reserve = _LABEL_RESERVE
    budget = max(16, max_bytes - reserve)

    raw_parts = chunk_text(body, budget, max_chunks)
    if not raw_parts:
        return []
    total = len(raw_parts)
    out: list[str] = []
    for i, part in enumerate(raw_parts, start=1):
        suffix = part_suffix(i, total)
        head = ""
        if i == 1:
            if timer_prefix:
                for candidate in (f"{timer_prefix} ", "[timer] "):
                    if fits_utf8(candidate + part + suffix, max_bytes):
                        head = candidate
                        break
            if scene_emoji:
                candidate = f"{head}{scene_emoji} "
                if fits_utf8(candidate + part + suffix, max_bytes):
                    head = candidate
        msg = f"{head}{part}{suffix}"
        while utf8_len(msg) > max_bytes and part:
            part = part[:-1]
            msg = f"{head}{part}{suffix}".rstrip()
        if msg:
            out.append(msg)
    return out


def format_reply(
    story: str,
    ui: str,
    max_bytes: int,
    max_chunks: int,
    *,
    scene_emoji: str = "",
    timer_prefix: str = "",
) -> list[str]:
    """Story may be multipart-labeled; UI (Exits tip, score) is always unlabeled."""
    story_body = (story or "").strip()
    ui_body = (ui or "").strip()
    if not story_body and not ui_body:
        return []

    # Leave at least one chunk for UI when both present.
    story_cap = max_chunks
    if story_body and ui_body:
        story_cap = max(1, max_chunks - 1)

    story_parts: list[str] = []
    if story_body:
        story_parts = format_multipart(
            story_body,
            max_bytes,
            story_cap,
            scene_emoji=scene_emoji,
            timer_prefix=timer_prefix,
        )

    remaining = max(0, max_chunks - len(story_parts))
    ui_parts: list[str] = []
    if ui_body and remaining > 0:
        # Status tips never carry n/m — chunk at full radio budget.
        for part in chunk_text(ui_body, max(16, max_bytes), remaining):
            while utf8_len(part) > max_bytes and part:
                part = part[:-1]
            if part:
                ui_parts.append(part)

    return story_parts + ui_parts
