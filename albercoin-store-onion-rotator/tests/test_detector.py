import os
import pytest


def test_scan_apps_finds_valid_onions(monkeypatch_tor_dir):
    from detector import scan_apps
    apps = scan_apps()
    app_ids = {a["app_id"] for a in apps}
    assert "bitcoin" in app_ids
    assert "electrs" in app_ids
    assert "lnd" in app_ids


def test_scan_apps_onion_content(monkeypatch_tor_dir):
    from detector import scan_services
    services = scan_services()
    bitcoin = next(a for a in services if a["service_id"] == "bitcoin")
    assert bitcoin["onion_address"].endswith(".onion")
    assert len(bitcoin["onion_address"]) == 56 + len(".onion")


def test_scan_apps_status_available(monkeypatch_tor_dir):
    from detector import scan_services
    services = scan_services()
    for service in services:
        if service["service_id"] in ("bitcoin", "electrs", "lnd"):
            assert service["status"] == "available"


def test_scan_apps_invalid_app_id_has_no_onion(monkeypatch_tor_dir):
    from detector import scan_services
    services = scan_services(include_unavailable=True)
    invalid = [a for a in services if a["service_id"] == "invalid"]
    assert len(invalid) == 0


def test_scan_apps_empty_dir(tmp_path):
    import config
    config.REAL_TOR_DATA_DIR = None
    import detector
    import importlib
    importlib.reload(detector)

    empty_dir = tmp_path / "empty_tor"
    empty_dir.mkdir()
    config.TOR_DATA_DIR = str(empty_dir)
    config.REAL_TOR_DATA_DIR = None
    importlib.reload(detector)

    apps = detector.scan_apps()
    assert apps == []


def test_read_hostname_invalid_onion(monkeypatch_tor_dir):
    hostname_path = os.path.join(monkeypatch_tor_dir, "app-invalid", "hostname")
    from detector import _read_hostname
    result = _read_hostname(hostname_path)
    assert result is None


def test_read_hostname_nonexistent():
    from detector import _read_hostname
    result = _read_hostname("/nonexistent/path")
    assert result is None


def test_scan_apps_filters_to_installed_ids(monkeypatch_tor_dir):
    from detector import scan_services
    services = scan_services({"bitcoin", "lnd"})
    service_ids = {a["service_id"] for a in services}
    assert service_ids == {"bitcoin", "lnd"}


def test_scan_apps_maps_auxiliary_service_to_owner(monkeypatch_tor_dir):
    from detector import scan_services
    services = scan_services({"electrs"})
    service_map = {a["service_id"]: a for a in services}
    assert "electrs" in service_map
    assert "electrs-rpc" in service_map
    assert service_map["electrs-rpc"]["app_id"] == "electrs"


def test_scan_apps_groups_services_by_app(monkeypatch_tor_dir):
    from detector import scan_apps
    apps = scan_apps()
    electrs = next(a for a in apps if a["app_id"] == "electrs")
    service_ids = {s["service_id"] for s in electrs["services"]}
    assert {"electrs", "electrs-rpc"}.issubset(service_ids)


def test_get_app_data_app_ids(monkeypatch, temp_app_data_dir):
    import config
    import detector
    import importlib
    monkeypatch.setattr(config, "UMBREL_APP_DATA_DIR", temp_app_data_dir)
    importlib.reload(detector)

    app_ids = detector.get_app_data_app_ids()
    assert {"bitcoin", "electrs", "lnd"}.issubset(app_ids)
