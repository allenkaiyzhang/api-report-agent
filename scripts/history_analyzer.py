from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.history_context import build_history_context


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build compact metrics history context for one market/date.")
    parser.add_argument("--market", required=True, help="Market code, for example US or HK.")
    parser.add_argument("--date", required=True, help="Trading date, for example 2026-05-12.")
    parser.add_argument("--lookback-days", type=int, default=5, help="Number of prior metrics days to include.")
    parser.add_argument("--base-dir", default=str(PROJECT_ROOT), help="Project base directory.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = build_history_context(
        base_dir=Path(args.base_dir),
        market=args.market,
        trading_date=args.date,
        lookback_days=args.lookback_days,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
