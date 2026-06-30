"""Walk-forward validation for the rule-based trend strategy (Plan D).

For each of 5 rolling windows:
  1. Grid-search TrendParams on the TRAIN segment (pick best Sharpe)
  2. Apply those params to the OOS TEST segment
  3. Record test metrics
Train and test NEVER overlap in time. Reports per-window metrics, pass-rate
against SPEC, and parameter stability across windows.

Usage:
    python scripts/trend_walkforward.py
    python scripts/trend_walkforward.py --windows 5 --pair BTC/USDT:USDT
"""
from __future__ import annotations

import argparse
import itertools
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.trainer import load_historical_data
from engine.trend_strategy import TrendParams
from engine.trend_backtest import TrendBacktest

DATA_DIR = ROOT / "user_data" / "data"
PAIRS = ["BTC/USDT:USDT", "ETH/USDT:USDT"]

SPEC = {"sharpe": 0.5, "max_dd": 0.15, "win_rate": 0.40, "profit_factor": 1.5}


def resample_ohlcv(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Resample 1h OHLCV to a higher timeframe (e.g. '4h').

    Keeps the same column layout. '1h' returns df unchanged.
    """
    if timeframe == "1h":
        return df
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    rule = timeframe  # e.g. "4h"
    out = df.resample(rule).agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna().reset_index()
    return out

# Parameter grid for search — expanded with larger TP (for higher PF) and
# slope confirmation (to filter false breakouts, raise win rate).
GRID = {
    "ema_fast": [9, 21],
    "ema_slow": [50, 100],
    "sl_atr_mult": [2.0, 3.0],
    "tp_atr_mult": [4.0, 5.0, 6.0],
    "regime_filter": [True],
    "slope_confirm": [True],
    # trend_filter is a HARD constraint (always on) — it blocks counter-trend
    # entries which are the dominant loss source. Not a grid option because
    # letting the optimizer choose it lets it overfit to "off" on train segments.
    "trend_filter": [True],
}


def grid_params():
    """Yield TrendParams for every grid combination."""
    keys = list(GRID.keys())
    for combo in itertools.product(*[GRID[k] for k in keys]):
        kw = dict(zip(keys, combo))
        yield TrendParams(
            ema_fast=kw["ema_fast"], ema_slow=kw["ema_slow"],
            sl_atr_mult=kw["sl_atr_mult"], tp_atr_mult=kw["tp_atr_mult"],
            regime_filter=kw["regime_filter"], slope_confirm=kw["slope_confirm"],
            trend_filter=kw["trend_filter"],
        )


def make_windows(n_rows: int, n_windows: int, train_frac: float = 0.55, test_frac: float = 0.10):
    """Rolling windows: each has train_size then test_size, rolling by test_size."""
    test_size = int(n_rows * test_frac)
    train_size = int(n_rows * train_frac)
    windows = []
    start = 0
    for _ in range(n_windows):
        t_end = start + train_size
        v_start = t_end
        v_end = v_start + test_size
        if v_end > n_rows:
            break
        windows.append((start, t_end, v_start, v_end))
        start += test_size
    return windows


def search_best_params(ohlcv_train: pd.DataFrame, leverage: int = 3,
                       position_pct: float = 0.30) -> tuple[TrendParams, dict]:
    """Grid-search on train segment, return best params by Sharpe (with floor on trades)."""
    bt = TrendBacktest(initial_equity=5000, leverage=leverage, position_pct=position_pct)
    best = None
    best_score = -1e9
    best_metrics = None
    for params in grid_params():
        try:
            res = bt.run(ohlcv_train, params=params)
        except Exception:
            continue
        # Require at least 20 trades (else Sharpe is meaningless)
        if res.total_trades < 20:
            continue
        # Score: Sharpe, but penalize catastrophic drawdown
        score = res.sharpe_ratio - max(0, res.max_drawdown - 0.3) * 2
        if score > best_score:
            best_score = score
            best = params
            best_metrics = res.to_dict()
            best_metrics["trades"] = res.total_trades
    return best, best_metrics or {}


def evaluate(ohlcv_test: pd.DataFrame, params: TrendParams, leverage: int = 3,
             position_pct: float = 0.30) -> dict:
    """Run backtest on test segment with given params."""
    bt = TrendBacktest(initial_equity=5000, leverage=leverage, position_pct=position_pct)
    res = bt.run(ohlcv_test, params=params)
    d = res.to_dict()
    d["trades"] = res.total_trades
    return d


def passes_spec(m: dict) -> bool:
    return (m["sharpe_ratio"] > SPEC["sharpe"]
            and m["max_drawdown_pct"] < SPEC["max_dd"] * 100
            and m["win_rate_pct"] > SPEC["win_rate"] * 100
            and m["profit_factor"] > SPEC["profit_factor"])


def run_pair(pair: str, n_windows: int, timeframe: str = "1h",
             leverage: int = 3, position_pct: float = 0.30):
    print(f"\n{'='*70}\n{pair}  ({timeframe}, lev={leverage}x, pos={position_pct*100:.0f}%)\n{'='*70}")
    ohlcv = load_historical_data(str(DATA_DIR), pair, timeframe="1h")
    ohlcv["date"] = pd.to_datetime(ohlcv["date"])
    ohlcv = resample_ohlcv(ohlcv, timeframe)
    print(f"  rows={len(ohlcv)}  {ohlcv['date'].iloc[0]} .. {ohlcv['date'].iloc[-1]}")

    windows = make_windows(len(ohlcv), n_windows)
    print(f"  {len(windows)} windows")
    if not windows:
        return []

    results = []
    for wi, (s, te, vs, ve) in enumerate(windows):
        train = ohlcv.iloc[s:te].reset_index(drop=True)
        test = ohlcv.iloc[vs:ve].reset_index(drop=True)
        print(f"\n  W{wi}: train {train['date'].iloc[0].date()}..{train['date'].iloc[-1].date()} "
              f"({len(train)}) → test {test['date'].iloc[0].date()}..{test['date'].iloc[-1].date()} ({len(test)})")

        best, train_m = search_best_params(train, leverage=leverage, position_pct=position_pct)
        if best is None:
            print("    [SKIP] no param produced ≥20 trades on train")
            continue
        print(f"    best train: sharpe={train_m.get('sharpe_ratio','?')} trades={train_m.get('trades','?')} "
              f"params=ema{best.ema_fast}/{best.ema_slow} sl{best.sl_atr_mult} tp{best.tp_atr_mult} "
              f"reg={best.regime_filter} slope={best.slope_confirm} trend={best.trend_filter}")

        test_m = evaluate(test, best, leverage=leverage, position_pct=position_pct)
        passed = passes_spec(test_m)
        results.append({
            "window": wi,
            "params": f"ema{best.ema_fast}/{best.ema_slow} sl{best.sl_atr_mult} tp{best.tp_atr_mult} reg={best.regime_filter} slope={best.slope_confirm} trend={best.trend_filter}",
            "test": f"{test['date'].iloc[0].date()}..{test['date'].iloc[-1].date()}",
            "sharpe": test_m["sharpe_ratio"], "max_dd": test_m["max_drawdown_pct"],
            "win_rate": test_m["win_rate_pct"], "pf": test_m["profit_factor"],
            "return": test_m["total_return_pct"], "trades": test_m["trades"],
            "passed": passed,
        })
        print(f"    TEST: sharpe={test_m['sharpe_ratio']:.2f} dd={test_m['max_drawdown_pct']:.1f}% "
              f"win={test_m['win_rate_pct']:.0f}% pf={test_m['profit_factor']:.2f} "
              f"ret={test_m['total_return_pct']:+.1f}% trades={test_m['trades']} "
              f"→ {'PASS' if passed else 'FAIL'}")
    return results


def summarize(pair: str, results: list):
    if not results:
        print(f"\n[{pair}] no results")
        return
    passed = sum(1 for r in results if r["passed"])
    print(f"\n--- {pair} SUMMARY ---")
    print(f"  windows={len(results)}  pass_all_spec={passed}/{len(results)} ({passed/len(results)*100:.0f}%)")
    print(f"  sharpe : mean={np.mean([r['sharpe'] for r in results]):.3f}  "
          f"min={np.min([r['sharpe'] for r in results]):.3f}  max={np.max([r['sharpe'] for r in results]):.3f}")
    print(f"  return : mean={np.mean([r['return'] for r in results]):+.1f}%")
    print(f"  pf     : mean={np.mean([r['pf'] for r in results]):.2f}")
    print(f"  最优参数稳定性:")
    from collections import Counter
    pc = Counter(r["params"] for r in results)
    for p, c in pc.most_common():
        print(f"    {p}: {c}/{len(results)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", type=int, default=5)
    ap.add_argument("--pair", default=None, help="Single pair (default: both)")
    ap.add_argument("--timeframe", default="1h", help="1h or 4h (4h resampled from 1h)")
    ap.add_argument("--leverage", type=int, default=3, help="Leverage (try 2 for 4h)")
    ap.add_argument("--position-pct", type=float, default=0.30, help="Position size fraction (try 0.20)")
    args = ap.parse_args()

    print(f"Trend strategy walk-forward — {args.timeframe}, {args.windows} windows, "
          f"lev={args.leverage}x pos={args.position_pct*100:.0f}%")
    pairs = [args.pair] if args.pair else PAIRS

    all_results = []
    for pair in pairs:
        r = run_pair(pair, args.windows, timeframe=args.timeframe,
                     leverage=args.leverage, position_pct=args.position_pct)
        summarize(pair, r)
        all_results.extend([(pair, x) for x in r])

    # Overall verdict
    if all_results:
        total = len(all_results)
        passed = sum(1 for _, r in all_results if r["passed"])
        print(f"\n{'='*70}\nOVERALL: {passed}/{total} windows pass all SPEC ({passed/total*100:.0f}%)")
        if passed / total >= 0.6:
            print("  → 策略在 1h 上有边际，值得进一步优化/4h 验证")
        else:
            print("  → 策略在 1h 上不可靠，建议转 4h 或调整策略")


if __name__ == "__main__":
    main()
