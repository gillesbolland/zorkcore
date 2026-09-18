"""Session store and leaderboard."""

from pathlib import Path

from zorcore.content_loader import load_game_data
from zorcore.engine.game import Game
from zorcore.engine.sessions import SessionStore


def _game() -> Game:
    return Game.from_game_data(load_game_data())


def test_persist_roundtrip(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions")
    game = _game()
    s = game.new_session("deadbeefcafebabe")
    s = game.step(s, "open mailbox").session
    s = game.step(s, "take leaflet").session
    store.save(s)
    loaded = store.load("deadbeefcafebabe")
    assert loaded is not None
    assert "adver" in loaded.inventory or "leaflet" in loaded.inventory
    assert loaded.last_output


def test_migrate_truncated_and_empty(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions")
    full = "ab" * 32
    prefix = full[:12]
    path = store.root / f"{prefix}.json"
    path.write_text(
        '{"sender_key": "%s", "room_id": "whous", "inventory": [], '
        '"visited": [], "score": 1, "moves": 0, "alive": true}'
        % prefix,
        encoding="utf-8",
    )
    empty = store.root / "empty.json"
    empty.write_text(
        '{"sender_key": "", "room_id": "whous", "inventory": [], "visited": []}',
        encoding="utf-8",
    )
    n = store.migrate_truncated_keys({full, "cd" * 32})
    assert n >= 1
    assert store.load(full) is not None
    assert store.load(full).score == 1
    assert not empty.exists()


def test_leaderboard(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions")
    game = _game()
    a = game.new_session("aaa")
    a.score = 10
    store.save(a)
    b = game.new_session("bbb")
    b.score = 30
    store.save(b)
    board = store.leaderboard()
    assert board[0]["score"] == 30
