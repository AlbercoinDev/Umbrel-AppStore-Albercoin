import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _make_onion(seed: str) -> str:
    chars = "abcdefghijklmnopqrstuvwxyz234567"
    result = []
    for i, c in enumerate(seed):
        result.append(chars[ord(c) % 32])
    while len(result) < 56:
        result.append(chars[len(result) % 32])
    return "".join(result[:56]) + ".onion"


@pytest.fixture
def tor_fixtures():
    base = os.path.join(os.path.dirname(__file__), "fixtures", "tor")
    return base


@pytest.fixture
def temp_tor_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        tor_dir = os.path.join(tmpdir, "tor", "data")
        os.makedirs(tor_dir, exist_ok=True)

        app_dirs = ["app-bitcoin", "app-electrs", "app-lnd", "app-invalid"]
        onions = {
            "app-bitcoin": _make_onion("bitcoin"),
            "app-electrs": _make_onion("electrs"),
            "app-lnd": _make_onion("lndnode"),
        }

        for app_dir in app_dirs:
            os.makedirs(os.path.join(tor_dir, app_dir), exist_ok=True)
            if app_dir in onions:
                hostname_path = os.path.join(tor_dir, app_dir, "hostname")
                with open(hostname_path, "w") as f:
                    f.write(onions[app_dir] + "\n")

        yield tor_dir


@pytest.fixture
def monkeypatch_tor_dir(monkeypatch, temp_tor_dir):
    import config
    config.REAL_TOR_DATA_DIR = None
    monkeypatch.setattr(config, "TOR_DATA_DIR", temp_tor_dir)
    monkeypatch.setattr(config, "REAL_TOR_DATA_DIR", None)
    import detector
    import importlib
    importlib.reload(detector)
    return temp_tor_dir
