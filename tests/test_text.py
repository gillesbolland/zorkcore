"""Unit tests for text chunking and multipart mesh formatting."""

from zorcore.text import (
    chunk_text,
    format_multipart,
    format_reply,
    normalize_command,
    part_suffix,
    utf8_len,
)


def test_normalize():
    assert normalize_command("  Hello   ?  ") == "hello ?"


def test_normalize_case_insensitive_commands():
    for phrase in (
        "New game",
        "NEW GAME",
        "New Game",
        "nEw GaMe",
        "\u00a0New\u00a0game\u00a0",
        "New\u200b game",
        "New  game",
    ):
        assert normalize_command(phrase) == "new game"


def test_chunk_short():
    assert chunk_text("hi", 145, 4) == ["hi"]


def test_chunk_splits():
    text = "word " * 80
    chunks = chunk_text(text, 40, 4)
    assert 1 < len(chunks) <= 4
    assert all(len(c.encode("utf-8")) <= 40 for c in chunks[:-1])


def test_chunk_truncates_with_ellipsis():
    text = "x" * 500
    chunks = chunk_text(text, 50, 2)
    assert len(chunks) == 2
    assert chunks[-1].endswith("…")


def test_part_suffix_shapes():
    assert part_suffix(1, 1) == ""
    assert part_suffix(1, 3) == " 1/3…"
    assert part_suffix(2, 3) == " 2/3…"
    assert part_suffix(3, 3) == " .3/3"


def test_multipart_labels_when_split():
    # North-of-house style prose that exceeds a small radio budget
    text = (
        "You are facing the north side of a white house. There is no door here, "
        "and all the windows are boarded up. To the north a narrow path leads into gloomy woods."
    )
    parts = format_multipart(text, max_bytes=80, max_chunks=8)
    assert len(parts) >= 2
    total = len(parts)
    assert parts[0].endswith(f" 1/{total}…")
    assert parts[-1].endswith(f" .{total}/{total}")
    assert all(utf8_len(p) <= 80 for p in parts)
    assert "leads into" in "".join(parts) or "gloomy" in "".join(parts)


def test_multipart_single_part_unlabeled():
    parts = format_multipart("Short.", max_bytes=145, max_chunks=8)
    assert parts == ["Short."]


def test_scene_emoji_omitted_when_over_budget():
    body = "x" * 70
    parts = format_multipart(
        body,
        max_bytes=80,
        max_chunks=4,
        scene_emoji="🌳🏡🌳",
    )
    assert parts
    assert "🌳" not in parts[0]
    assert parts[0].startswith("x")


def test_scene_emoji_on_first_part_when_room():
    parts = format_multipart(
        "West of House.",
        max_bytes=145,
        max_chunks=4,
        scene_emoji="🌳🏡🌳",
    )
    assert parts[0].startswith("🌳🏡🌳 ")


def test_timer_prefix_and_ascii_fallback():
    parts = format_multipart(
        "Oh no!",
        max_bytes=145,
        max_chunks=4,
        timer_prefix="⏱️",
    )
    assert parts[0].startswith("⏱️ ")
    # Oversized custom prefix fails → ASCII [timer] fallback
    tiny = format_multipart(
        "Die!",
        max_bytes=20,
        max_chunks=4,
        timer_prefix="⏱️" * 5,
    )
    assert tiny
    assert tiny[0].startswith("[timer] ")


# ------------------------------------------- continuation labels on replies


def test_single_part_reply_carries_no_marker():
    parts = format_reply("You are in a small room.", "", 145, 8)
    assert len(parts) == 1
    assert "…" not in parts[0]
    assert not parts[0].endswith("/1")


def test_multipart_reply_marks_every_part():
    story = "word " * 120
    parts = format_reply(story, "", 145, 8)
    assert len(parts) > 1
    total = len(parts)
    for index, part in enumerate(parts[:-1], start=1):
        assert part.endswith(f" {index}/{total}…"), part
    assert parts[-1].endswith(f" .{total}/{total}")


def test_short_story_plus_exits_are_unlabeled():
    """Exits is a tip DM — must not invent a false 1/2… sequence."""
    story = "A small clearing in the forest. Paths lead out. You see: pile of leaves."
    parts = format_reply(story, "Exits: north, south, west.", 145, 8)
    assert len(parts) == 2
    assert "Exits:" in parts[1]
    assert not any("1/2" in p or "2/2" in p for p in parts)
    assert not parts[0].endswith("…")
    assert not parts[1].endswith("…")


def test_long_story_labels_exclude_exits_tip():
    story = "word " * 60
    parts = format_reply(story, "Exits: north, south.", 145, 8)
    assert len(parts) > 1
    assert "Exits" in parts[-1]
    assert "1/" not in parts[-1] and "2/" not in parts[-1] and "3/" not in parts[-1]
    story_parts = parts[:-1]
    total = len(story_parts)
    assert total >= 1
    if total == 1:
        assert not story_parts[0].endswith("…")
    else:
        for index, part in enumerate(story_parts[:-1], start=1):
            assert part.endswith(f" {index}/{total}…"), part
        assert story_parts[-1].endswith(f" .{total}/{total}")


def test_markers_stay_inside_the_byte_budget():
    story = "supercalifragilistic " * 40
    parts = format_reply(story, "Exits: north.", 145, 8)
    assert all(utf8_len(p) <= 145 for p in parts)


def test_reply_respects_the_chunk_ceiling():
    parts = format_reply("word " * 2000, "", 145, 4)
    assert len(parts) <= 4
    assert parts[-1].endswith(f" .{len(parts)}/{len(parts)}")


def test_scene_emoji_and_timer_prefix_lead_the_first_part():
    parts = format_reply("You are here.", "", 145, 8, scene_emoji="🏚", timer_prefix="⏱️")
    assert parts[0].startswith("⏱️") or "🏚" in parts[0]


def test_ui_only_reply_is_still_delivered():
    parts = format_reply("", "Score: 10. Moves: 4.", 145, 8)
    assert parts
    assert "Score" in " ".join(parts)
