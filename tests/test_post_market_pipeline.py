from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.post_market_pipeline import run_post_market_pipeline


class PostMarketPipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base_dir = PROJECT_ROOT / "tests" / "post_market_output_test"
        if self.base_dir.exists():
            shutil.rmtree(self.base_dir)
        raw_dir = self.base_dir / "data" / "raw" / "US"
        raw_dir.mkdir(parents=True)
        rows = [
            {
                "collected_at": "2026-05-07T09:30:00-04:00",
                "provider": "mock",
                "market": "US",
                "symbol": "QQQ.US",
                "timestamp": "2026-05-07T09:30:00-04:00",
                "last_price": 100,
                "volume": 1000,
                "turnover": 100000,
                "currency": "USD",
            },
            {
                "collected_at": "2026-05-07T09:32:00-04:00",
                "provider": "mock",
                "market": "US",
                "symbol": "QQQ.US",
                "timestamp": "2026-05-07T09:32:00-04:00",
                "last_price": 102,
                "volume": 1500,
                "turnover": 153000,
                "currency": "USD",
            },
        ]
        (raw_dir / "2026-05-07.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def tearDown(self) -> None:
        if self.base_dir.exists():
            shutil.rmtree(self.base_dir)

    def test_post_market_pipeline_generates_outputs_and_finalizes(self) -> None:
        outputs = run_post_market_pipeline("US", "2026-05-07", base_dir=self.base_dir)

        daily = json.loads((self.base_dir / "data" / "metrics" / "US" / "2026-05-07" / "daily.json").read_text(encoding="utf-8"))
        manifest = json.loads((self.base_dir / "data" / "archive" / "raw" / "US" / "2026-05-07.manifest.json").read_text(encoding="utf-8"))

        self.assertTrue(daily["finalized"])
        self.assertIn("finalized_at", daily)
        self.assertTrue((self.base_dir / "data" / "reports" / "US" / "2026-05-07_market_summary.json").exists())
        self.assertTrue((self.base_dir / "data" / "reports" / "US" / "2026-05-07_timeline.json").exists())
        self.assertTrue((self.base_dir / "data" / "reports" / "US" / "2026-05-07_health.json").exists())
        self.assertTrue((self.base_dir / "data" / "features" / "US" / "2026-05-07.json").exists())
        self.assertTrue(manifest["compressed"])
        self.assertIn("ai_summary", outputs)


if __name__ == "__main__":
    unittest.main()
