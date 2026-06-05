#!/usr/bin/env bash
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
export PATH="/opt/homebrew/bin:$PATH"

MODE="dev"
if [ "$1" = "--prod" ] || [ "$1" = "-p" ] || [ "$1" = "prod" ]; then
  MODE="prod"
fi

# LAN access (phone/iPad): bind both servers to 0.0.0.0 and point the frontend
# at this Mac's mDNS name so API calls work from other devices. Apple devices
# resolve .local via Bonjour; the name survives DHCP IP changes (unlike a raw IP).
LOCAL_HOST="$(scutil --get LocalHostName 2>/dev/null | tr '[:upper:]' '[:lower:]')"
if [ -n "$LOCAL_HOST" ]; then
  export VITE_API_URL="http://${LOCAL_HOST}.local:8000"
fi

# Kill stale processes on our ports
for port in 8000 5173; do
  lsof -ti:$port 2>/dev/null | xargs kill 2>/dev/null || true
done

echo "Starting backend ($MODE)..."
cd "$ROOT/backend"
if [ "$MODE" = "prod" ]; then
  venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 &
else
  venv/bin/uvicorn main:app --reload --reload-exclude 'data' --host 0.0.0.0 --port 8000 &
fi
BACKEND_PID=$!

cd "$ROOT/frontend"
if [ "$MODE" = "prod" ]; then
  echo "Building frontend..."
  npm run build
  echo "Starting frontend (preview)..."
  npm run preview -- --host --port 5173 &
  FRONTEND_URL="http://localhost:5173"
else
  echo "Starting frontend (dev)..."
  npm run dev -- --host &
  FRONTEND_URL="http://localhost:5173"
fi
FRONTEND_PID=$!

echo ""
echo "  Backend:  http://localhost:8000"
echo "  Frontend: $FRONTEND_URL"
if [ -n "$LOCAL_HOST" ]; then
  echo "  LAN (phone/iPad): http://${LOCAL_HOST}.local:5173"
fi
echo ""
echo "Press Ctrl+C to stop both servers."

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
wait
