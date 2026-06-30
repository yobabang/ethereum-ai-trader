"""Offline AI pipeline check — runs the full decision pipeline on REAL
historical OHLCV data, with NO OKX connection and NO order placement.

This is a read-only validation harness. It reuses the same AI core as
LiveTrader._run_ai_pipeline (engine/live_trader.py:89-170) but:
  - reads local feather files instead of ccxt.fetch_ohlcv
  - never calls create_order / connect / any exchange write
  - evaluates model effectiveness across many candles, not just the latest

Usage:
    python scripts/offline_pipeline_check.py
    python scripts/offline_pipeline_check.py --model-dir ./models_real --window 500
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Allow running from project root
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.features import FeatureEngineer
from engine.regime_classifier import RegimeClassifier
from engine.direction_predictor import DirectionPredictor
from engine.decision_arbitrator import DecisionArbitrator, RiskCalculator

DATA_DIR = ROOT / "user_data" / "data" / "okx"
PAIRS = ["BTC/USDT:USDT", "ETH/USDT:USDT"]
TIMEFRAME = "1h"

# Mirror LiveTrader risk params (engine/live_trader.py:48-52) so decisions match
MIN_CONFIDENCE = 0.45
MIN_SIGNAL = 0.0003


def load_ohlcv(pair: str) -> pd.DataFrame:
    safe = pair.replace("/", "_").replace(":", "_")
    p = DATA_DIR / f"{safe}-{TIMEFRAME}-futures.feather"
    if not p.exists():
        raise FileNotFoundError(f"No data at {p}")
    df = pd.read_feather(p)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def fuse_rl(lgbm_er: float, lgbm_conf: float, rl_pred: list[dict] | None):
    """Dual-signal fusion. RL veto overrides LGBM on disagreement.

    Mirrors the corrected logic in live_trader.py (_run_ai_pipeline).
    rl.predict() returns list[dict]; we extract expected_return to get RL direction.
    """
    if not rl_pred:
        return lgbm_er, lgbm_conf, "lgbm_only"
    rl = rl_pred[0]
    rl_er = rl["expected_return"]
    rl_action = "long" if rl_er > 0.001 else "short" if rl_er < -0.001 else "hold"
    lgbm_action = "long" if lgbm_er > 0.001 else "short" if lgbm_er < -0.001 else "hold"

    if rl_action in ("long", "short") and lgbm_action != rl_action and lgbm_action != "hold":
        # RL vetoes and overrides
        er = 0.003 if rl_action == "long" else -0.003
        conf = max(lgbm_conf, 0.65)
        return er, conf, f"rl_override({rl_action})"
    return lgbm_er, lgbm_conf, "agree"


def run_pipeline_for_pair(pair: str, model_dir: str, window: int, use_rl: bool):
    print(f"\n{'='*70}\n{pair}  (model_dir={model_dir}, window={window})\n{'='*70}")

    df = load_ohlcv(pair)
    # Use the last (window + 60) rows: 60 for EMA50/indicator warmup at the start of the window
    df = df.tail(window + 60).reset_index(drop=True)

    fe = FeatureEngineer()
    features = fe.compute_price_features(df)

    rc = RegimeClassifier(model_dir=model_dir)
    dp = DirectionPredictor(model_dir=model_dir)
    rc.load()
    dp.load()

    # Optional RL from original models/ (not retrained — may fail to match features)
    rl = None
    if use_rl:
        try:
            from engine.rl_signal import RlSignalAgent
            rl = RlSignalAgent(model_dir=str(ROOT / "models"))
            if not rl.load():
                print("  [RL] model not loaded — LightGBM-only mode")
                rl = None
        except Exception as e:
            print(f"  [RL] unavailable: {e} — LightGBM-only mode")
            rl = None

    arb = DecisionArbitrator(RiskCalculator())

    # Skip warmup rows; evaluate one decision per candle
    start = 60
    rows = []
    for i in range(start, len(features) - 1):
        row = features.iloc[i : i + 1]
        regime_list = rc.predict(row)
        preds = dp.predict(row)
        regime = regime_list[0] if regime_list else None
        p = preds[0] if preds else None
        if p is None or regime is None:
            continue

        er = p["expected_return"]
        conf = p["confidence"]

        rl_pred = rl.predict(features.iloc[max(0, i - 49) : i + 1]) if rl else None
        er, conf, fusion = fuse_rl(er, conf, rl_pred)

        # Confidence / signal gates (mirror LiveTrader)
        if conf < MIN_CONFIDENCE:
            action, reason = "HOLD", f"conf {conf:.2f}<{MIN_CONFIDENCE}"
        elif abs(er) < MIN_SIGNAL:
            action, reason = "HOLD", "signal<noise"
        else:
            # EMA50 trend filter
            e50 = df["close"].iloc[: i + 1].ewm(span=50).mean().iloc[-1]
            pr = float(df["close"].iloc[i])
            if (er > 0 and pr < e50) or (er < 0 and pr > e50):
                action, reason = "HOLD", "counter-trend(EMA50)"
            else:
                decision = arb.decide(
                    account_equity=735.0, current_positions=[],
                    regime=regime, expected_return=er,
                    confidence=conf, max_drawdown=p["max_drawdown"],
                    atr_pct=float(features["atr_ratio"].iloc[i]) if "atr_ratio" in features.columns else 0.015,
                    adaptive_confidence=MIN_CONFIDENCE, adaptive_position_scalar=1.0,
                )
                action = decision.action.value
                reason = decision.reason[:50]

        # Actual next-candle direction (ground truth)
        next_close = float(df["close"].iloc[i + 1])
        cur_close = float(df["close"].iloc[i])
        actual_ret = next_close / cur_close - 1
        actual_dir = "long" if actual_ret > 0 else "short"

        pred_dir = "long" if er > 0 else "short" if er < 0 else "flat"
        dir_correct = pred_dir == actual_dir

        rows.append({
            "regime": regime, "action": action, "reason": reason,
            "pred_dir": pred_dir, "actual_dir": actual_dir,
            "dir_correct": dir_correct, "fusion": fusion,
            "er": er, "conf": conf,
        })

    return _summarize(pair, rows)


def _summarize(pair: str, rows: list[dict]) -> dict:
    n = len(rows)
    if n == 0:
        print("  No evaluable rows.")
        return {}
    df = pd.DataFrame(rows)
    from collections import Counter
    act_dist = Counter(df["action"])
    regime_dist = Counter(df["regime"])
    dir_acc = df["dir_correct"].mean()

    # Directional accuracy AMONG trade signals only (where model committed to long/short pre-gate)
    # Use pred_dir on rows that survived to a LONG/SHORT action
    traded = df[df["action"].isin(["long", "short"])]
    traded_dir_acc = traded["dir_correct"].mean() if len(traded) else float("nan")

    print(f"\n  candles evaluated : {n}")
    print(f"  action distribution: {dict(act_dist)}")
    print(f"  regime distribution: {dict(regime_dist)}")
    print(f"  pred-direction accuracy (all candles) : {dir_acc:.3f}  (0.5 = coin flip)")
    if len(traded):
        print(f"  pred-direction accuracy (LONG/SHORT only, n={len(traded)}): {traded_dir_acc:.3f}")
    else:
        print("  NO long/short signals fired — pipeline held on every candle.")
    print(f"  fusion breakdown: {dict(Counter(df['fusion']))}")

    # Sample a few decisions
    print("\n  sample decisions (first 5 trade signals or first 5 rows):")
    sample = traded.head(5) if len(traded) else df.head(5)
    for _, r in sample.iterrows():
        print(f"    {r['action']:5} regime={r['regime']:18} pred={r['pred_dir']:5} "
              f"actual={r['actual_dir']:5} {'OK' if r['dir_correct'] else 'XX'} "
              f"er={r['er']:+.4f} conf={r['conf']:.2f} [{r['fusion']}]")

    return {
        "pair": pair, "n": n, "action_dist": dict(act_dist),
        "dir_acc": dir_acc, "traded_dir_acc": traded_dir_acc,
        "traded_n": len(traded),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default="./models_real", help="LightGBM model dir")
    ap.add_argument("--window", type=int, default=500, help="Recent candles to evaluate")
    ap.add_argument("--no-rl", action="store_true", help="Skip RL dual-signal")
    args = ap.parse_args()

    model_dir = args.model_dir if Path(args.model_dir).is_absolute() else str(ROOT / args.model_dir)
    print(f"Offline pipeline check — REAL data, NO OKX, NO orders")
    print(f"data: {DATA_DIR}  model_dir: {model_dir}  rl: {not args.no_rl}")

    results = []
    for pair in PAIRS:
        try:
            results.append(run_pipeline_for_pair(pair, model_dir, args.window, not args.no_rl))
        except Exception as e:
            print(f"\n[ERROR] {pair}: {e}")
            import traceback; traceback.print_exc()

    print(f"\n{'='*70}\nSUMMARY\n{'='*70}")
    for r in results:
        if not r:
            continue
        verdict = "EFFECTIVE" if r["traded_dir_acc"] and r["traded_dir_acc"] > 0.55 else \
                  "COIN-FLIP" if r["traded_dir_acc"] and abs(r["traded_dir_acc"] - 0.5) < 0.05 else \
                  "INEFFECTIVE" if r["traded_dir_acc"] and r["traded_dir_acc"] < 0.45 else \
                  "NO-SIGNALS" if r["traded_n"] == 0 else "UNCLEAR"
        print(f"  {r['pair']:18} dir_acc={r['dir_acc']:.3f}  traded_acc={r['traded_dir_acc'] if r['traded_dir_acc']==r['traded_dir_acc'] else 'n/a'}  "
              f"trades={r['traded_n']}  -> {verdict}")


if __name__ == "__main__":
    main()
