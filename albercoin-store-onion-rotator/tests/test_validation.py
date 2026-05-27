import os
import pytest


def test_onion_v3_regex():
    from config import ONION_V3_REGEX
    valid = "a" * 56 + ".onion"
    assert ONION_V3_REGEX.match(valid)

    invalid_short = "short.onion"
    assert not ONION_V3_REGEX.match(invalid_short)

    invalid_chars = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa!@#$%aaaaaaa.onion"
    assert not ONION_V3_REGEX.match(invalid_chars)

    no_onion = "abcdefghijklmnopqrstuvwxyz234567abcdefghijklmnopqrstuvwxyz23"
    assert not ONION_V3_REGEX.match(no_onion)


def test_app_id_regex():
    from config import APP_ID_REGEX
    assert APP_ID_REGEX.match("bitcoin")
    assert APP_ID_REGEX.match("my-app_123")
    assert APP_ID_REGEX.match("a")
    assert APP_ID_REGEX.match("a1b2-c3d4_e5f6")

    assert not APP_ID_REGEX.match("")
    assert not APP_ID_REGEX.match("../path")
    assert not APP_ID_REGEX.match("app id")
    assert not APP_ID_REGEX.match("app/id")


def test_is_safe_path(monkeypatch_tor_dir):
    from detector import _is_safe_path
    safe = os.path.join(monkeypatch_tor_dir, "app-bitcoin", "hostname")
    assert _is_safe_path(safe)

    unsafe = "/etc/passwd"
    assert not _is_safe_path(unsafe)


def test_path_traversal_blocked(monkeypatch_tor_dir):
    from detector import _is_safe_path
    traversal = os.path.join(monkeypatch_tor_dir, "..", "..", "etc", "passwd")
    assert not _is_safe_path(traversal)


def test_extract_app_id():
    from detector import _extract_app_id
    assert _extract_app_id("app-bitcoin") == "bitcoin"
    assert _extract_app_id("app-my-app_123") == "my-app_123"

    assert _extract_app_id("bitcoin") is None
    assert _extract_app_id("") is None
    assert _extract_app_id("app-../etc") is None
