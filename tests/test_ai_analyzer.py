from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.ai_analyzer import AIAnalysisConfig, analyze_market_report, build_analysis_prompt


class AIAnalyzerTest(unittest.TestCase):
    def test_disabled_ai_returns_empty_analysis(self) -> None:
        config = AIAnalysisConfig.from_env({})

        self.assertEqual(analyze_market_report(config, {"market": "US"}), "")

    def test_prompt_includes_payload_without_inventing_data_instruction(self) -> None:
        prompt = build_analysis_prompt({"market": "US", "raw_lines": 10})

        self.assertIn("Do not invent data", prompt)
        self.assertIn('"raw_lines": 10', prompt)

    def test_mock_provider_returns_default_report_without_api_key(self) -> None:
        config = AIAnalysisConfig.from_env({"AI_ANALYSIS_ENABLED": "true", "AI_PROVIDER": "mock"})

        analysis = analyze_market_report(
            config,
            {
                "report_type": "intraday",
                "market": "US",
                "trading_date": "2026-05-07",
                "raw_lines": 10,
                "normalized_lines": 10,
                "symbol_count": 2,
                "raw_json_parse_errors": 0,
            },
        )

        self.assertIn("Mock analysis", analysis)
        self.assertIn("raw=10", analysis)


if __name__ == "__main__":
    unittest.main()
