from __future__ import annotations

import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]


class TorrcTests(unittest.TestCase):
    def test_hidden_service_targets_container_hostname(self) -> None:
        contents = (APP_DIR / "torrc").read_text(encoding="utf-8")
        self.assertIn("HiddenServicePort 55011 e-lite:55011", contents)
        self.assertIn("HiddenServicePort 55022 e-lite:55022", contents)
        self.assertNotIn(" app:", contents)


if __name__ == "__main__":
    unittest.main()
