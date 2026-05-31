from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.ai_analyzer import AIAnalysisConfig
from core.config_registry import apply_registry_to_env
from core.data_pipeline import BASE_DIR, daily_day, metrics_day, normalize_day, quality_day
from core.post_market.ai_narrative import generate_ai_narrative
from core.post_market.archive import archive_raw
from core.post_market.feature_generation import generate_features
from core.post_market.finalizer import finalize_market_day
from core.post_market.health_report import generate_health_report
from core.post_market.market_summary import generate_market_summary
from core.post_market.timeline_replay import generate_timeline_replay


def run_post_market_pipeline(market: str, trading_date: str, base_dir: Path = BASE_DIR) -> dict[str, str]:
    normalize_day(market, trading_date, base_dir=base_dir)
    metrics_day(market, trading_date, base_dir=base_dir)
    daily_day(market, trading_date, base_dir=base_dir)
    quality_day(market, trading_date, base_dir=base_dir)
    finalizer_result = finalize_market_day(market, trading_date, base_dir=base_dir)
    summary_path = generate_market_summary(market, trading_date, base_dir=base_dir)
    timeline_path = generate_timeline_replay(market, trading_date, base_dir=base_dir)
    features_path = generate_features(market, trading_date, base_dir=base_dir)
    archive_path = archive_raw(market, trading_date, base_dir=base_dir)
    health_path = generate_health_report(market, trading_date, base_dir=base_dir)
    ai_path = generate_ai_narrative(market, trading_date, AIAnalysisConfig.from_env(os.environ), base_dir=base_dir)
    return {
        "finalizer": str(finalizer_result),
        "market_summary": str(summary_path),
        "timeline": str(timeline_path),
        "features": str(features_path),
        "archive_manifest": str(archive_path),
        "health": str(health_path),
        "ai_summary": str(ai_path),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run post-market offline processing")
    parser.add_argument("--market", required=True, choices=["HK", "US"])
    parser.add_argument("--date", required=True)
    return parser.parse_args()


def main() -> None:
    load_dotenv(BASE_DIR / ".env")
    apply_registry_to_env(override=True)
    args = parse_args()
    outputs = run_post_market_pipeline(args.market, args.date)
    for name, path in outputs.items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()
