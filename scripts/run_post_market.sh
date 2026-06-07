#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

MARKET="${1:-}"
DATE="${2:-$(date +%F)}"

if [[ "$MARKET" != "HK" && "$MARKET" != "US" ]]; then
  echo "usage: scripts/run_post_market.sh HK|US [YYYY-MM-DD]" >&2
  exit 2
fi

DAILY="data/metrics/${MARKET}/${DATE}/daily.json"
if [[ -f "$DAILY" ]] && grep -q '"finalized": true' "$DAILY"; then
  echo "already finalized: ${MARKET} ${DATE}"
  exit 0
fi

python scripts/post_market_pipeline.py --market "$MARKET" --date "$DATE"
