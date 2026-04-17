#!/usr/bin/env bash
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
export PATH="/opt/homebrew/bin:$PATH"

MODE="dev"
if [ "$1" = "--prod" ] || [ "$1" = "-p" ] || [ "$1" = "prod" ]; then
  MODE="prod"
fi

echo "Starting backend ($MODE)..."
cd "$ROOT/backend"
if [ "$MODE" = "prod" ]; then
  venv/bin/uvicorn main:app --port 8000 &
else
  venv/bin/uvicorn main:app --reload --reload-exclude 'data' --port 8000 &
fi
BACKEND_PID=$!

cd "$ROOT/frontend"
if [ "$MODE" = "prod" ]; then
  echo "Building frontend..."
  npm run build
  echo "Starting frontend (preview)..."
  npm run preview -- --port 4173 &
  FRONTEND_URL="http://localhost:4173"
else
  echo "Starting frontend (dev)..."
  npm run dev &
  FRONTEND_URL="http://localhost:5173"
fi
FRONTEND_PID=$!

echo ""
echo "  Backend:  http://localhost:8000"
echo "  Frontend: $FRONTEND_URL"
echo ""
echo "Press Ctrl+C to stop both servers."

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
wait
