#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/api-report-agent}"
cd "$APP_DIR"
git pull --ff-only
exec bash scripts/deploy.sh
