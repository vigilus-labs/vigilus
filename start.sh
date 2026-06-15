#!/usr/bin/env bash
# start.sh — Launch Vigilus backend (FastAPI) and frontend (Vite dev server).
#
# Portable: resolves its own location, so it works regardless of where the
# repo is cloned or which OS you're on (macOS / Linux).
#
# Usage:
#   ./start.sh            # dev mode: backend on :8000, frontend dev on :5173
#   ./start.sh --build    # build the frontend and serve everything from :8000
set -euo pipefail

# ── Resolve repo root (the directory this script lives in) ──────────────
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
ENV_FILE="$BACKEND_DIR/.env"

GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[0;33m'; NC='\033[0m'
echo -e "${BLUE}Starting Vigilus from ${ROOT_DIR}${NC}"

# ── Secret key ──────────────────────────────────────────────────────────
# Must stay stable across restarts: stored API keys and SSH credentials are
# encrypted with a key derived from it, so a new secret makes them unreadable.
if [ -z "${VIGILUS_SECRET:-}" ] && [ -f "$ENV_FILE" ]; then
    export VIGILUS_SECRET="$(grep -E '^VIGILUS_SECRET=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
fi
if [ -z "${VIGILUS_SECRET:-}" ]; then
    echo -e "${GREEN}Generating a VIGILUS_SECRET and saving it to backend/.env...${NC}"
    VIGILUS_SECRET="$(head -c 32 /dev/urandom | base64 | tr -d '+/=' | head -c 43)"
    export VIGILUS_SECRET
    echo "VIGILUS_SECRET=$VIGILUS_SECRET" >> "$ENV_FILE"
fi

# ── Backend virtualenv ──────────────────────────────────────────────────
if [ ! -d "$BACKEND_DIR/.venv" ]; then
    echo -e "${YELLOW}No backend virtualenv found — creating one and installing deps...${NC}"
    python3 -m venv "$BACKEND_DIR/.venv"
    "$BACKEND_DIR/.venv/bin/pip" install --quiet -e "$BACKEND_DIR[dev]"
fi

# ── Optional: build frontend and serve everything from the backend ──────
if [ "${1:-}" = "--build" ]; then
    echo -e "${BLUE}Building frontend...${NC}"
    ( cd "$FRONTEND_DIR" && [ -d node_modules ] || npm install )
    ( cd "$FRONTEND_DIR" && npm run build )
    echo -e "${GREEN}Serving Vigilus (UI + API) on http://localhost:8000${NC}"
    cd "$BACKEND_DIR"
    exec "$BACKEND_DIR/.venv/bin/uvicorn" vigilus.main:app --host 0.0.0.0 --port 8000
fi

# ── Dev mode: backend + Vite dev server ─────────────────────────────────
if curl -s -o /dev/null --max-time 2 http://localhost:8000/api/health 2>/dev/null; then
    echo -e "${YELLOW}Something is already listening on port 8000 (an old backend?).${NC}"
    echo -e "${YELLOW}Stop it first: lsof -nP -i :8000${NC}"
    exit 1
fi

echo -e "${BLUE}Starting backend on port 8000...${NC}"
( cd "$BACKEND_DIR" && exec "$BACKEND_DIR/.venv/bin/uvicorn" vigilus.main:app --host 0.0.0.0 --port 8000 ) &
BACKEND_PID=$!

# Wait for the backend to come up before starting the frontend, so the
# browser never sees proxy ECONNREFUSED errors during the boot window.
echo -e "${BLUE}Waiting for backend to be ready...${NC}"
for _ in $(seq 1 30); do
    if curl -s -o /dev/null --max-time 1 http://localhost:8000/api/health 2>/dev/null; then
        break
    fi
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
        echo -e "${YELLOW}Backend exited during startup — check the log output above.${NC}"
        exit 1
    fi
    sleep 1
done

echo -e "${BLUE}Starting frontend dev server on port 5173...${NC}"
( cd "$FRONTEND_DIR" && [ -d node_modules ] || npm install )
( cd "$FRONTEND_DIR" && exec npm run dev ) &
FRONTEND_PID=$!

echo -e "${GREEN}Vigilus is running!${NC}"
echo -e "  Frontend: http://localhost:5173"
echo -e "  Backend:  http://localhost:8000/api/health"
echo -e "Press Ctrl+C to stop both."

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" SIGINT SIGTERM
wait
