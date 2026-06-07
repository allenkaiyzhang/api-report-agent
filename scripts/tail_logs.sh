#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-api-report-agent}"
LINES="${LINES:-120}"

sudo journalctl -u "$SERVICE_NAME" -n "$LINES" --no-pager
