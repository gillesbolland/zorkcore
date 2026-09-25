"""Engine, timers, help, hello?/? tests against full adventure content."""

from zorcore.content_loader import load_game_data
from zorcore.engine.game import Game, SHORT_HELP


def _game(now=None) -> Game:
    return Game.from_game_data(load_game_data(), now=now)


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def test_new_opens_near_house():
    game = _game()
    s = game.new_session("aabb")
    assert "white house" in s.last_output.lower() or "west" in s.last_output.lower()
    assert s.last_story
    assert s.last_ui
    assert s.room_id == "whous"
    # Welcome matches room description — do not double it.
    assert s.last_story.count("open field west of a big white house") == 1


def test_new_game_restarts():
    game = _game()
    s = game.new_session("aabb")
    s = game.step(s, "open mailbox").session
    s = game.step(s, "take leaflet").session
    assert "leaflet" in s.inventory or "adver" in s.inventory
    r = game.step(s, "new game")
    assert r.milestone == "start"
    assert "leaflet" not in r.session.inventory and "adver" not in r.session.inventory
    assert r.session.room_id == "whous"
    assert r.story and "open field west of a big white house" in r.story
    assert r.session.brief_mode is False


def test_verbose_by_default_on_revisit():
    game = _game()
    s = game.new_session("aabb")
    s = game.step(s, "north").session
    assert s.room_id == "nhous"
    # Leave and return to west of house.
    s = game.step(s, "west").session
    assert s.room_id == "whous"
    assert "open field west of a big white house" in (s.last_story or s.last_output)
    assert "West of House." != (s.last_story or "").strip()


def test_brief_command_shortens_revisit():
    game = _game()
    s = game.new_session("aabb")
    s = game.step(s, "brief").session
    assert s.brief_mode is True
    s = game.step(s, "north").session
    s = game.step(s, "west").session
    assert s.room_id == "whous"
    story = (s.last_story or s.last_output or "").strip()
    assert story.startswith("West of House")
    assert "open field" not in story


def test_start_aliases_restart():
    game = _game()
    s = game.new_session("aabb")
    for phrase in ("new", "start", "play", "start game", "play game"):
        r = game.step(s, phrase)
        assert r.milestone == "start", phrase
        s = r.session


def test_help_text():
    game = _game()
    s = game.new_session("aabb")
    r = game.step(s, "help")
    assert "new game" in r.text.lower()
    assert "pause" not in r.text.lower()
    assert game.short_help()


def test_hello_and_question_resend():
    game = _game()
    s = game.new_session("aabb")
    original = s.last_output
    for phrase in ("hello?", "hello ?", "?"):
        result = game.step(s, phrase)
        assert result.resend_only
        assert result.text == original


def test_awol_timer_kills():
    clock = Clock()
    game = _game(now=clock)
    s = game.new_session("ccdd")
    # Enter dark attic without light
    for cmd in ["n", "e", "open window", "w", "u"]:
        s = game.step(s, cmd).session
    assert s.room_id == "attic"
    assert s.alive
    clock.advance(50)
    due = game.apply_due_timers(s)
    assert due is not None
    assert due.milestone == "death" or not due.session.alive
