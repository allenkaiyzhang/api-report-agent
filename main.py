from __future__ import annotations

import argparse
import sys

from core import data_pipeline
from scripts import cleanup, debug_chart, healthcheck, market_data_agent, replay, run_pipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Market Data Pipeline entrypoint")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("collect", help="run the market data collector")
    subparsers.add_parser("run", help="run the long-running stable pipeline")
    subparsers.add_parser("health", help="print pipeline health status")
    subparsers.add_parser("cleanup", help="cleanup transient charts and logs")

    subparsers.add_parser("data", help="run data pipeline commands")

    subparsers.add_parser("replay", help="replay/debug market data")

    subparsers.add_parser("chart", help="generate debug chart")

    args, remaining = parser.parse_known_args()
    args.args = remaining
    return args


def main() -> None:
    args = parse_args()
    if args.command == "collect":
        market_data_agent.main()
    elif args.command == "run":
        run_pipeline.main()
    elif args.command == "health":
        healthcheck.main()
    elif args.command == "cleanup":
        cleanup.main()
    elif args.command == "data":
        sys.argv = ["data_pipeline.py", *(args.args or ["--help"])]
        data_pipeline.main()
    elif args.command == "replay":
        sys.argv = ["replay.py", *(args.args or ["--help"])]
        replay.main()
    elif args.command == "chart":
        sys.argv = ["debug_chart.py", *(args.args or ["--help"])]
        debug_chart.main()


if __name__ == "__main__":
    main()
