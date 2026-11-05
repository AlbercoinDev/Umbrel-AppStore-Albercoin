from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from elite.supervisor import read_tail


class SupervisorTests(unittest.TestCase):
    def test_read_tail_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "log"
            path.write_text("\n".join(f"line {index}" for index in range(200)))
            lines = read_tail(path, limit=10)
            self.assertEqual(len(lines), 10)
            self.assertEqual(lines[-1], "line 199")

    def test_read_missing_log(self) -> None:
        self.assertEqual(read_tail(Path("/nonexistent/e-lite.log")), [])


if __name__ == "__main__":
    unittest.main()
