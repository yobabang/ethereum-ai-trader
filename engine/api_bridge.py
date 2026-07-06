"""AI API Bridge — standalone FastAPI app for AI-specific endpoints.

Extends freqtrade's REST API with AI decision, status, and optimizer
statistics endpoints. Runs as a separate process alongside freqtrade,
sharing the same database and model directory.

Endpoints:
  GET  /api/v1/ai/status    → model versions, last training time
  GET  /api/v1/ai/decision  → latest AI trading decision
  GET  /api/v1/ai/stats     → optimizer trade statistics
  GET  /api/v1/ai/health    → health check
"""

import sys
from pathlib import Path

# Ensure the project root is in sys.path so `from engine.xxx` works
# both when running as `python engine/api_bridge.py` and `python -m engine.api_bridge`
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import json
import logging
from datetime import UTC, datetime
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from engine.sim_broker import SimBroker
from engine.database import Database

logger = logging.getLogger(__name__)

app = FastAPI(title="以太 AI Trader API", version="0.2.0")

# Global broker instance (shared with live_trader)
_broker: Optional[SimBroker] = None
_db: Optional[Database] = None


def initialize_broker(db_path: str = "sim_trader.db", initial_equity: float = 1000.0):
    """Initialize the global broker instance (called once at startup)."""
    global _broker, _db
    _broker = SimBroker(db_path=db_path)
    _db = _broker.db
    logger.info(f"API bridge initialized with DB: {db_path}")


def get_broker() -> SimBroker:
    """Get the global broker instance."""
    if _broker is None:
        # Lazy init for testing
        initialize_broker()
    return _broker


def get_db() -> Database:
    """Get the global database instance."""
    if _db is None:
        initialize_broker()
    return _db

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


# ---------------------------------------------------------------------------
# State reader — reads from the AI module's disk state
# ---------------------------------------------------------------------------

def _read_optimizer_state() -> dict:
    """Read optimizer state from disk."""
    path = Path(_get_model_dir()) / "optimizer_state.json"
    if not path.exists():
        return {"status": "no_data", "message": "No optimizer state yet"}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _read_latest_decision() -> dict | None:
    """Read the most recent AI decision from disk."""
    path = Path(_get_model_dir()) / "last_decision.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------

@app.get("/api/v1/ai/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(UTC).isoformat()}


@app.get("/api/v1/ai/status")
async def ai_status():
    """AI system status: model versions, last training time."""
    state = _read_optimizer_state()

    return {
        "current_version": state.get("current_version", "none"),
        "last_train_time": state.get("last_train_time", 0),
        "version_count": len(state.get("version_history", [])),
        "trade_count": state.get("trade_count", 0),
        "consecutive_losses": state.get("consecutive_losses", 0),
        "consecutive_wins": state.get("consecutive_wins", 0),
        "adaptive_confidence_threshold": state.get("adaptive_confidence_threshold", 0.55),
        "adaptive_position_scalar": state.get("adaptive_position_scalar", 1.0),
    }


@app.get("/api/v1/ai/decision")
async def ai_decision():
    """Latest AI trading decision."""
    decision = _read_latest_decision()

    if decision is None:
        return {
            "action": "HOLD",
            "reason": "No decision yet — waiting for next cycle",
            "confidence": 0.0,
            "expected_return": 0.0,
            "position_size_pct": 0.0,
            "stop_loss_pct": 0.0,
            "take_profit_pct": 0.0,
            "leverage": 1,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    return decision


@app.get("/api/v1/ai/stats")
async def ai_stats():
    """Optimizer trade statistics for the dashboard."""
    state = _read_optimizer_state()

    if state.get("status") == "no_data":
        return {
            "total_trades": 0,
            "win_rate": 0,
            "sharpe": 0,
            "total_pnl": 0,
            "avg_win": 0,
            "avg_loss": 0,
            "consecutive_losses": 0,
            "consecutive_wins": 0,
            "current_confidence_threshold": 0.55,
            "current_position_scalar": 1.0,
        }

    # Compute stats from trade history (stored in state)
    trade_count = state.get("trade_count", 0)
    version_history = state.get("version_history", [])

    # Best model stats
    best_sharpe = 0.0
    best_win_rate = 0.0
    if version_history:
        best = max(version_history, key=lambda v: v.get("sharpe", 0))
        best_sharpe = best.get("sharpe", 0)
        best_win_rate = best.get("win_rate", 0)

    return {
        "total_trades": trade_count,
        "best_sharpe": round(best_sharpe, 3),
        "best_win_rate": round(best_win_rate, 3),
        "version_count": len(version_history),
        "current_version": state.get("current_version", "none"),
        "consecutive_losses": state.get("consecutive_losses", 0),
        "consecutive_wins": state.get("consecutive_wins", 0),
        "current_confidence_threshold": state.get("adaptive_confidence_threshold", 0.55),
        "current_position_scalar": state.get("adaptive_position_scalar", 1.0),
    }


@app.get("/api/v1/ai/training")
async def ai_training():
    """Auto-training scheduler status."""
    state = _read_optimizer_state()

    # Also try to read scheduler state
    scheduler_path = Path(state.get("model_dir", "./models")) / "scheduler_state.json"
    scheduler = {}
    if scheduler_path.exists():
        try:
            with open(scheduler_path) as f:
                scheduler = json.load(f)
        except Exception:
            pass

    return {
        "training_in_progress": scheduler.get("training_in_progress", False),
        "training_count": scheduler.get("training_count", 0),
        "last_train_time": scheduler.get("last_train_time", "never"),
        "hours_until_next": scheduler.get("hours_until_next", 0),
        "last_metrics": scheduler.get("last_metrics", {}),
        "last_error": scheduler.get("last_error", ""),
        "model_versions": len(state.get("version_history", [])),
        "current_version": state.get("current_version", "none"),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _check_port_available(host: str, port: int) -> bool:
    """Check if a port is available. Returns True if free."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            s.bind((host, port))
            return True
    except (socket.error, OSError):
        return False


def _find_who_occupies(port: int) -> str:
    """Best-effort: identify what's holding the port (Windows netstat)."""
    import subprocess
    try:
        r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=5)
        for line in r.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                # e.g. "  TCP    0.0.0.0:8081    0.0.0.0:0    LISTENING    1234"
                parts = line.split()
                pid = parts[-1] if parts else "?"
                return f"PID {pid} (try `tasklist /FI \"PID eq {pid}\"` to see process)"
        return "unknown process"
    except Exception:
        return "unknown (netstat failed)"


def run_api_bridge(host: str = "127.0.0.1", port: int = 8090, model_dir: str = "./models",
                   db_path: str = "sim_trader.db", initial_equity: float = 1000.0):
    """Start the AI API bridge server.

    Default port is 8090 (changed from 8081 — 8081 is commonly used by McAfee
    macmnsvc on enterprise machines). If the port is occupied, prints a clear
    error with the offending PID and suggests --port override.
    """
    import uvicorn

    # Set model_dir globally for all endpoints
    global _MODEL_DIR
    _MODEL_DIR = model_dir

    # Check port availability before starting
    if not _check_port_available(host, port):
        occupier = _find_who_occupies(port)
        print("=" * 60)
        print(f"  ERROR: port {port} is already in use by {occupier}")
        print(f"  Options:")
        print(f"    1. Use a different port:  python -m engine.api_bridge --port 8091")
        print(f"    2. Free the port (stop the occupying process)")
        print("=" * 60)
        raise SystemExit(1)

    # Initialize broker
    initialize_broker(db_path=db_path, initial_equity=initial_equity)

    logger.info(f"AI API Bridge on {host}:{port} (models: {model_dir}, db: {db_path})")
    uvicorn.run(app, host=host, port=port, log_level="info")


_MODEL_DIR = "./models"


def _get_model_dir() -> str:
    return _MODEL_DIR


# ===========================================================================
# Trade endpoints (SPEC_SUPPLEMENT.md §6.2)
# ===========================================================================


def _pair_to_inst(pair: str) -> str:
    return "BTC-USDT-SWAP" if "BTC" in pair else "ETH-USDT-SWAP"


def _get_price(pair: str, fallback: float) -> float:
    try:
        import requests
        r = requests.get("https://www.okx.com/api/v5/market/ticker",
                         params={"instId": _pair_to_inst(pair)}, timeout=3)
        if r.ok and r.json().get("data"):
            return float(r.json()["data"][0]["last"])
    except Exception:
        pass
    return fallback


@app.get("/api/v1/trade/account")
async def trade_account():
    """Account summary — reads directly from database."""
    import sqlite3
    conn = sqlite3.connect("sim_trader.db")
    initial = 1000.0

    open_rows = conn.execute(
        "SELECT pair, side, entry_price, contracts, margin FROM positions WHERE status='open'"
    ).fetchall()

    used_margin = sum(r[4] for r in open_rows)
    closed_pnl = conn.execute(
        "SELECT COALESCE(SUM(realized_pnl),0) FROM positions WHERE status!='open'"
    ).fetchone()[0]
    balance = initial - used_margin + closed_pnl

    unrealized = 0.0
    for r in open_rows:
        pair, side, entry, contracts, margin = r
        price = _get_price(pair, entry)
        unrealized += contracts * (price - entry) if side == "long" else contracts * (entry - price)

    equity = balance + unrealized
    total_closed = conn.execute("SELECT COUNT(*) FROM positions WHERE status!='open'").fetchone()[0]
    wins = conn.execute("SELECT COUNT(*) FROM positions WHERE status!='open' AND realized_pnl>0").fetchone()[0]
    conn.close()

    return {
        "initial_equity": initial, "equity": round(equity, 2), "balance": round(balance, 2),
        "unrealized_pnl": round(unrealized, 2),
        "today_pnl": round(equity - initial, 2),
        "today_pnl_pct": round((equity - initial) / initial * 100, 2),
        "open_positions": len(open_rows), "total_trades": total_closed,
        "win_rate": round(wins / total_closed, 4) if total_closed else 0,
        "max_drawdown": 0.0,
    }


@app.get("/api/v1/trade/positions")
async def trade_positions():
    """Current open positions — reads directly from database."""
    import sqlite3
    conn = sqlite3.connect("sim_trader.db")
    rows = conn.execute(
        "SELECT id, pair, side, entry_price, contracts, margin, leverage, sl_price, tp_price, "
        "funding_paid, entry_time, ai_confidence, ai_reason, mode FROM positions WHERE status='open'"
    ).fetchall()
    positions = []
    for r in rows:
        pid, pair, side, entry, contracts, margin, lev, sl, tp, funding, etime, conf, reason, mode = r
        price = _get_price(pair, entry)
        unrealized = contracts * (price - entry) if side == "long" else contracts * (entry - price)
        positions.append({
            "id": pid, "pair": pair, "side": side,
            "entry_price": round(entry, 2), "current_price": round(price, 2),
            "contracts": round(contracts, 6), "margin": round(margin, 2),
            "leverage": lev, "sl_price": round(sl, 2), "tp_price": round(tp, 2),
            "unrealized_pnl": round(unrealized, 2),
            "roe_pct": round(unrealized / margin * 100, 2) if margin else 0,
            "funding_paid": round(funding, 4), "entry_time": etime,
            "ai_confidence": conf, "ai_reason": reason, "mode": mode,
        })
    conn.close()
    return {"positions": positions, "count": len(positions)}


@app.get("/api/v1/trade/positions/{pos_id}")
async def trade_position_detail(pos_id: int):
    """Single position detail."""
    db = get_db()
    pos = db.get_position(pos_id)
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")
    return pos


@app.get("/api/v1/trade/orders")
async def trade_orders(limit: int = 50):
    """Order history (closed + open)."""
    db = get_db()
    positions = db.get_recent_positions(limit)
    return {"orders": positions, "count": len(positions)}


@app.get("/api/v1/trade/equity")
async def trade_equity(days: int = 7):
    """Equity curve data."""
    db = get_db()
    history = db.get_equity_history(days)
    return {
        "snapshots": [
            {"timestamp": h["timestamp"], "equity": h["equity"],
             "balance": h["balance"], "unrealized_pnl": h["unrealized_pnl"],
             "open_positions": h["open_positions"]}
            for h in history
        ],
        "count": len(history),
    }


@app.get("/api/v1/trade/decisions")
async def trade_decisions(limit: int = 50):
    """Recent AI decisions."""
    db = get_db()
    decisions = db.get_recent_decisions(limit)
    return {"decisions": decisions, "count": len(decisions)}


# ---------------------------------------------------------------------------
# Market data REST endpoints (OKX public, no auth)
# ---------------------------------------------------------------------------

@app.get("/api/v1/market/klines")
async def market_klines(symbol: str = "BTC/USDT:USDT", timeframe: str = "1h", limit: int = 200):
    import requests
    inst_map = {"BTC/USDT:USDT": "BTC-USDT-SWAP", "ETH/USDT:USDT": "ETH-USDT-SWAP"}
    inst = inst_map.get(symbol, symbol.replace("/", "-").replace(":USDT", "-SWAP"))
    tf_okx = timeframe.upper() if timeframe[-1].isdigit() else timeframe[:-1] + timeframe[-1].upper()
    try:
        r = requests.get("https://www.okx.com/api/v5/market/candles",
                         params={"instId": inst, "bar": tf_okx, "limit": str(min(limit, 300))}, timeout=10)
        r.raise_for_status()
        rows = list(reversed(r.json().get("data", [])))
        candles = [{"time": int(row[0]), "open": float(row[1]), "high": float(row[2]),
                     "low": float(row[3]), "close": float(row[4]), "volume": float(row[5])}
                   for row in rows]
        return {"symbol": symbol, "timeframe": timeframe, "count": len(candles), "data": candles}
    except Exception as e:
        return {"error": str(e), "data": []}, 500

@app.get("/api/v1/market/ticker")
async def market_ticker(symbol: str = "BTC/USDT:USDT"):
    import requests
    inst_map = {"BTC/USDT:USDT": "BTC-USDT-SWAP", "ETH/USDT:USDT": "ETH-USDT-SWAP"}
    inst = inst_map.get(symbol, symbol.replace("/", "-").replace(":USDT", "-SWAP"))
    try:
        r = requests.get("https://www.okx.com/api/v5/market/ticker", params={"instId": inst}, timeout=10)
        r.raise_for_status()
        t = r.json().get("data", [{}])[0]
        return {"symbol": symbol, "last": float(t.get("last", 0)),
                "bid": float(t.get("bidPx", 0)), "ask": float(t.get("askPx", 0)),
                "high_24h": float(t.get("high24h", 0)), "low_24h": float(t.get("low24h", 0)),
                "volume_24h": float(t.get("vol24h", 0)),
                "change_pct_24h": float(t.get("sodUtc0", 0) or t.get("open24h", 0)),
                "timestamp": int(t.get("ts", 0))}
    except Exception as e:
        return {"error": str(e)}, 500

# ---------------------------------------------------------------------------
# WebSocket 实时行情推送
# ---------------------------------------------------------------------------

import asyncio


def _fetch_okx_ws_data(pair: str) -> dict:
    """Fetch OKX ticker + candles synchronously (works with proxy)."""
    import requests
    inst_id = pair.replace("/", "-").replace(":USDT", "-SWAP")
    ticker_data, candles_data = None, []
    try:
        r = requests.get("https://www.okx.com/api/v5/market/ticker",
                         params={"instId": inst_id}, timeout=5)
        if r.ok and r.json().get("data"):
            ticker_data = r.json()["data"][0]
    except Exception as e:
        logger.warning(f"WS ticker: {e}")
    try:
        r = requests.get("https://www.okx.com/api/v5/market/candles",
                         params={"instId": inst_id, "bar": "1H", "limit": 5}, timeout=5)
        if r.ok and r.json().get("data"):
            candles_data = r.json()["data"][:5]
    except Exception as e:
        logger.warning(f"WS candles: {e}")
    return {"pair": pair, "timestamp": datetime.utcnow().isoformat(),
            "status": "ok" if (ticker_data or candles_data) else "degraded",
            "source": "okx" if (ticker_data or candles_data) else None,
            "ticker": ticker_data, "candles": candles_data}


@app.websocket("/ws/klines")
async def ws_klines(websocket: WebSocket, pair: str = "BTC/USDT:USDT"):
    await websocket.accept()
    logger.info(f"WebSocket: {pair}")
    loop = asyncio.get_event_loop()
    try:
        while True:
            data = await loop.run_in_executor(None, _fetch_okx_ws_data, pair)
            await websocket.send_json(data)
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        logger.info(f"WebSocket closed: {pair}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


# POST /trade/manual and DELETE /trade/positions/{id} — manual trading endpoints
from pydantic import BaseModel


class ManualOrderRequest(BaseModel):
    """Request body for manual order placement."""
    pair: str                                    # e.g. "BTC/USDT:USDT"
    side: str                                    # "long" or "short"
    position_size_pct: float = 0.10              # fraction of equity
    leverage: int = 3
    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.04
    confidence: float = 1.0                      # manual = max confidence
    reason: str = "manual"


@app.post("/api/v1/trade/manual")
async def trade_manual(req: ManualOrderRequest):
    """Place a manual order through the sim_broker.

    Allows the operator to manually open a position (bypassing AI decision).
    The order goes through the same sim_broker risk checks (margin, leverage cap,
    duplicate pair rejection) — it's not a free pass to break risk rules.
    """
    broker = get_broker()
    decision = {
        "pair": req.pair,
        "side": req.side,
        "position_size_pct": req.position_size_pct,
        "leverage": req.leverage,
        "stop_loss_pct": req.stop_loss_pct,
        "take_profit_pct": req.take_profit_pct,
        "confidence": req.confidence,
        "reason": req.reason,
        "mode": "manual",
        "aggressive": False,
    }
    pos_id = broker.open_order(decision)
    if pos_id is None:
        return {"success": False, "reason": "Broker rejected order (check risk limits/logs)"}
    return {"success": True, "position_id": pos_id, "pair": req.pair, "side": req.side}


@app.delete("/api/v1/trade/positions/{pos_id}")
async def trade_close_position(pos_id: int):
    """Manually close a position at current market price.

    The position is closed through sim_broker (realizes PnL, deducts fees,
    updates equity). This is the manual exit path — AI-managed positions
    are normally closed by SL/TP/liquidation automatically.
    """
    broker = get_broker()
    if pos_id not in broker.open_positions:
        raise HTTPException(status_code=404, detail=f"Position {pos_id} not found or already closed")

    pos = broker.open_positions[pos_id]
    try:
        price = broker.get_ticker(pos.pair)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Cannot fetch price: {e}")

    broker._close(pos, price, "manual")
    return {"success": True, "position_id": pos_id, "exit_price": price, "reason": "manual"}


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="AI API Bridge")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090,
                        help="Port (default 8090; 8081 conflicts with McAfee)")
    parser.add_argument("--config", help="Config file path (for model_dir)")
    parser.add_argument("--model-dir", default="./models")
    args = parser.parse_args()

    model_dir = args.model_dir
    if args.config:
        try:
            with open(args.config) as f:
                config = json.load(f)
            model_dir = config.get("ai", {}).get("model_dir", model_dir)
        except FileNotFoundError:
            pass

    run_api_bridge(args.host, args.port, model_dir)
