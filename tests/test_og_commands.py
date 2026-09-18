"""OG command surface: ALL/EXCEPT/AND, pronouns, look-under, again, save/restore, quit."""

from pathlib import Path

from zorcore.content_loader import load_game_data
from zorcore.engine.checkpoints import CheckpointStore
from zorcore.engine.game import Game, SHORT_HELP
from zorcore.engine.parser import parse


def _game(tmp_path: Path | None = None) -> Game:
    store = CheckpointStore(tmp_path / "checkpoints") if tmp_path else None
    return Game.from_game_data(load_game_data(), checkpoint_store=store)


def _to_living(game: Game, key: str = "aa"):
    s = game.new_session(key)
    for c in ["n", "e", "open window", "w", "w"]:
        s = game.step(s, c).session
    return s


def test_parse_batch_except_and_lists():
    v = load_game_data().verbs
    cmd = parse("take all except lamp", v)
    assert cmd.verb == "take"
    assert cmd.batch == "all"
    assert cmd.except_nouns == ("lamp",)

    cmd = parse("take lamp, sword and paper", v)
    assert cmd.nouns == ("lamp", "sword", "paper")

    cmd = parse("drop valuables but not lamp", v)
    assert cmd.batch == "valuables"
    assert "lamp" in cmd.except_nouns


def test_parse_look_phrases_and_again():
    v = load_game_data().verbs
    assert parse("look under rug", v).verb == "look_under"
    assert parse("look behind door", v).verb == "look_prep"
    assert parse("look at lamp", v).verb == "examine"
    assert parse("again", v).verb == "again"
    assert parse("g", v).verb == "again"
    assert parse("repeat", v).verb == "again"
    assert parse("?", v).verb == "hello?"
    assert parse("hello", v).verb == "hello?"
    assert parse("hello?", v).verb == "hello?"


def test_take_all_except_and_lists(tmp_path: Path):
    game = _game(tmp_path)
    s = _to_living(game)
    r = game.step(s, "take all except lamp")
    s = r.session
    assert "lamp" not in s.inventory
    assert "sword" in s.inventory
    assert "Taken." in r.text
    assert "lamp" not in r.text.lower() or "except" not in r.text.lower()

    s = game.step(s, "drop all").session
    r = game.step(s, "take lamp and sword")
    s = r.session
    assert set(s.inventory) >= {"lamp", "sword"}
    assert "lamp: Taken." in r.text.lower() or "Taken." in r.text


def test_pronouns_it_them(tmp_path: Path):
    game = _game(tmp_path)
    s = _to_living(game)
    s = game.step(s, "take lamp and sword").session
    assert s.last_them == ["lamp", "sword"]
    r = game.step(s, "drop them")
    assert "Dropped." in r.text
    assert r.session.inventory == []
    s = game.step(r.session, "take lamp").session
    assert s.last_it == "lamp"
    r = game.step(s, "drop it")
    assert "Dropped." in r.text
    s = game.new_session("fresh")
    r = game.step(s, "drop it")
    assert "I don't know what you mean by 'it'" in r.text


def test_look_under_rug_and_leaves(tmp_path: Path):
    game = _game(tmp_path)
    s = _to_living(game)
    r = game.step(s, "look under rug")
    assert "trap door" in r.text.lower()

    s = game.new_session("leaves")
    s.room_id = "clear"
    s.room_objects["clear"] = list(game.world.rooms["clear"].objects)
    r = game.step(s, "look under leaves")
    assert "grating" in r.text.lower()

    r = game.step(s, "look through window")
    assert "nothing special" in r.text.lower()


def test_again_redos_last_command(tmp_path: Path):
    game = _game(tmp_path)
    s = _to_living(game)
    s = game.step(s, "take lamp").session
    assert "lamp" in s.inventory
    s = game.step(s, "drop lamp").session
    assert "lamp" not in s.inventory
    r = game.step(s, "again")
    # redo drop with empty — soft fail or empty
    assert r.session.last_command == "drop lamp"
    s = r.session
    s = game.step(s, "take lamp").session
    r = game.step(s, "inventory")
    assert "lamp" in r.text.lower()
    r = game.step(r.session, "g")
    assert "lamp" in r.text.lower()
    assert r.resend_only is False


def test_question_resends_not_redo(tmp_path: Path):
    game = _game(tmp_path)
    s = _to_living(game)
    s = game.step(s, "take lamp").session
    original = s.last_output
    r = game.step(s, "?")
    assert r.resend_only
    assert r.text == original
    assert "lamp" in r.session.inventory  # did not redo take


def test_hello_help_text():
    assert "take all" in SHORT_HELP.lower() or "again" in SHORT_HELP.lower()
    game = _game()
    s = game.new_session("hh")
    s.last_output = ""
    s.last_story = ""
    s.last_ui = ""
    r = game.hello(s)
    assert "new game" in r.text.lower()


def test_save_restore_quit(tmp_path: Path):
    game = _game(tmp_path)
    s = _to_living(game, "save1")
    s = game.step(s, "take lamp").session
    r = game.step(s, "save")
    assert r.text == "Saved."
    s = game.step(r.session, "e").session
    assert s.room_id != "lroom"
    r = game.step(s, "restore")
    assert "Restored." in r.text
    assert r.session.room_id == "lroom"
    assert "lamp" in r.session.inventory

    r = game.step(r.session, "quit")
    assert r.opt_out
    assert "Farewell" in r.text
    assert r.session.opted_out

    empty = _game(tmp_path / "empty")
    s = empty.new_session("nosave")
    assert empty.step(s, "restore").text == "No saved game."


def test_stub_verbs_understood(tmp_path: Path):
    game = _game(tmp_path)
    s = game.new_session("stub")
    for verb in ("yell", "pray", "xyzzy", "knock", "swim", "diagnose"):
        r = game.step(s, verb)
        assert "I don't understand" not in r.text, verb
        s = r.session
