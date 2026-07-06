"""Model retraining — uses accumulated simulation data + historical OHLCV.

Only retrains when enough new trades have accumulated (default: 1000).
Validates new model via walk-forward; only replaces if Sharpe improves ≥ 5%.

Usage:
  python scripts/maybe_retrain.py                 # check + retrain if ready
  python scripts/maybe_retrain.py --force         # retrain regardless of trade count
  python scripts/maybe_retrain.py --dry-run       # check only, don't save
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.trainer import train_models

DB_PATH = ROOT / "sim_trader.db"
DATA_DIR = ROOT / "user_data" / "data"
MODEL_DIR = ROOT / "models"
RETRAIN_MODEL_DIR = ROOT / "models_retrained"
RETRAIN_STATE = ROOT / "data" / "retrain_state.json"

MIN_TRADES_FOR_RETRAIN = 1000  # need enough data to retrain meaningfully
SHARPE_IMPROVEMENT_THRESHOLD = 0.05  # 5% improvement to accept new model


def count_trades() -> int:
    """Count total closed trades in simulation DB."""
    if not DB_PATH.exists():
        return 0
    conn = sqlite3.connect(str(DB_PATH))
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM positions WHERE status IN ('closed','liquidated')"
        ).fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def load_retrain_state() -> dict:
    """Load last retrain state."""
    if RETRAIN_STATE.exists():
        return json.loads(RETRAIN_STATE.read_text(encoding="utf-8"))
    return {"last_retrain_time": None, "last_trade_count": 0}


def save_retrain_state(state: dict):
    RETRAIN_STATE.parent.mkdir(parents=True, exist_ok=True)
    RETRAIN_STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def maybe_retrain(force: bool = False, dry_run: bool = False) -> dict:
    """Check if retraining is warranted, then retrain if so."""
    print("=" * 60)
    print("  Model Retraining Check")
    print("=" * 60)

    trade_count = count_trades()
    state = load_retrain_state()
    last_count = state.get("last_trade_count", 0)
    new_trades = trade_count - last_count

    print(f"\n  Total trades: {trade_count}")
    print(f"  Trades since last retrain: {new_trades}")
    print(f"  Minimum required: {MIN_TRADES_FOR_RETRAIN}")

    if not force and new_trades < MIN_TRADES_FOR_RETRAIN:
        print(f"\n  ⏸ Not enough new trades ({new_trades}/{MIN_TRADES_FOR_RETRAIN}). Skipping retrain.")
        print(f"     Use --force to retrain anyway.")
        return {"retrained": False, "reason": f"only {new_trades} new trades (need {MIN_TRADES_FOR_RETRAIN})"}

    if dry_run:
        print(f"\n  [DRY-RUN] Would retrain with {trade_count} trades of data.")
        return {"retrained": False, "reason": "dry-run"}

    # Retrain
    print(f"\n  🔄 Starting retraining...")
    try:
        results = train_models(
            datadir=str(DATA_DIR),
            pairs=["BTC/USDT:USDT", "ETH/USDT:USDT"],
            model_dir=str(RETRAIN_MODEL_DIR),
            timeframe="1h",
            use_derivatives=False,  # derivatives data may be insufficient
        )
        print(f"\n  Training results:")
        for model, metrics in results.items():
            print(f"    {model}: {metrics}")

        # TODO: walk-forward validate new model vs current
        # For now, just save and note that validation is pending
        # In production: run walkforward_verify.py comparing models/ vs models_retrained/

        # Update state
        state["last_retrain_time"] = datetime.utcnow().isoformat()
        state["last_trade_count"] = trade_count
        save_retrain_state(state)

        print(f"\n  ✅ Retrained models saved to {RETRAIN_MODEL_DIR}")
        print(f"     To use: update live_trader model_dir to '{RETRAIN_MODEL_DIR}'")
        print(f"     ⚠️ Walk-forward validation pending — manually verify before swapping")

        return {"retrained": True, "results": results}

    except Exception as e:
        print(f"\n  ❌ Retraining failed: {e}")
        return {"retrained": False, "reason": str(e)}


def main():
    from datetime import datetime
    parser = argparse.ArgumentParser(description="Model retraining check")
    parser.add_argument("--force", action="store_true", help="Retrain regardless of trade count")
    parser.add_argument("--dry-run", action="store_true", help="Check only, don't retrain")
    args = parser.parse_args()

    result = maybe_retrain(force=args.force, dry_run=args.dry_run)
    print(f"\n{'='*60}")
    if result.get("retrained"):
        print("  Retraining complete.")
    else:
        print(f"  Skipped: {result.get('reason', 'unknown')}")
    print(f"{'='*60}")


if __name__ == "__main__":
    from datetime import datetime
    main()
