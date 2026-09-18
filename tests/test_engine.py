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
