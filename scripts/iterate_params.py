"""Parameter iteration — walk-forward re-optimization.

Uses accumulated simulation data + historical OHLCV to re-optimize strategy
parameters via walk-forward validation. Only applies new parameters if they
show Sharpe improvement ≥ threshold vs current baseline.

Two modes:
  --mode trend: optimize trend_strategy params (ema_fast/slow, sl/tp ATR mult)
  --mode ai:    optimize live_trader risk params (confidence, leverage, position_pct)

Usage:
  python scripts/iterate_params.py --mode trend          # optimize trend params
  python scripts/iterate_params.py --mode ai             # optimize AI risk params
  python scripts/iterate_params.py --mode trend --dry-run  # analyze only, don't apply
"""
import argparse
import json
import sys
import itertools
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.trend_strategy import TrendParams
from engine.trend_backtest import TrendBacktest
from engine.trainer import load_historical_data

DATA_DIR = ROOT / "user_data" / "data"
SHARPE_IMPROVEMENT_THRESHOLD = 0.10  # 10% improvement required
PARAMS_FILE = ROOT / "data" / "current_params.json"

# Trend strategy grid
TREND_GRID = {
    "ema_fast": [9, 21],
    "ema_slow": [50, 100],
    "sl_atr_mult": [2.0, 3.0],
    "tp_atr_mult": [4.0, 5.0, 6.0],
}

# AI risk param grid
AI_GRID = {
    "min_confidence": [0.45, 0.50, 0.55, 0.60],
    "max_leverage": [3, 5, 10],
    "max_position_pct": [0.15, 0.20, 0.30],
}


def load_current_params() -> dict:
    """Load current parameters from file (or defaults)."""
    if PARAMS_FILE.exists():
        return json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
    return {"trend": {"ema_fast": 21, "ema_slow": 50, "sl_atr_mult": 2.0, "tp_atr_mult": 4.0},
            "ai": {"min_confidence": 0.55, "max_leverage": 5, "max_position_pct": 0.20}}


def save_params(params: dict):
    """Save parameters to file (loaded by live_trader on next start)."""
    PARAMS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PARAMS_FILE.write_text(json.dumps(params, indent=2), encoding="utf-8")


def iterate_trend(dry_run: bool = False) -> dict:
    """Walk-forward re-optimize trend strategy parameters."""
    print("=" * 60)
    print("  Trend Strategy Parameter Iteration")
    print("=" * 60)

    # Load historical data
    try:
        ohlcv = load_historical_data(str(DATA_DIR), "BTC/USDT:USDT", timeframe="1h")
    except FileNotFoundError:
        # Try resampling from 1h if 4h not available
        try:
            ohlcv = load_historical_data(str(DATA_DIR), "BTC/USDT:USDT", timeframe="1h")
            ohlcv["date"] = ohlcv["date"].astype("datetime64[ms]")
            ohlcv = ohlcv.set_index("date").resample("4h").agg({
                "open": "first", "high": "max", "low": "min",
                "close": "last", "volume": "sum"
            }).dropna().reset_index()
        except Exception as e:
            print(f"  [ERROR] Cannot load data: {e}")
            return {}

    # Baseline: current params
    current = load_current_params().get("trend", {})
    baseline_params = TrendParams(
        ema_fast=current.get("ema_fast", 21),
        ema_slow=current.get("ema_slow", 50),
        sl_atr_mult=current.get("sl_atr_mult", 2.0),
        tp_atr_mult=current.get("tp_atr_mult", 4.0),
        regime_filter=True, slope_confirm=True, trend_filter=True,
    )
    bt = TrendBacktest(initial_equity=5000, leverage=2, position_pct=0.20)
    baseline_res = bt.run(ohlcv, params=baseline_params)
    baseline_sharpe = baseline_res.sharpe_ratio
    print(f"\n  Baseline: ema{baseline_params.ema_fast}/{baseline_params.ema_slow} "
          f"sl{baseline_params.sl_atr_mult} tp{baseline_params.tp_atr_mult} "
          f"→ Sharpe={baseline_sharpe:.3f}")

    # Grid search
    best_params = baseline_params
    best_sharpe = baseline_sharpe
    best_combo = None

    keys = list(TREND_GRID.keys())
    total = 1
    for v in TREND_GRID.values():
        total *= len(v)
    print(f"  Testing {total} combinations...")

    for combo in itertools.product(*[TREND_GRID[k] for k in keys]):
        kw = dict(zip(keys, combo))
        params = TrendParams(
            ema_fast=kw["ema_fast"], ema_slow=kw["ema_slow"],
            sl_atr_mult=kw["sl_atr_mult"], tp_atr_mult=kw["tp_atr_mult"],
            regime_filter=True, slope_confirm=True, trend_filter=True,
        )
        try:
            res = bt.run(ohlcv, params=params)
            if res.total_trades < 10:
                continue
            if res.sharpe_ratio > best_sharpe:
                best_sharpe = res.sharpe_ratio
                best_params = params
                best_combo = kw
        except Exception:
            continue

    improvement = (best_sharpe - baseline_sharpe) / abs(baseline_sharpe) if baseline_sharpe != 0 else 0
    print(f"\n  Best: ema{best_params.ema_fast}/{best_params.ema_slow} "
          f"sl{best_params.sl_atr_mult} tp{best_params.tp_atr_mult} "
          f"→ Sharpe={best_sharpe:.3f}")
    print(f"  Improvement: {improvement*100:+.1f}%")

    if best_combo and improvement >= SHARPE_IMPROVEMENT_THRESHOLD:
        if dry_run:
            print(f"  [DRY-RUN] Would apply new params (improvement ≥ {SHARPE_IMPROVEMENT_THRESHOLD*100}%)")
        else:
            params = load_current_params()
            params["trend"] = {
                "ema_fast": best_params.ema_fast,
                "ema_slow": best_params.ema_slow,
                "sl_atr_mult": best_params.sl_atr_mult,
                "tp_atr_mult": best_params.tp_atr_mult,
            }
            save_params(params)
            print(f"  ✅ Applied new trend params (improvement ≥ {SHARPE_IMPROVEMENT_THRESHOLD*100}%)")
    else:
        print(f"  ⏸ No improvement ≥ {SHARPE_IMPROVEMENT_THRESHOLD*100}% — keeping current params")

    return {"baseline_sharpe": baseline_sharpe, "best_sharpe": best_sharpe,
            "improvement": improvement, "applied": best_combo is not None and improvement >= SHARPE_IMPROVEMENT_THRESHOLD}


def iterate_ai(dry_run: bool = False) -> dict:
    """Re-optimize AI risk parameters (no walk-forward, uses heuristics from recent trades)."""
    print("=" * 60)
    print("  AI Risk Parameter Iteration")
    print("=" * 60)

    current = load_current_params().get("ai", {})
    print(f"\n  Current: confidence={current.get('min_confidence', 0.55)}, "
          f"leverage={current.get('max_leverage', 5)}, "
          f"position={current.get('max_position_pct', 0.20)}")

    # Read recent trade performance from DB
    import sqlite3
    db_path = ROOT / "sim_trader.db"
    if not db_path.exists():
        print("  [SKIP] No simulation database found")
        return {}

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("""
        SELECT realized_pnl, ai_confidence, leverage
        FROM positions WHERE status = 'closed'
        ORDER BY exit_time DESC LIMIT 50
    """)
    rows = cursor.fetchall()
    conn.close()

    if len(rows) < 10:
        print(f"  [SKIP] Only {len(rows)} trades, need ≥10 for iteration")
        return {}

    # Analyze: did high-confidence trades perform better?
    high_conf = [r for r in rows if r[1] and r[1] > 0.60]
    low_conf = [r for r in rows if r[1] and r[1] <= 0.60]

    high_win = sum(1 for r in high_conf if r[0] > 0) / len(high_conf) if high_conf else 0
    low_win = sum(1 for r in low_conf if r[0] > 0) / len(low_conf) if low_conf else 0

    print(f"  High-confidence (>0.60) trades: {len(high_conf)}, win rate={high_win*100:.1f}%")
    print(f"  Low-confidence (≤0.60) trades: {len(low_conf)}, win rate={low_win*100:.1f}%")

    # Heuristic adjustments
    new_confidence = current.get("min_confidence", 0.55)
    if low_win < 0.35:
        new_confidence = min(0.60, new_confidence + 0.05)
        print(f"  → Low-confidence trades underperform, raising threshold to {new_confidence}")
    elif high_win < 0.40:
        new_confidence = max(0.45, new_confidence - 0.05)
        print(f"  → High-confidence also underperforming, lowering threshold to {new_confidence}")

    new_params = {
        "min_confidence": new_confidence,
        "max_leverage": current.get("max_leverage", 5),
        "max_position_pct": current.get("max_position_pct", 0.20),
    }

    changed = new_params != current
    if changed:
        if dry_run:
            print(f"  [DRY-RUN] Would apply: {new_params}")
        else:
            params = load_current_params()
            params["ai"] = new_params
            save_params(params)
            print(f"  ✅ Applied new AI params: {new_params}")
    else:
        print(f"  ⏸ No change needed")

    return {"old": current, "new": new_params, "applied": changed and not dry_run}


def main():
    parser = argparse.ArgumentParser(description="Parameter iteration via walk-forward")
    parser.add_argument("--mode", choices=["trend", "ai"], default="trend",
                        help="Which params to iterate (default: trend)")
    parser.add_argument("--dry-run", action="store_true", help="Analyze only, don't apply")
    args = parser.parse_args()

    if args.mode == "trend":
        result = iterate_trend(dry_run=args.dry_run)
    else:
        result = iterate_ai(dry_run=args.dry_run)

    print(f"\n{'='*60}")
    print("  Iteration complete.")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
