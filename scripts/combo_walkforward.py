"""Combo walk-forward: BTC breakout + ETH trend, 50/50 capital split.

For each window:
  1. Grid-search BTC breakout params on BTC train → test on BTC test (50% capital)
  2. Grid-search ETH trend params on ETH train → test on ETH test (50% capital)
  3. Merge the two equity curves into a combined curve
  4. Report combo metrics vs each single leg

Tests whether diversifying across strategy+asset beats either alone.

Usage:
    python scripts/combo_walkforward.py
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
from engine.breakout_strategy import BreakoutStrategy, BreakoutParams
from engine.trend_backtest import TrendBacktest
from scripts.trend_walkforward import resample_ohlcv, make_windows, SPEC

DATA_DIR = ROOT / "user_data" / "data"
TIMEFRAME = "4h"

# ETH trend grid (trend_filter forced ON — proven in prior step)
TREND_GRID = {
    "ema_fast": [9, 21], "ema_slow": [50, 100],
    "sl_atr_mult": [2.0, 3.0], "tp_atr_mult": [4.0, 5.0, 6.0],
}
# BTC breakout grid
BREAKOUT_GRID = {
    "donchian_period": [20, 55],
    "sl_atr_mult": [2.0, 3.0],
    "tp_atr_mult": [4.0, 6.0],
}


def trend_grid():
    keys = list(TREND_GRID.keys())
    for combo in itertools.product(*[TREND_GRID[k] for k in keys]):
        kw = dict(zip(keys, combo))
        yield TrendParams(ema_fast=kw["ema_fast"], ema_slow=kw["ema_slow"],
                          sl_atr_mult=kw["sl_atr_mult"], tp_atr_mult=kw["tp_atr_mult"],
                          regime_filter=True, slope_confirm=True, trend_filter=True)


def breakout_grid():
    keys = list(BREAKOUT_GRID.keys())
    for combo in itertools.product(*[BREAKOUT_GRID[k] for k in keys]):
        kw = dict(zip(keys, combo))
        yield BreakoutParams(donchian_period=kw["donchian_period"],
                             sl_atr_mult=kw["sl_atr_mult"], tp_atr_mult=kw["tp_atr_mult"],
                             regime_filter=True, trend_filter=True)


def _score(res):
    """Sharpe with drawdown penalty, floor on trades."""
    if res.total_trades < 15:
        return -1e9
    return res.sharpe_ratio - max(0, res.max_drawdown - 0.3) * 2


def search_trend(train):
    bt = TrendBacktest(initial_equity=5000, leverage=2, position_pct=0.20)
    best, best_s = None, -1e9
    for p in trend_grid():
        try:
            r = bt.run(train, params=p)
        except Exception:
            continue
        s = _score(r)
        if s > best_s:
            best_s, best = s, p
    return best


def search_breakout(train):
    bt = TrendBacktest(initial_equity=5000, leverage=2, position_pct=0.20)
    best, best_s = None, -1e9
    for p in breakout_grid():
        try:
            r = bt.run(train, strategy=BreakoutStrategy(p))
        except Exception:
            continue
        s = _score(r)
        if s > best_s:
            best_s, best = s, p
    return best


def run_leg(ohlcv_test, params, is_breakout):
    """Backtest one leg with half capital. Returns (result, equity_curve_scaled)."""
    bt = TrendBacktest(initial_equity=2500, leverage=2, position_pct=0.20)
    if is_breakout:
        res = bt.run(ohlcv_test, strategy=BreakoutStrategy(params))
    else:
        res = bt.run(ohlcv_test, params=params)
    return res


def merge_metrics(btc_res, eth_res):
    """Combine two equity curves (each starting at 2500) into combo metrics."""
    # Align equity curves by length
    ec_b = np.array(btc_res.equity_curve)
    ec_e = np.array(eth_res.equity_curve)
    n = min(len(ec_b), len(ec_e))
    combo = ec_b[:n] + ec_e[:n]  # combined equity, starts at 5000
    initial = 5000.0
    total_return = combo[-1] / initial - 1

    # Daily-ish returns from combo curve (bar returns)
    rets = np.diff(combo) / combo[:-1]
    rets = rets[np.isfinite(rets)]
    sharpe = (rets.mean() / rets.std() * np.sqrt(365 * 6)) if rets.std() > 0 else 0  # 4h ~6 bars/day

    peak = np.maximum.accumulate(combo)
    dd = (peak - combo) / peak
    max_dd = dd.max()

    # Trade stats: sum trades, approximate win rate / PF from both legs
    all_trades = list(btc_res.trades) + list(eth_res.trades)
    wins = [t for t in all_trades if t["pnl"] > 0]
    losses = [t for t in all_trades if t["pnl"] <= 0]
    win_rate = len(wins) / len(all_trades) if all_trades else 0
    gp = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    pf = gp / gl if gl > 0 else float("inf")
    return {
        "total_return_pct": round(total_return * 100, 2),
        "sharpe_ratio": round(float(sharpe), 3),
        "max_drawdown_pct": round(float(max_dd) * 100, 2),
        "win_rate_pct": round(win_rate * 100, 1),
        "profit_factor": round(pf, 2),
        "total_trades": len(all_trades),
    }


def passes(m):
    return (m["sharpe_ratio"] > SPEC["sharpe"]
            and m["max_drawdown_pct"] < SPEC["max_dd"] * 100
            and m["win_rate_pct"] > SPEC["win_rate"] * 100
            and m["profit_factor"] > SPEC["profit_factor"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", type=int, default=5)
    args = ap.parse_args()

    btc = load_historical_data(str(DATA_DIR), "BTC/USDT:USDT", timeframe="1h")
    btc["date"] = pd.to_datetime(btc["date"]); btc = resample_ohlcv(btc, TIMEFRAME)
    eth = load_historical_data(str(DATA_DIR), "ETH/USDT:USDT", timeframe="1h")
    eth["date"] = pd.to_datetime(eth["date"]); eth = resample_ohlcv(eth, TIMEFRAME)
    print(f"BTC {len(btc)} rows, ETH {len(eth)} rows ({TIMEFRAME})")

    windows = make_windows(len(btc), args.windows)
    print(f"{len(windows)} windows\n")

    rows = []
    for wi, (s, te, vs, ve) in enumerate(windows):
        btc_tr, btc_va = btc.iloc[s:te].reset_index(drop=True), btc.iloc[vs:ve].reset_index(drop=True)
        eth_tr, eth_va = eth.iloc[s:te].reset_index(drop=True), eth.iloc[vs:ve].reset_index(drop=True)
        print(f"W{wi}: train {btc_tr['date'].iloc[0].date()}..{btc_tr['date'].iloc[-1].date()} "
              f"→ test {btc_va['date'].iloc[0].date()}..{btc_va['date'].iloc[-1].date()}")

        bp = search_breakout(btc_tr)
        tp = search_trend(eth_tr)
        if bp is None or tp is None:
            print("  [SKIP] no valid params"); continue

        btc_res = run_leg(btc_va, bp, is_breakout=True)
        eth_res = run_leg(eth_va, tp, is_breakout=False)
        bm, em = btc_res.to_dict(), eth_res.to_dict()
        cm = merge_metrics(btc_res, eth_res)

        cp = passes(cm)
        rows.append({"w": wi, "combo": cm, "btc": bm, "eth": em, "passed": cp})
        print(f"  BTC breakout: sharpe={bm['sharpe_ratio']:.2f} ret={bm['total_return_pct']:+.1f}% dd={bm['max_drawdown_pct']:.1f}%")
        print(f"  ETH trend   : sharpe={em['sharpe_ratio']:.2f} ret={em['total_return_pct']:+.1f}% dd={em['max_drawdown_pct']:.1f}%")
        print(f"  COMBO       : sharpe={cm['sharpe_ratio']:.2f} ret={cm['total_return_pct']:+.1f}% dd={cm['max_drawdown_pct']:.1f}% "
              f"win={cm['win_rate_pct']:.0f}% pf={cm['profit_factor']:.2f} → {'PASS' if cp else 'FAIL'}\n")

    if not rows:
        print("No results"); return
    print(f"{'='*60}\nSUMMARY ({len(rows)} windows)\n{'='*60}")
    combo_pass = sum(1 for r in rows if r["passed"])
    btc_pos = sum(1 for r in rows if r["btc"]["sharpe_ratio"] > 0)
    eth_pos = sum(1 for r in rows if r["eth"]["sharpe_ratio"] > 0)
    combo_pos = sum(1 for r in rows if r["combo"]["sharpe_ratio"] > 0)
    print(f"  COMBO pass all SPEC: {combo_pass}/{len(rows)} ({combo_pass/len(rows)*100:.0f}%)")
    print(f"  正 Sharpe 窗口: BTC={btc_pos}/{len(rows)}  ETH={eth_pos}/{len(rows)}  COMBO={combo_pos}/{len(rows)}")
    print(f"  COMBO 均值: sharpe={np.mean([r['combo']['sharpe_ratio'] for r in rows]):.2f} "
          f"ret={np.mean([r['combo']['total_return_pct'] for r in rows]):+.1f}% "
          f"dd={np.mean([r['combo']['max_drawdown_pct'] for r in rows]):.1f}%")


if __name__ == "__main__":
    main()
