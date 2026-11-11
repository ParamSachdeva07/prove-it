#!/usr/bin/env bash
# Starts the API (port 8000) and the web page (port 5180). Ctrl+C stops both.
set -euo pipefail
cd "$(dirname "$0")"

[[ -f .env ]] || { cp .env.example .env; echo "Created .env. Add your API keys to make calls."; }
uv sync --quiet
[[ -d web/node_modules ]] || npm --prefix web install --no-audit --no-fund

uv run uvicorn app.server:app --host 127.0.0.1 --port 8000 &
API=$!
trap 'kill $API 2>/dev/null || true' EXIT INT TERM

echo "Open http://127.0.0.1:5180 (the voice models take about 15 seconds to load)"
npm --prefix web run dev
