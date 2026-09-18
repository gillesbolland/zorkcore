"""Parser unit tests."""

from zorcore.engine.parser import parse


def test_directions_and_look():
    assert parse("n").verb == "go"
    assert parse("n").noun == "north"
    assert parse("look").verb == "look"
    assert parse("l").verb == "look"


def test_resend_phrases():
    assert parse("hello?").verb == "hello?"
    assert parse("hello ?").verb == "hello?"
    assert parse("?").verb == "hello?"
    assert parse("hello").verb == "hello?"
    assert parse("again").verb == "again"
    assert parse("g").verb == "again"
    assert parse("repeat").verb == "again"


def test_start_phrases():
    for phrase in ("new game", "new", "start", "start game", "play", "play game"):
        cmd = parse(phrase)
        assert cmd.verb == "new"
        assert cmd.noun == "game"


def test_start_phrases_ignore_case():
    for phrase in ("New game", "NEW GAME", "New Game", "Play", "START"):
        cmd = parse(phrase)
        assert cmd.verb == "new", phrase
        assert cmd.noun == "game", phrase


def test_no_pause_resume():
    assert parse("pause").verb == "pause" or parse("pause").verb != "hello?"
    # pause is no longer a meta verb — treated as unknown world verb
    assert parse("pause").verb == "pause"
    assert parse("resume").verb == "resume"


def test_take_aliases():
    assert parse("get lamp").verb == "take"
    assert parse("get lamp").noun == "lamp"
