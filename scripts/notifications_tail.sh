#!/usr/bin/env bash
set -euo pipefail

LINES="${1:-50}"
case "$LINES" in
  ''|*[!0-9]*) LINES=50 ;;
esac

if [ "$LINES" -gt 300 ]; then
  LINES=300
fi

ARCHIVE_DIR="${NOTIFICATION_ARCHIVE_DIR:-/opt/api-report-agent/data/notifications}"
TODAY="$(date -u +%F)"
FILE="${ARCHIVE_DIR}/${TODAY}.jsonl"

if [ ! -f "$FILE" ]; then
  exit 0
fi

tail -n "$LINES" "$FILE"
