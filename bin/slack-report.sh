#!/usr/bin/env bash
# Post a formatted message to Slack via incoming webhook.
# Usage: bin/slack-report.sh "message body"
# Reads SLACK_WEBHOOK_URL from backend/.env

set -euo pipefail

ENVFILE="$(dirname "$0")/../backend/.env"
WEBHOOK_URL=""

if [[ -f "$ENVFILE" ]]; then
  WEBHOOK_URL=$(grep -E '^SLACK_WEBHOOK_URL=' "$ENVFILE" | cut -d= -f2- | tr -d '"' | tr -d "'" | xargs)
fi

if [[ -z "$WEBHOOK_URL" ]]; then
  echo "SLACK_WEBHOOK_URL not set in backend/.env — skipping notification"
  exit 0
fi

MSG="${1:-No message provided}"

# Escape JSON special characters
JSON_MSG=$(printf '%s' "$MSG" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))')

curl -s -X POST "$WEBHOOK_URL" \
  -H 'Content-Type: application/json' \
  -d "{\"text\": ${JSON_MSG}}" \
  -o /dev/null -w ""

echo "Slack notification sent"
