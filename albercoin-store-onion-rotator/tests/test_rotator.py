import os
import pytest


def test_validate_app_id_valid():
    from rotator import validate_app_id
    assert validate_app_id("bitcoin") is None
    assert validate_app_id("my-app_123") is None
    assert validate_app_id("a") is None


def test_validate_app_id_invalid():
    from rotator import validate_app_id
    assert validate_app_id("") is not None
    assert validate_app_id("../etc/passwd") is not None
    assert validate_app_id("app with spaces") is not None
    assert validate_app_id("app/bitcoin") is not None


def test_delete_hostname_safe_path(monkeypatch_tor_dir):
    from rotator import delete_hostname
    hostname_path = os.path.join(monkeypatch_tor_dir, "app-bitcoin", "hostname")
    ok, msg = delete_hostname(hostname_path)
    assert ok
    assert not os.path.exists(hostname_path)


def test_delete_hostname_path_traversal():
    from rotator import delete_hostname
    ok, msg = delete_hostname("/etc/passwd")
    assert not ok
    assert "path_traversal" in msg


def test_delete_hostname_nonexistent(monkeypatch_tor_dir):
    from rotator import delete_hostname
    ok, msg = delete_hostname(os.path.join(monkeypatch_tor_dir, "nonexistent", "hostname"))
    assert not ok


def test_rotate_single_dry_run(monkeypatch, monkeypatch_tor_dir):
    import config
    import restarter
    import rotator
    import importlib

    monkeypatch.setattr(config, "DRY_RUN", True)
    monkeypatch.setattr(restarter, "DRY_RUN", True)
    importlib.reload(rotator)
    importlib.reload(restarter)

    hostname_path = os.path.join(monkeypatch_tor_dir, "app-bitcoin", "hostname")
    result = rotator.rotate_single("bitcoin", hostname_path, "old.onion")
    assert result["app_id"] == "bitcoin"
