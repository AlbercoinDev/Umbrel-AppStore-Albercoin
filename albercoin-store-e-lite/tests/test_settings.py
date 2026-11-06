from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from elite.settings import (
    FIELDS_BY_KEY,
    NetworkConfig,
    Settings,
    SettingsError,
    default_label,
    format_value,
    load_settings,
    parse_form,
    parse_networks_form,
    parse_setup_form,
    save_settings,
    validate_network,
    validate_settings,
)


class SettingsTests(unittest.TestCase):
    def test_defaults_are_omitted_for_one_network(self) -> None:
        form = {}
        for key in FIELDS_BY_KEY:
            form[f"default__{key}"] = ["1"]
            form[f"value__{key}"] = ["999"]
        settings, errors = parse_form(form, "signet")
        self.assertEqual(errors, [])
        self.assertEqual(settings.network("signet").overrides, {})
        self.assertFalse(settings.network("mainnet").enabled)

    def test_manual_values(self) -> None:
        form = {
            "value__index.profile": ["ssd"],
            "value__index.cache_mib": ["2048"],
            "value__rawtx.use_p2p": ["false"],
            "value__rawtx.p2p_peers": ["1.2.3.4:8333\n[2001:db8::1]:8333"],
        }
        settings, errors = parse_form(form, "testnet4")
        self.assertEqual(errors, [])
        config = settings.network("testnet4")
        self.assertEqual(config.overrides["index.profile"], "ssd")
        self.assertEqual(config.overrides["index.cache_mib"], 2048)
        self.assertIs(config.overrides["rawtx.use_p2p"], False)
        self.assertEqual(len(config.overrides["rawtx.p2p_peers"]), 2)

    def test_invalid_enum_and_range(self) -> None:
        form = {
            "value__index.profile": ["floppy"],
            "value__server.max_clients": ["0"],
        }
        _, errors = parse_form(form, "signet")
        self.assertTrue(any("index.profile" in error for error in errors))
        self.assertTrue(any("server.max_clients" in error for error in errors))

    def test_prune_constraints(self) -> None:
        config = NetworkConfig(overrides={"core.prune_mib": 550})
        errors = validate_network("signet", config)
        self.assertTrue(any("allow_auto_prune" in error for error in errors))
        config.overrides["pruning.allow_auto_prune"] = True
        self.assertFalse(any("allow_auto_prune" in error for error in validate_network("signet", config)))
        config.overrides["core.prune_mib"] = 200
        self.assertTrue(any("core.prune_mib" in error for error in validate_network("signet", config)))

    def test_extra_args_validation(self) -> None:
        config = NetworkConfig(overrides={"core.extra_args": ["-dbcache=256", "not-an-arg"]})
        errors = validate_network("signet", config)
        self.assertTrue(any("core.extra_args" in error for error in errors))

    def test_setup_requires_one_network_and_mainnet_ack(self) -> None:
        settings, errors = parse_setup_form({"enabled_signet": ["1"]})
        self.assertTrue(settings.setup_complete)
        self.assertTrue(settings.network("signet").enabled)
        self.assertEqual(errors, [])

        settings, errors = parse_setup_form({})
        self.assertFalse(settings.setup_complete)
        self.assertTrue(any("networks" in error for error in errors))

        settings, errors = parse_setup_form({"enabled_mainnet": ["1"]})
        self.assertFalse(settings.setup_complete)
        self.assertTrue(any("mainnet_ack" in error for error in errors))

        settings, errors = parse_setup_form({"enabled_mainnet": ["1"], "mainnet_ack": ["1"]})
        self.assertTrue(settings.setup_complete)
        self.assertEqual(errors, [])

    def test_save_and_load_per_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            settings = Settings(setup_complete=True)
            settings.network("testnet4").enabled = True
            settings.network("testnet4").overrides = {"index.profile": "nvme"}
            settings.network("testnet4").public_hostnames = ["elite.example.org"]
            save_settings(path, settings)
            loaded = load_settings(path)
            self.assertTrue(loaded.setup_complete)
            self.assertTrue(loaded.network("testnet4").enabled)
            self.assertFalse(loaded.network("signet").enabled)
            self.assertEqual(loaded.network("testnet4").overrides, {"index.profile": "nvme"})
            self.assertEqual(loaded.network("testnet4").public_hostnames, ["elite.example.org"])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_optional_rate_limit(self) -> None:
        settings, errors = parse_form({}, "signet")
        self.assertEqual(errors, [])
        self.assertNotIn("server.rate_limit", settings.network("signet").overrides)
        settings, errors = parse_form({"value__server.rate_limit": ["100"], "value__server.rate_burst": ["600"]}, "signet")
        self.assertEqual(errors, [])
        self.assertEqual(settings.network("signet").overrides["server.rate_limit"], 100)
        self.assertIsInstance(settings.network("signet").overrides["server.rate_limit"], int)
        self.assertEqual(settings.network("signet").overrides["server.rate_burst"], 600)
        self.assertIsInstance(settings.network("signet").overrides["server.rate_burst"], int)
        settings, errors = parse_form({"value__server.rate_limit": ["0"]}, "signet")
        self.assertEqual(errors, [])
        self.assertEqual(settings.network("signet").overrides["server.rate_limit"], 0)

    def test_public_hostnames(self) -> None:
        form = {"public_hostnames": ["elite.example.org, otro.example.net\nelite.example.org"]}
        settings, errors = parse_form(form, "signet")
        self.assertEqual(errors, [])
        self.assertEqual(settings.network("signet").public_hostnames, ["elite.example.org", "otro.example.net"])

    def test_invalid_public_hostname(self) -> None:
        settings, errors = parse_form({"public_hostnames": ["not a hostname"]}, "signet")
        self.assertTrue(any("public_hostnames" in error for error in errors))
        self.assertEqual(settings.network("signet").public_hostnames, [])

    def test_old_schema_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text('{"network":"signet","setup_complete":true,"overrides":{"index.profile":"nvme"}}')
            loaded = load_settings(path)
            self.assertFalse(loaded.setup_complete)
            self.assertFalse(loaded.network("signet").enabled)
            self.assertEqual(loaded.network("signet").overrides, {})

    def test_load_rejects_unknown_and_invalid_without_bricking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(
                '{"schema":2,"setup_complete":true,"networks":{"signet":{"enabled":true,'
                '"overrides":{"bad.key":1,"server.max_clients":0},"public_hostnames":[]}}}'
            )
            loaded = load_settings(path)
            self.assertTrue(loaded.network("signet").enabled)
            self.assertEqual(loaded.network("signet").overrides, {})

    def test_validate_settings_all_networks(self) -> None:
        settings = Settings(setup_complete=True)
        settings.network("mainnet").overrides = {"core.prune_mib": 200}
        self.assertTrue(any(error.startswith("mainnet:") for error in validate_settings(settings)))

    def test_format_and_labels(self) -> None:
        self.assertEqual(default_label(FIELDS_BY_KEY["index.adaptive"]), "true")
        self.assertEqual(format_value(FIELDS_BY_KEY["rawtx.p2p_peers"], ["a:1", "b:2"]), "a:1\nb:2")

    def test_invalid_json_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text("{")
            with self.assertRaises(SettingsError):
                load_settings(path)


if __name__ == "__main__":
    unittest.main()
