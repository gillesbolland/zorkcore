"""Advert-based contact sync helpers."""

from zorcore.contact_sync import (
    excluded_sync_keys,
    extract_chat_pubkeys_from_adverts,
    filter_player_keys,
    keys_to_add,
    normalize_pubkey,
    resolve_pubkey,
)


def test_normalize_pubkey():
    assert normalize_pubkey("AB" * 32) == "ab" * 32
    assert normalize_pubkey("0x" + "cd" * 32) == "cd" * 32
    assert len(normalize_pubkey("ff" * 40)) == 64


def test_resolve_pubkey_full_and_prefix():
    full = "aa" * 32
    other = "bb" * 32
    assert resolve_pubkey(full, {full, other}) == full
    assert resolve_pubkey(full[:12], {full, other}) == full
    assert resolve_pubkey("cc", {full}) == ""
    assert resolve_pubkey(full[:12], {full, full[:12] + "ff" * 26}) == ""  # ambiguous


def test_excluded_and_filter_player_keys():
    self_key = "11" * 32
    player = "33" * 32
    excl = excluded_sync_keys(self_key=self_key)
    assert excl == {self_key}
    assert filter_player_keys({self_key, player}, excl) == {player}


def test_extract_chat_pubkeys_freshest_limit():
    payload = {
        "data": [
            {"pubkey": "aa" * 32, "last_seen": 100, "contact_type": "Chat Node"},
            {"pubkey": "bb" * 32, "last_seen": 300, "contact_type": "Chat Node"},
            {"pubkey": "cc" * 32, "timestamp": 200, "contact_type": "Chat Node"},
            {"pubkey": "dd" * 32, "last_seen": 400, "contact_type": "Repeater"},
        ]
    }
    keys = extract_chat_pubkeys_from_adverts(payload, limit=2)
    assert keys == ["bb" * 32, "cc" * 32]


def test_extract_dedupes_by_freshest():
    payload = [
        {"pubkey": "aa" * 32, "last_seen": 10, "contact_type": "Chat Node"},
        {"pubkey": "aa" * 32, "last_seen": 50, "contact_type": "Chat Node"},
        {"pubkey": "bb" * 32, "last_seen": 40, "contact_type": "Chat Node"},
    ]
    keys = extract_chat_pubkeys_from_adverts(payload, limit=10)
    assert keys == ["aa" * 32, "bb" * 32]


def test_keys_to_add_skips_known():
    candidates = {"aa" * 32, "bb" * 32}
    known = {"AA" * 32}
    assert keys_to_add(candidates, known) == ["bb" * 32]


def test_prune_keep_logic_session_holders():
    """Session holders survive even when absent from advert set."""
    advert = {"aa" * 32}
    sessions = {"bb" * 32}
    known = {"aa" * 32, "bb" * 32, "cc" * 32}
    keep = advert | sessions
    prune = known - keep
    assert prune == {"cc" * 32}
