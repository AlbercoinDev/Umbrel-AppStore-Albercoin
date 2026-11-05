from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from elite.settings import (
    FIELDS_BY_KEY,
    Settings,
    SettingsError,
    default_label,
    format_value,
    load_settings,
    parse_form,
    parse_setup_form,
    save_settings,
    validate_settings,
)


class SettingsTests(unittest.TestCase):
    def test_defaults_are_omitted(self) -> None:
        form = {"network": ["signet"]}
        for key in FIELDS_BY_KEY:
            form[f"default__{key}"] = ["1"]
            form[f"value__{key}"] = ["999"]
        settings, errors = parse_form(form)
        self.assertEqual(errors, [])
        self.assertEqual(settings.network, "signet")
        self.assertEqual(settings.overrides, {})

    def test_manual_values(self) -> None:
        form = {
            "network": ["mainnet"],
            "value__index.profile": ["ssd"],
            "value__index.cache_mib": ["2048"],
            "value__rawtx.use_p2p": ["false"],
            "value__rawtx.p2p_peers": ["1.2.3.4:8333\n[2001:db8::1]:8333"],
        }
        settings, errors = parse_form(form)
        self.assertEqual(errors, [])
        self.assertEqual(settings.overrides["index.profile"], "ssd")
        self.assertEqual(settings.overrides["index.cache_mib"], 2048)
        self.assertIs(settings.overrides["rawtx.use_p2p"], False)
        self.assertEqual(len(settings.overrides["rawtx.p2p_peers"]), 2)

    def test_invalid_enum_and_range(self) -> None:
        form = {
            "network": ["signet"],
            "value__index.profile": ["floppy"],
            "value__server.max_clients": ["0"],
        }
        _, errors = parse_form(form)
        self.assertTrue(any("index.profile" in error for error in errors))
        self.assertTrue(any("server.max_clients" in error for error in errors))

    def test_prune_constraints(self) -> None:
        settings = Settings(network="signet", overrides={"core.prune_mib": 550}, setup_complete=True)
        errors = validate_settings(settings)
        self.assertTrue(any("allow_auto_prune" in error for error in errors))
        settings.overrides["pruning.allow_auto_prune"] = True
        self.assertFalse(any("allow_auto_prune" in error for error in validate_settings(settings)))

        settings.overrides["core.prune_mib"] = 200
        self.assertTrue(any("core.prune_mib" in error for error in validate_settings(settings)))

    def test_extra_args_validation(self) -> None:
        settings = Settings(
            network="signet",
            overrides={"core.extra_args": ["-dbcache=256", "not-an-arg"]},
            setup_complete=True,
        )
        errors = validate_settings(settings)
        self.assertTrue(any("core.extra_args" in error for error in errors))

    def test_setup_requires_mainnet_ack(self) -> None:
        settings, errors = parse_setup_form({"network": ["mainnet"]})
        self.assertFalse(settings.setup_complete)
        self.assertTrue(any("mainnet_ack" in error for error in errors))
        settings, errors = parse_setup_form({"network": ["mainnet"], "mainnet_ack": ["1"]})
        self.assertEqual(errors, [])
        self.assertTrue(settings.setup_complete)

    def test_save_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            settings = Settings(network="testnet4", overrides={"index.profile": "nvme"}, setup_complete=True)
            save_settings(path, settings)
            loaded = load_settings(path)
            self.assertEqual(loaded.network, "testnet4")
            self.assertEqual(loaded.overrides, {"index.profile": "nvme"})
            self.assertTrue(loaded.setup_complete)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_load_rejects_unknown_and_invalid_without_bricking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text('{"network":"signet","setup_complete":true,"overrides":{"bad.key":1,"server.max_clients":0}}')
            loaded = load_settings(path)
            self.assertEqual(loaded.network, "signet")
            self.assertEqual(loaded.overrides, {})

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
