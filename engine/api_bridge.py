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

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)

app = FastAPI(title="以太 AI Trader API", version="0.1.0")

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

def run_api_bridge(host: str = "127.0.0.1", port: int = 8081, model_dir: str = "./models"):
    """Start the AI API bridge server."""
    import uvicorn

    # Set model_dir globally for all endpoints
    global _MODEL_DIR
    _MODEL_DIR = model_dir

    logger.info(f"AI API Bridge on {host}:{port} (models: {model_dir})")
    uvicorn.run(app, host=host, port=port, log_level="info")


_MODEL_DIR = "./models"


def _get_model_dir() -> str:
    return _MODEL_DIR


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="AI API Bridge")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8081)
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
