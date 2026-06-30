"""Walk-forward validation — strict out-of-sample, no data leakage.

For each rolling window:
  1. Train models on the TRAIN segment (saved to a temp dir)
  2. Backtest + measure direction accuracy on the next OOS TEST segment
  3. Record metrics
Train and test segments NEVER overlap in time.

Compares OHLCV-only vs OHLCV+derivatives to show whether derivatives
features actually improve out-of-sample prediction.

Usage:
    python scripts/walkforward_verify.py
    python scripts/walkforward_verify.py --derivatives --windows 4
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.features import FeatureEngineer
from engine.regime_classifier import RegimeClassifier
from engine.direction_predictor import DirectionPredictor
from engine.decision_arbitrator import DecisionArbitrator, RiskCalculator
from engine.backtest_adapter import AIBacktestAdapter
from engine.trainer import load_historical_data, load_derivatives_data

DATA_DIR = ROOT / "user_data" / "data"
PAIRS = ["BTC/USDT:USDT", "ETH/USDT:USDT"]
TIMEFRAME = "1h"

MIN_CONFIDENCE = 0.45
SPEC = {"sharpe": 0.5, "max_dd": 0.15, "win_rate": 0.40, "profit_factor": 1.5}


def load_pair(pair: str, use_deriv: bool):
    ohlcv = load_historical_data(str(DATA_DIR), pair, timeframe=TIMEFRAME)
    ohlcv["date"] = pd.to_datetime(ohlcv["date"])
    deriv = load_derivatives_data(str(DATA_DIR), pair, timeframe=TIMEFRAME) if use_deriv else None
    # NOTE: do NOT pre-convert deriv['date'] here. It is ms-epoch int64 from
    # the pull script; compute_all_features/_to_datetime handles it via unit='ms'.
    # pd.to_datetime(int) defaults to NANOseconds → 1970 dates → merge fails.
    return ohlcv, deriv


def make_windows(n_rows: int, n_windows: int, train_frac: float = 0.6):
    """Return list of (train_start, train_end, test_start, test_end) index ranges.

    Windows roll forward; train and test are contiguous and non-overlapping.
    """
    total = n_rows
    # Each window covers train_size + test_size; windows roll by test_size
    test_size = (total - int(total * train_frac)) // n_windows
    train_size = int(total * train_frac)
    windows = []
    start = 0
    for w in range(n_windows):
        t_end = start + train_size
        v_start = t_end
        v_end = v_start + test_size
        if v_end > total:
            break
        windows.append((start, t_end, v_start, v_end))
        start += test_size  # roll forward
    return windows


def train_on_slice(ohlcv_slice, deriv_slice, model_dir, use_deriv):
    """Train regime + direction models on a slice, save to model_dir.

    If derivatives are requested but mostly NaN in this slice (exchange only
    keeps recent history), automatically degrade to OHLCV-only so training
    doesn't crash on <100 valid rows. Prints a clear warning.
    """
    fe = FeatureEngineer()
    if use_deriv and deriv_slice is not None:
        feats = fe.compute_all_features(ohlcv_slice, deriv_slice)
        # Check if derivatives columns have enough non-NaN to be usable
        deriv_cols = [c for c in feats.columns
                      if c in ("funding_rate", "open_interest", "long_short_ratio",
                               "taker_buy_sell_ratio", "funding_signal")]
        if deriv_cols:
            deriv_valid = feats[deriv_cols].notna().any(axis=1).sum()
            if deriv_valid < 100:
                print(f"    [WARN] only {deriv_valid} rows have derivatives in this slice "
                      f"(exchange history limit) — degrading to OHLCV-only training")
                feats = fe.compute_price_features(ohlcv_slice)
    else:
        feats = fe.compute_price_features(ohlcv_slice)
    rc = RegimeClassifier(model_dir=model_dir)
    rc.train(feats, label_horizon=6)
    dp = DirectionPredictor(model_dir=model_dir)
    dp.train(feats, horizon=4)
    return feats


def measure_dir_accuracy(ohlcv_slice, deriv_slice, model_dir, use_deriv):
    """Out-of-sample direction accuracy: predict t+1 return sign.

    Mirrors train_on_slice's degradation: if derivatives are mostly NaN here,
    use OHLCV-only features to match an OHLCV-only-trained model.
    """
    fe = FeatureEngineer()
    if use_deriv and deriv_slice is not None:
        feats = fe.compute_all_features(ohlcv_slice, deriv_slice)
        deriv_cols = [c for c in feats.columns
                      if c in ("funding_rate", "open_interest", "long_short_ratio",
                               "taker_buy_sell_ratio", "funding_signal")]
        if deriv_cols and feats[deriv_cols].notna().any(axis=1).sum() < 100:
            feats = fe.compute_price_features(ohlcv_slice)
    else:
        feats = fe.compute_price_features(ohlcv_slice)
    rc = RegimeClassifier(model_dir=model_dir); rc.load()
    dp = DirectionPredictor(model_dir=model_dir); dp.load()

    close = ohlcv_slice["close"].values
    correct, total = 0, 0
    n = len(feats)
    for i in range(60, n - 1):
        row = feats.iloc[i:i+1]
        preds = dp.predict(row)
        if not preds or not preds[-1]:
            continue
        er = preds[-1]["expected_return"]
        pred_dir = 1 if er > 0 else -1
        actual = 1 if close[i+1] > close[i] else -1
        correct += int(pred_dir == actual)
        total += 1
    return (correct / total if total else 0.0), total


def backtest_slice(ohlcv_slice, deriv_slice, model_dir, use_deriv):
    bt = AIBacktestAdapter(model_dir=model_dir, initial_equity=5000.0,
                           max_position_pct=0.20, max_leverage=5)
    res = bt.run(ohlcv_slice, pair="x", derivatives=deriv_slice if use_deriv else None)
    return res.to_dict()


def run_pair(pair: str, use_deriv: bool, n_windows: int):
    print(f"\n{'='*70}\n{pair}  derivatives={'ON' if use_deriv else 'OFF'}\n{'='*70}")
    ohlcv, deriv = load_pair(pair, use_deriv)
    if deriv is not None:
        print(f"  derivatives rows: {len(deriv)}")
    print(f"  ohlcv rows: {len(ohlcv)}  range: {ohlcv['date'].iloc[0]} .. {ohlcv['date'].iloc[-1]}")

    windows = make_windows(len(ohlcv), n_windows)
    print(f"  {len(windows)} walk-forward windows")
    if not windows:
        print("  [SKIP] not enough data for windows")
        return []

    results = []
    for wi, (s, te, vs, ve) in enumerate(windows):
        tmp = tempfile.mkdtemp(prefix=f"wf_{pair[:3]}_w{wi}_")
        try:
            ohlcv_tr = ohlcv.iloc[s:te].reset_index(drop=True)
            ohlcv_va = ohlcv.iloc[vs:ve].reset_index(drop=True)
            deriv_tr = deriv.iloc[s:te].reset_index(drop=True) if deriv is not None else None
            deriv_va = deriv.iloc[vs:ve].reset_index(drop=True) if deriv is not None else None

            # If derivatives are mostly NaN in this slice (exchange history limit),
            # degrade to OHLCV-only for ALL of train/measure/backtest so they stay consistent.
            degraded = False
            if use_deriv and deriv_tr is not None:
                from engine.features import FeatureEngineer as _FE
                _chk = _FE().compute_all_features(ohlcv_tr, deriv_tr)
                _dcols = [c for c in _chk.columns if c in ("funding_rate","open_interest","long_short_ratio","funding_signal")]
                if _dcols and _chk[_dcols].notna().any(axis=1).sum() < 100:
                    print(f"  W{wi}: derivatives insufficient in slice — OHLCV-only mode")
                    deriv_tr = None; deriv_va = None; degraded = True

            train_on_slice(ohlcv_tr, deriv_tr, tmp, use_deriv)
            dir_acc, dir_n = measure_dir_accuracy(ohlcv_va, deriv_va, tmp, use_deriv)
            bt = backtest_slice(ohlcv_va, deriv_va, tmp, use_deriv)

            r = {
                "window": wi,
                "train": f"{ohlcv_tr['date'].iloc[0].date()}..{ohlcv_tr['date'].iloc[-1].date()}",
                "test": f"{ohlcv_va['date'].iloc[0].date()}..{ohlcv_va['date'].iloc[-1].date()}",
                "dir_acc": dir_acc, "dir_n": dir_n,
                "sharpe": bt["sharpe_ratio"], "max_dd": bt["max_drawdown_pct"],
                "win_rate": bt["win_rate_pct"], "pf": bt["profit_factor"],
                "return": bt["total_return_pct"], "trades": bt["total_trades"],
            }
            results.append(r)
            print(f"  W{wi} train={r['train']} test={r['test']} | "
                  f"dir_acc={r['dir_acc']:.3f}({r['dir_n']}) sharpe={r['sharpe']:.2f} "
                  f"dd={r['max_dd']:.1f}% win={r['win_rate']:.0f}% pf={r['pf']:.2f} "
                  f"ret={r['return']:+.1f}% trades={r['trades']}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return results


def summarize(name: str, all_results: list):
    if not all_results:
        print(f"\n[{name}] no results")
        return
    dir_accs = [r["dir_acc"] for r in all_results]
    sharpes = [r["sharpe"] for r in all_results]
    pfs = [r["pf"] for r in all_results]
    wins = [r["win_rate"] for r in all_results]
    # A window "passes" if all 4 SPEC criteria met
    passed = sum(1 for r in all_results
                 if r["sharpe"] > SPEC["sharpe"] and r["max_dd"] < SPEC["max_dd"]*100
                 and r["win_rate"] > SPEC["win_rate"] and r["pf"] > SPEC["profit_factor"])
    print(f"\n--- {name} ---")
    print(f"  windows={len(all_results)}  pass_all_spec={passed}/{len(all_results)} ({passed/len(all_results)*100:.0f}%)")
    print(f"  dir_acc : mean={np.mean(dir_accs):.3f}  min={np.min(dir_accs):.3f}  max={np.max(dir_accs):.3f}")
    print(f"  sharpe  : mean={np.mean(sharpes):.3f}  std={np.std(sharpes):.3f}")
    print(f"  pf      : mean={np.mean(pfs):.2f}")
    print(f"  win_rate: mean={np.mean(wins):.1f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", type=int, default=3, help="Number of walk-forward windows")
    ap.add_argument("--derivatives", action="store_true", help="Use derivatives features")
    ap.add_argument("--compare", action="store_true", help="Run both OHLCV-only and +derivatives for comparison")
    args = ap.parse_args()

    print(f"Walk-forward validation — strict OOS, NO leakage")
    print(f"pairs={PAIRS} windows={args.windows} derivatives={args.derivatives or args.compare}")

    if args.compare:
        for label, use_deriv in [("OHLCV-only", False), ("OHLCV+derivatives", True)]:
            all_r = []
            for pair in PAIRS:
                all_r.extend(run_pair(pair, use_deriv, args.windows))
            summarize(label, all_r)
    else:
        all_r = []
        for pair in PAIRS:
            all_r.extend(run_pair(pair, args.derivatives, args.windows))
        summarize("OHLCV+derivatives" if args.derivatives else "OHLCV-only", all_r)


if __name__ == "__main__":
    main()
