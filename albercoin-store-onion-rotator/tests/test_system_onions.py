import os


def test_scan_system_onions(monkeypatch_tor_dir):
    from system_onions import scan_system_onions

    services = scan_system_onions()
    service_map = {s["id"]: s for s in services}

    assert set(service_map) == {"web", "auth"}
    assert service_map["web"]["status"] == "available"
    assert service_map["auth"]["onion_address"].endswith(".onion")


def test_delete_system_onion_contents(monkeypatch_tor_dir):
    from system_onions import delete_system_onion_contents

    web_dir = os.path.join(monkeypatch_tor_dir, "web")
    assert os.path.isdir(web_dir)
    assert os.path.exists(os.path.join(web_dir, "hostname"))

    result = delete_system_onion_contents("web")

    assert result["status"] == "deleted"
    assert os.path.isdir(web_dir)
    assert os.listdir(web_dir) == []


def test_delete_system_onion_rejects_unknown(monkeypatch_tor_dir):
    from system_onions import delete_system_onion_contents

    result = delete_system_onion_contents("../web")
    assert result["status"] == "error"
    assert result["message"] == "invalid_system_service"
