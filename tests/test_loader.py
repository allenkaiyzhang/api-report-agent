from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.loader import load_symbols


class LoaderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = PROJECT_ROOT / "tests" / "loader_output_test"
        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir)
        self.tmp_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir)

    def test_load_symbols_json_object_rows(self) -> None:
        path = self.tmp_dir / "symbols.json"
        path.write_text(
            json.dumps(
                {
                    "symbols": [
                        {"symbol": "QQQ.US", "enabled": True},
                        {"symbol": "DISABLED.US", "enabled": False},
                    ]
                }
            ),
            encoding="utf-8",
        )

        rows = load_symbols(path)

        self.assertEqual([row["symbol"] for row in rows], ["QQQ.US"])
        self.assertEqual(rows[0]["enabled"], "true")

    def test_load_symbols_json_string_rows(self) -> None:
        path = self.tmp_dir / "symbols.json"
        path.write_text(json.dumps({"symbols": ["QQQ.US", "0700.HK"]}), encoding="utf-8")

        rows = load_symbols(path)

        self.assertEqual([row["symbol"] for row in rows], ["QQQ.US", "0700.HK"])

    def test_load_symbols_from_registry_yaml(self) -> None:
        path = self.tmp_dir / "registry.yaml"
        path.write_text(
            "\n".join(
                [
                    "symbols:",
                    "  - symbol: QQQ.US",
                    "    market: US",
                    "    sessions:",
                    "      - regular",
                    "      - extended",
                    "    enabled: true",
                    "  - symbol: DISABLED.US",
                    "    enabled: false",
                ]
            ),
            encoding="utf-8",
        )

        rows = load_symbols(path)

        self.assertEqual([row["symbol"] for row in rows], ["QQQ.US"])
        self.assertEqual(rows[0]["sessions"], ["regular", "extended"])


if __name__ == "__main__":
    unittest.main()
