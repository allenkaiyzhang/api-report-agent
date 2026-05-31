#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-api-report-agent}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/health}"

for attempt in $(seq 1 10); do
  echo "Health check attempt $attempt: $HEALTH_URL"
  if response="$(curl -fsS --max-time 5 "$HEALTH_URL")"; then
    echo "$response"
    if [[ "$response" == *'"status":"ok"'* && "$response" == *'"service":"api-report-agent"'* ]]; then
      echo "PASS"
      exit 0
    fi
    if [[ "$response" == *'"status": "ok"'* && "$response" == *'"service": "api-report-agent"'* ]]; then
      echo "PASS"
      exit 0
    fi
    echo "Unexpected health response: $response" >&2
  fi
  sleep 2
done

echo "FAIL: health check failed for $HEALTH_URL" >&2
sudo systemctl --no-pager --full status "$SERVICE_NAME" || true
sudo journalctl -u "$SERVICE_NAME" -n 100 --no-pager || true
exit 1
