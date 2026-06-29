#!/bin/bash
# 以太 AI Trader — 一键启动 (bot + AI bridge + web dashboard)
# Usage: bash start.sh [--live]

set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
TRADE_DIR="$DIR/../freqtrade"
CONFIG="$DIR/config.json"
MODEL_DIR="$DIR/models"
VENV_PYTHON="$TRADE_DIR/freqtrade/.venv/Scripts/python.exe"

echo "========================================"
echo "  以太 AI Trader — 启动中..."
echo "========================================"
echo "  Config:    $CONFIG"
echo "  Models:    $MODEL_DIR"
echo ""

# 1. Start AI API Bridge (port 8081)
echo "[1/3] Starting AI API Bridge on :8081..."
"$VENV_PYTHON" -m freqtrade.ai.api_bridge --config "$CONFIG" --host 127.0.0.1 --port 8081 &
API_PID=$!
echo "  API Bridge PID: $API_PID"
sleep 2

# 2. Start Freqtrade bot (port 8080)
echo "[2/3] Starting Freqtrade Bot..."
cd "$TRADE_DIR"
MODE="${1:-}"
if [ "$MODE" = "--live" ]; then
    echo "  WARNING: LIVE TRADING MODE"
    "$VENV_PYTHON" -m freqtrade trade -c "$CONFIG" &
else
    echo "  DRY-RUN mode (use --live for real trading)"
    "$VENV_PYTHON" -m freqtrade trade -c "$CONFIG" --dry-run &
fi
BOT_PID=$!
echo "  Bot PID: $BOT_PID"
sleep 3

# 3. Start Web Dashboard (port 3000)
echo "[3/3] Starting Web Dashboard..."
cd "$TRADE_DIR/web"
if [ ! -d "node_modules" ]; then
    echo "  Installing dependencies..."
    npm install
fi
npm run dev &
WEB_PID=$!
echo "  Web PID: $WEB_PID"

echo ""
echo "========================================"
echo "  全部启动完成!"
echo "========================================"
echo "  Dashboard: http://localhost:3000"
echo "  API:       http://localhost:8080 (bot)"
echo "  AI Bridge: http://localhost:8081 (ai)"
echo ""
echo "  PIDs: Bot=$BOT_PID API=$API_PID Web=$WEB_PID"
echo "  Stop all: kill $BOT_PID $API_PID $WEB_PID"
echo "========================================"

# Wait for any to exit
trap "kill $BOT_PID $API_PID $WEB_PID 2>/dev/null; exit" SIGINT SIGTERM
wait
