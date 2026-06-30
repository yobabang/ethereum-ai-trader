"""
AI Direction Predictor Comprehensive Test
=========================================
Tests the AI Direction Predictor on real OKX 4h futures data.
Runs 1000 trades per pair (BTC and ETH) with 5x leverage.

Three strategies compared per pair:
  1. AI Direction Predictor (based on expected_return)
  2. Random Direction (alternating long/short)
  3. Pure Long (buy and hold each candle)

Trading logic:
  - expected_return >  0.002 → LONG at open, close at close
  - expected_return < -0.002 → SHORT at open, close at close
  - |expected_return| <= 0.002 → SKIP (hold)
  - AI confidence must be >= 0.50
  - Full position each trade: position = equity * leverage
  - Liquidation: 1/leverage = 20% adverse move (since leverage=5)
"""

import sys
import time
import json
from datetime import datetime
from pathlib import Path

sys.path.insert(0, '.')

import numpy as np
import pandas as pd

# =====================================================================
# Configuration
# =====================================================================
INITIAL_EQUITY = 1000.0
LEVERAGE = 5
MAX_TRADES = 1000
WARMUP = 50
ENTRY_THRESHOLD = 0.002  # 0.2% expected return threshold
MIN_CONFIDENCE = 0.50
DATA_DIR = Path("user_data/data/okx")
MODEL_DIR = Path("../ethereum-ai-trader/models")

# Liquidation: at 5x leverage, 20% adverse move = full liquidation
LIQUIDATION_THRESHOLD = 1.0 / LEVERAGE  # 0.20 = 20%

# =====================================================================
# Load modules
# =====================================================================
from engine.features import FeatureEngineer
from engine.direction_predictor import DirectionPredictor
from engine.regime_classifier import RegimeClassifier


def load_data(pair_safe: str) -> pd.DataFrame:
    """Load and prepare OKX 4h futures data."""
    df = pd.read_feather(DATA_DIR / f"{pair_safe}-4h-futures.feather")
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def load_models(features: pd.DataFrame):
    """Load or train AI models."""
    fe = FeatureEngineer()
    rc = RegimeClassifier(model_dir=str(MODEL_DIR))
    dp = DirectionPredictor(model_dir=str(MODEL_DIR))

    try:
        rc.load()
        print("    [OK] RegimeClassifier loaded from disk")
    except Exception:
        print("    [..] Training RegimeClassifier...")
        rc.train(features)

    try:
        dp.load()
        print("    [OK] DirectionPredictor loaded from disk")
    except Exception:
        print("    [..] Training DirectionPredictor...")
        dp.train(features)

    return fe, rc, dp


def run_ai_test(df: pd.DataFrame, features: pd.DataFrame, dp: DirectionPredictor,
                pair_name: str, strategy_label: str = "AI") -> dict:
    """
    Run the AI Direction Predictor test.

    Trading rules:
      - expected_return >  0.002 → LONG
      - expected_return < -0.002 → SHORT
      - |expected_return| <= 0.002 → SKIP
      - Only trade if confidence >= 0.50
      - Full position size = equity * leverage
      - Liquidation if 20% adverse move
    """
    equity = INITIAL_EQUITY
    peak = INITIAL_EQUITY
    max_drawdown = 0.0
    trades = []
    liquidated = False
    liq_info = None

    ai_preds = dp.predict(features)
    total_candles = len(df)

    for i in range(WARMUP, total_candles - 1):
        if len(trades) >= MAX_TRADES:
            break
        if equity <= 0:
            break

        o = float(df["open"].iloc[i])
        c = float(df["close"].iloc[i])
        h = float(df["high"].iloc[i])
        l = float(df["low"].iloc[i])

        pred = ai_preds[i] if ai_preds and i < len(ai_preds) else None

        # Decision logic
        if pred is None or pred["confidence"] < MIN_CONFIDENCE:
            continue  # Skip - insufficient confidence

        exp_return = pred["expected_return"]

        if exp_return > ENTRY_THRESHOLD:
            side = "long"
        elif exp_return < -ENTRY_THRESHOLD:
            side = "short"
        else:
            continue  # Skip - return too small

        # Calculate PnL
        if side == "long":
            pnl_pct = (c / o - 1) * LEVERAGE
            liq_price = o * (1 - LIQUIDATION_THRESHOLD)
            liq_hit = l <= liq_price
        else:
            pnl_pct = (1 - c / o) * LEVERAGE
            liq_price = o * (1 + LIQUIDATION_THRESHOLD)
            liq_hit = h >= liq_price

        pnl = equity * pnl_pct

        if liq_hit:
            pnl = -equity
            liquidated = True
            liq_info = {
                "candle": i,
                "time": str(df["date"].iloc[i]),
                "entry_price": o,
                "liq_price": liq_price,
                "side": side,
            }
            trades.append({
                "candle": i,
                "time": str(df["date"].iloc[i]),
                "side": side,
                "entry": o,
                "exit": liq_price,
                "pnl": pnl,
                "pnl_pct": -100.0,
                "liquidated": True,
                "expected_return": exp_return,
                "confidence": pred["confidence"],
            })
            equity += pnl
            break

        equity += pnl
        trades.append({
            "candle": i,
            "time": str(df["date"].iloc[i]),
            "side": side,
            "entry": o,
            "exit": c,
            "pnl": pnl,
            "pnl_pct": pnl_pct * 100,
            "liquidated": False,
            "expected_return": exp_return,
            "confidence": pred["confidence"],
        })

        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0
        if dd > max_drawdown:
            max_drawdown = dd

    return _compute_stats(pair_name, strategy_label, trades, equity,
                          liquidated, liq_info, max_drawdown)


def run_random_test(df: pd.DataFrame, pair_name: str,
                    strategy_label: str = "Random") -> dict:
    """
    Run the Random Direction test (alternating long/short).
    Same trading rules as AI but direction is random (alternating).
    """
    equity = INITIAL_EQUITY
    peak = INITIAL_EQUITY
    max_drawdown = 0.0
    trades = []
    liquidated = False
    liq_info = None

    total_candles = len(df)

    for i in range(WARMUP, total_candles - 1):
        if len(trades) >= MAX_TRADES:
            break
        if equity <= 0:
            break

        o = float(df["open"].iloc[i])
        c = float(df["close"].iloc[i])
        h = float(df["high"].iloc[i])
        l = float(df["low"].iloc[i])

        # Alternating direction (random-like without foresight)
        side = "long" if (i % 2 == 0) else "short"

        if side == "long":
            pnl_pct = (c / o - 1) * LEVERAGE
            liq_price = o * (1 - LIQUIDATION_THRESHOLD)
            liq_hit = l <= liq_price
        else:
            pnl_pct = (1 - c / o) * LEVERAGE
            liq_price = o * (1 + LIQUIDATION_THRESHOLD)
            liq_hit = h >= liq_price

        pnl = equity * pnl_pct

        if liq_hit:
            pnl = -equity
            liquidated = True
            liq_info = {
                "candle": i,
                "time": str(df["date"].iloc[i]),
                "entry_price": o,
                "liq_price": liq_price,
                "side": side,
            }
            trades.append({
                "candle": i,
                "time": str(df["date"].iloc[i]),
                "side": side,
                "entry": o,
                "exit": liq_price,
                "pnl": pnl,
                "pnl_pct": -100.0,
                "liquidated": True,
            })
            equity += pnl
            break

        equity += pnl
        trades.append({
            "candle": i,
            "time": str(df["date"].iloc[i]),
            "side": side,
            "entry": o,
            "exit": c,
            "pnl": pnl,
            "pnl_pct": pnl_pct * 100,
            "liquidated": False,
        })

        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0
        if dd > max_drawdown:
            max_drawdown = dd

    return _compute_stats(pair_name, strategy_label, trades, equity,
                          liquidated, liq_info, max_drawdown)


def run_pure_long_test(df: pd.DataFrame, pair_name: str,
                       strategy_label: str = "PureLong") -> dict:
    """
    Run the Pure Long test - always go long regardless of AI signal.
    Same position sizing and risk rules.
    """
    equity = INITIAL_EQUITY
    peak = INITIAL_EQUITY
    max_drawdown = 0.0
    trades = []
    liquidated = False
    liq_info = None

    total_candles = len(df)

    for i in range(WARMUP, total_candles - 1):
        if len(trades) >= MAX_TRADES:
            break
        if equity <= 0:
            break

        o = float(df["open"].iloc[i])
        c = float(df["close"].iloc[i])
        h = float(df["high"].iloc[i])
        l = float(df["low"].iloc[i])

        # Always long
        pnl_pct = (c / o - 1) * LEVERAGE
        liq_price = o * (1 - LIQUIDATION_THRESHOLD)
        liq_hit = l <= liq_price

        pnl = equity * pnl_pct

        if liq_hit:
            pnl = -equity
            liquidated = True
            liq_info = {
                "candle": i,
                "time": str(df["date"].iloc[i]),
                "entry_price": o,
                "liq_price": liq_price,
                "side": "long",
            }
            trades.append({
                "candle": i,
                "time": str(df["date"].iloc[i]),
                "side": "long",
                "entry": o,
                "exit": liq_price,
                "pnl": pnl,
                "pnl_pct": -100.0,
                "liquidated": True,
            })
            equity += pnl
            break

        equity += pnl
        trades.append({
            "candle": i,
            "time": str(df["date"].iloc[i]),
            "side": "long",
            "entry": o,
            "exit": c,
            "pnl": pnl,
            "pnl_pct": pnl_pct * 100,
            "liquidated": False,
        })

        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0
        if dd > max_drawdown:
            max_drawdown = dd

    return _compute_stats(pair_name, strategy_label, trades, equity,
                          liquidated, liq_info, max_drawdown)


def _compute_stats(pair_name, strategy_label, trades, final_equity,
                   liquidated, liq_info, max_drawdown):
    """Compute comprehensive statistics from trade list."""
    n = len(trades)
    return_pct = (final_equity / INITIAL_EQUITY - 1) * 100

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]

    win_rate = len(wins) / n * 100 if n > 0 else 0.0
    avg_win = np.mean([t["pnl"] for t in wins]) if wins else 0.0
    avg_loss = np.mean([t["pnl"] for t in losses]) if losses else 0.0
    max_win = max(t["pnl"] for t in trades) if trades else 0.0
    max_loss = min(t["pnl"] for t in trades) if trades else 0.0

    # Profit factor
    gross_profit = sum(t["pnl"] for t in wins) if wins else 0.0
    gross_loss = abs(sum(t["pnl"] for t in losses)) if losses else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Equity curve milestones at 0, 250, 500, 750, 1000
    equity_curve = [INITIAL_EQUITY]
    for t in trades:
        equity_curve.append(equity_curve[-1] + t["pnl"])
    milestones = {}
    for m in [0, 250, 500, 750, 1000]:
        idx = min(m, len(equity_curve) - 1)
        milestones[m] = round(equity_curve[idx], 2)

    # Consecutive losses
    max_consec_losses = 0
    current_cl = 0
    for t in trades:
        if t["pnl"] <= 0:
            current_cl += 1
            if current_cl > max_consec_losses:
                max_consec_losses = current_cl
        else:
            current_cl = 0

    # Sharpe-like ratio (using trade returns)
    trade_returns = [t["pnl_pct"] / 100 for t in trades]
    avg_return = np.mean(trade_returns) if trade_returns else 0
    std_return = np.std(trade_returns) if trade_returns else 1
    sharpe = (avg_return / std_return * np.sqrt(365)) if std_return > 0 else 0

    # Long/Short breakdown
    longs = [t for t in trades if t["side"] == "long"]
    shorts = [t for t in trades if t["side"] == "short"]
    long_wins = [t for t in longs if t["pnl"] > 0]
    short_wins = [t for t in shorts if t["pnl"] > 0]

    return {
        "pair": pair_name,
        "strategy": strategy_label,
        "initial": INITIAL_EQUITY,
        "final": round(final_equity, 2),
        "return_pct": round(return_pct, 2),
        "liquidated": liquidated,
        "liq_info": liq_info,
        "max_dd_pct": round(max_drawdown * 100, 2),
        "total_trades": n,
        "longs": len(longs),
        "shorts": len(shorts),
        "long_wins": len(long_wins),
        "short_wins": len(short_wins),
        "long_win_rate": round(len(long_wins) / len(longs) * 100, 1) if longs else 0,
        "short_win_rate": round(len(short_wins) / len(shorts) * 100, 1) if shorts else 0,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "max_win": round(max_win, 2),
        "max_loss": round(max_loss, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else "inf",
        "max_consecutive_losses": max_consec_losses,
        "sharpe_ratio": round(sharpe, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "equity_curve_milestones": milestones,
        "avg_trade_return_pct": round(np.mean(trade_returns) * 100, 3) if trade_returns else 0,
    }


def run_single_pair(pair_safe: str, pair_name: str) -> dict:
    """Run all three strategies for a single pair."""
    print(f"\n  Loading {pair_name} data...")
    df = load_data(pair_safe)
    print(f"    {len(df)} candles ({df['date'].min()} to {df['date'].max()})")

    print(f"  Computing features...")
    fe, rc, dp = load_models(features=None)
    features = fe.compute_price_features(df)
    print(f"    {features.shape[1]} feature columns, {features.shape[0]} rows")

    # Load models (now we have features)
    rc.load()
    dp.load()

    # Run AI Predictor test
    print(f"  Running AI Direction Predictor test...")
    t0 = time.time()
    ai_result = run_ai_test(df, features, dp, pair_name, "AI Predictor")
    ai_time = time.time() - t0
    print(f"    Done in {ai_time:.1f}s, {ai_result['total_trades']} trades")

    # Run Random test
    print(f"  Running Random Direction test...")
    t0 = time.time()
    rand_result = run_random_test(df, pair_name, "Random Direction")
    rand_time = time.time() - t0
    print(f"    Done in {rand_time:.1f}s, {rand_result['total_trades']} trades")

    # Run Pure Long test
    print(f"  Running Pure Long test...")
    t0 = time.time()
    long_result = run_pure_long_test(df, pair_name, "Pure Long")
    long_time = time.time() - t0
    print(f"    Done in {long_time:.1f}s, {long_result['total_trades']} trades")

    return {"AI": ai_result, "Random": rand_result, "PureLong": long_result, "pair": pair_name}


def format_number(n):
    """Format number with commas."""
    if isinstance(n, float):
        return f"{n:,.2f}"
    return f"{n:,}"


def print_separator(char="=", width=72):
    print(char * width)


def print_results(results, pair_name):
    """Print formatted results for all three strategies."""
    print_separator()
    print(f"  {pair_name} — 策略对比 (5x杠杆, ${INITIAL_EQUITY:,}USDT, {MAX_TRADES}笔)")
    print_separator()

    strategies = ["AI Predictor", "Random Direction", "Pure Long"]
    data_map = {"AI Predictor": results["AI"],
                "Random Direction": results["Random"],
                "Pure Long": results["PureLong"]}

    # Header
    print(f"  {'指标':<22} {'AI方向预测':<20} {'随机方向':<18} {'纯多头':<14}")
    print(f"  {'-'*22} {'-'*20} {'-'*18} {'-'*14}")

    # Equity & return
    for s in strategies:
        d = data_map[s]
        print(f"  {'最终资产(USD)':<22} ", end="")
        print(f"${d['final']:<16,} ", end="")

    print()
    for s in strategies:
        d = data_map[s]
        ret = d["return_pct"]
        ret_str = f"{ret:+.2f}%"
        if ret > 0:
            ret_str = ret_str
        print(f"  {'总收益率':<22} ", end="")
        print(f"{ret_str:<20} ", end="")
    print()

    # Liquidation
    for s in strategies:
        d = data_map[s]
        liq_str = "是(爆仓)" if d["liquidated"] else "否(存活)"
        print(f"  {'爆仓':<22} ", end="")
        print(f"{liq_str:<20} ", end="")
    print()

    # Max drawdown
    for s in strategies:
        d = data_map[s]
        print(f"  {'最大回撤':<22} ", end="")
        print(f"{d['max_dd_pct']:.2f}%{'':<14} ", end="")
    print()

    # Win rate
    for s in strategies:
        d = data_map[s]
        print(f"  {'胜率':<22} ", end="")
        print(f"{d['win_rate']:.1f}%{'':<15} ", end="")
    print()

    # Profit factor
    for s in strategies:
        d = data_map[s]
        pf = d["profit_factor"]
        print(f"  {'盈亏比(Profit Factor)':<22} ", end="")
        print(f"{pf:<20} ", end="")
    print()

    # Total trades
    for s in strategies:
        d = data_map[s]
        print(f"  {'总交易数':<22} ", end="")
        print(f"{d['total_trades']:<20} ", end="")
    print()

    # Long/Short breakdown
    for s in strategies:
        d = data_map[s]
        print(f"  {'做多/做空':<22} ", end="")
        print(f"{d['longs']}/{d['shorts']:<17} ", end="")
    print()

    for s in strategies:
        d = data_map[s]
        print(f"  {'多/空胜率':<22} ", end="")
        print(f"{d['long_win_rate']:.0f}%/{d['short_win_rate']:.0f}%{'':<14} ", end="")
    print()

    # Avg win/loss
    for s in strategies:
        d = data_map[s]
        print(f"  {'平均盈利':<22} ", end="")
        print(f"${d['avg_win']:<+17,.2f} ", end="")
    print()

    for s in strategies:
        d = data_map[s]
        print(f"  {'平均亏损':<22} ", end="")
        print(f"${d['avg_loss']:<+17,.2f} ", end="")
    print()

    # Best/worst trade
    for s in strategies:
        d = data_map[s]
        print(f"  {'最佳单笔盈利':<22} ", end="")
        print(f"${d['max_win']:<+16,.2f} ", end="")
    print()

    for s in strategies:
        d = data_map[s]
        print(f"  {'最差单笔亏损':<22} ", end="")
        print(f"${d['max_loss']:<+16,.2f} ", end="")
    print()

    # Consecutive losses
    for s in strategies:
        d = data_map[s]
        print(f"  {'最大连续亏损':<22} ", end="")
        print(f"{d['max_consecutive_losses']:<20} ", end="")
    print()

    # Sharpe ratio
    for s in strategies:
        d = data_map[s]
        print(f"  {'夏普比率(年化)':<22} ", end="")
        print(f"{d['sharpe_ratio']:<20} ", end="")
    print()

    # Equity milestones
    print()
    print(f"  {'='*68}")
    print(f"  {'权益曲线里程碑':^66}")
    print(f"  {'='*68}")
    print(f"  {'交易数':<12} {'AI方向':<18} {'随机方向':<18} {'纯多头':<18}")
    print(f"  {'-'*12} {'-'*18} {'-'*18} {'-'*18}")
    for m in [0, 250, 500, 750, 1000]:
        ai_eq = results["AI"]["equity_curve_milestones"].get(m, "N/A")
        rand_eq = results["Random"]["equity_curve_milestones"].get(m, "N/A")
        long_eq = results["PureLong"]["equity_curve_milestones"].get(m, "N/A")
        print(f"  {m:<12} ${ai_eq:<14} ${rand_eq:<14} ${long_eq:<14}")


def assess_ai_performance(results, pair_name):
    """Assess whether the AI predictor adds value over random/baseline."""
    ai = results["AI"]
    rand = results["Random"]
    pl = results["PureLong"]

    assessments = []

    # Compare AI vs Random
    ai_vs_rand_return = ai["return_pct"] - rand["return_pct"]
    ai_vs_rand_wr = ai["win_rate"] - rand["win_rate"]
    ai_vs_rand_pf = ai["profit_factor"]
    rand_pf = rand["profit_factor"]
    if isinstance(ai_vs_rand_pf, str):
        ai_vs_rand_pf = float("inf")
    if isinstance(rand_pf, str):
        rand_pf = float("inf")

    # Compare AI vs PureLong
    ai_vs_long_return = ai["return_pct"] - pl["return_pct"]
    ai_vs_long_wr = ai["win_rate"] - pl["win_rate"]

    assessments.append({
        "pair": pair_name,
        "ai_return": ai["return_pct"],
        "random_return": rand["return_pct"],
        "purelong_return": pl["return_pct"],
        "ai_vs_random_return_diff": round(ai_vs_rand_return, 2),
        "ai_vs_long_return_diff": round(ai_vs_long_return, 2),
        "ai_vs_random_winrate_diff": round(ai_vs_rand_wr, 1),
        "ai_vs_long_winrate_diff": round(ai_vs_long_wr, 1),
        "ai_win_rate": ai["win_rate"],
        "ai_max_dd": ai["max_dd_pct"],
        "ai_profit_factor": ai["profit_factor"],
        "ai_sharpe": ai["sharpe_ratio"],
        "ai_trades": ai["total_trades"],
        "ai_liquidated": ai["liquidated"],
    })

    # Overall verdict
    if ai["liquidated"]:
        verdict = "CRITICAL: AI策略爆仓，不可用于实盘交易"
    elif ai["return_pct"] < 0:
        verdict = "WARNING: AI策略亏损，当前表现不如无风险收益"
    elif ai_vs_rand_return > 0 and ai_vs_long_return > 0:
        verdict = "POSITIVE: AI策略同时跑赢随机方向和纯多头策略"
    elif ai_vs_rand_return > 0:
        verdict = "MIXED: AI策略跑赢随机方向但未跑赢纯多头"
    elif ai_vs_long_return > 0:
        verdict = "MIXED: AI策略跑赢纯多头但未跑赢随机方向"
    else:
        verdict = "NEGATIVE: AI策略表现不如基准策略"

    assessments.append({"verdict": verdict})
    return assessments


def print_ai_assessment(assessments):
    """Print AI performance assessment."""
    print_separator()
    print(f"  AI 性能评估")
    print_separator()

    for a in assessments:
        if "verdict" in a:
            print(f"\n  >>> 综合判定: {a['verdict']}")
            continue
        print(f"\n  --- {a['pair']} ---")
        print(f"  AI收益率:     {a['ai_return']:+.2f}%")
        print(f"  随机方向:     {a['random_return']:+.2f}%")
        print(f"  纯多头:       {a['purelong_return']:+.2f}%")
        print(f"  AI vs 随机:   {a['ai_vs_random_return_diff']:+.2f}%")
        print(f"  AI vs 纯多头: {a['ai_vs_long_return_diff']:+.2f}%")
        print(f"  AI胜率:       {a['ai_win_rate']:.1f}%")
        print(f"  AI最大回撤:   {a['ai_max_dd']:.2f}%")
        print(f"  AI盈亏比:     {a['ai_profit_factor']}")
        print(f"  AI夏普比率:   {a['ai_sharpe']}")
        print(f"  AI交易数:     {a['ai_trades']}")
        print(f"  AI爆仓:       {'是' if a['ai_liquidated'] else '否'}")


def print_risk_assessment(results):
    """Print risk assessment for the AI strategy."""
    print_separator()
    print(f"  风险评估")
    print_separator()

    for pair_name in ["BTC/USDT:USDT", "ETH/USDT:USDT"]:
        pair_key = "BTC" if "BTC" in pair_name else "ETH"
        ai = results.get(pair_name, results.get(pair_key, {}))
        if isinstance(ai, dict) and "AI" in ai:
            ai = ai["AI"]
        elif isinstance(results, dict) and pair_key in results:
            ai = results[pair_key]["AI"]
        else:
            continue

        print(f"\n  --- {pair_name} ---")
        print(f"  最大回撤:     {ai['max_dd_pct']:.2f}%")
        print(f"  最大连续亏损: {ai['max_consecutive_losses']}笔")
        print(f"  爆仓风险:     {'是 (已爆仓)' if ai['liquidated'] else '否 (存活)'}")
        print(f"  夏普比率:     {ai['sharpe_ratio']}")
        print(f"  盈亏比:       {ai['profit_factor']}")

        # Recovery assessment
        max_dd = ai["max_dd_pct"]
        if max_dd > 50:
            recovery = "极差: 回撤超过50%, 恢复极其困难"
        elif max_dd > 30:
            recovery = "差: 回撤超过30%, 需要较大盈利方可恢复"
        elif max_dd > 15:
            recovery = "中等: 回撤15-30%, 需谨慎监控"
        elif max_dd > 5:
            recovery = "良好: 回撤可控, 可接受范围"
        else:
            recovery = "优秀: 回撤极小"
        print(f"  恢复能力评估: {recovery}")

        # Required gain to recover
        if max_dd > 0:
            required_gain = (100 / (100 - max_dd) - 1) * 100
            print(f"  恢复所需盈利: {required_gain:.1f}% (当前回撤{max_dd:.2f}%)")


def generate_recommendations(results):
    """Generate trading recommendations based on test results."""
    recs = []

    for pair_name in ["BTC/USDT:USDT", "ETH/USDT:USDT"]:
        pair_key = "BTC" if "BTC" in pair_name else "ETH"
        data = results.get(pair_name, results.get(pair_key, None))
        if data is None:
            continue

        ai = data["AI"]
        rand = data["Random"]
        pl = data["PureLong"]

        rec = {"pair": pair_name}

        # Safety first
        if ai["liquidated"]:
            rec["trading_verdict"] = "不推荐实盘交易"
            rec["reason"] = "AI策略在回测中爆仓, 风险不可控"
        elif ai["max_dd_pct"] > 40:
            rec["trading_verdict"] = "高风险, 不建议实盘"
            rec["reason"] = f"最大回撤{ai['max_dd_pct']:.2f}%过高, 风险收益比不佳"
        elif ai["return_pct"] < 0:
            rec["trading_verdict"] = "不推荐使用"
            rec["reason"] = "策略亏损, 不如持有稳定币"
        elif ai["return_pct"] > rand["return_pct"] and ai["return_pct"] > pl["return_pct"]:
            rec["trading_verdict"] = "可以谨慎实盘"
            rec["reason"] = "AI策略跑赢所有基准, 且回撤可控"
        elif ai["return_pct"] > 0 and ai["profit_factor"] > 1.2:
            rec["trading_verdict"] = "可以小额试盘"
            rec["reason"] = f"策略盈利但未全面跑赢基准, 盈亏比{ai['profit_factor']}"
        else:
            rec["trading_verdict"] = "不建议实盘"
            rec["reason"] = f"策略表现未达基准或风险过高"

        recs.append(rec)
    return recs


def print_recommendations(recs):
    """Print recommendations."""
    print_separator()
    print(f"  建议与总结")
    print_separator()
    for r in recs:
        print(f"\n  {r['pair']}:")
        print(f"    交易判定: {r['trading_verdict']}")
        print(f"    理由:     {r['reason']}")


def generate_json_report(all_results, ai_assessments, recs):
    """Save JSON report."""
    report = {
        "test_metadata": {
            "timestamp": datetime.now().isoformat(),
            "initial_equity": INITIAL_EQUITY,
            "leverage": LEVERAGE,
            "max_trades": MAX_TRADES,
            "entry_threshold": ENTRY_THRESHOLD,
            "min_confidence": MIN_CONFIDENCE,
            "warmup_candles": WARMUP,
            "data_source": "OKX 4h futures",
            "data_range": "2025-12-31 to 2026-06-28",
        },
        "results": all_results,
        "ai_assessment": ai_assessments,
        "recommendations": recs,
    }

    report_path = Path("../ethereum-ai-trader/test_report_ai_comprehensive.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n  JSON报告已保存: {report_path}")
    return report_path


# =====================================================================
# Main
# =====================================================================
def main():
    t_start = time.time()
    print_separator("=")
    print(f"  AI方向预测器 — 综合测试报告")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  配置:")
    print(f"    数据: OKX 4h永续合约期货 (1074根K线)")
    print(f"    初始资金: ${INITIAL_EQUITY:,} USDT")
    print(f"    杠杆: {LEVERAGE}x")
    print(f"    最大交易数: {MAX_TRADES}")
    print(f"    入场阈值: ±{ENTRY_THRESHOLD} (预期收益)")
    print(f"    最低置信度: {MIN_CONFIDENCE}")
    print(f"    预热K线: {WARMUP}")
    print(f"    清算线: {LIQUIDATION_THRESHOLD*100:.0f}% 反向波动")
    print_separator("=")

    # ================================================================
    # Test A: BTC
    # ================================================================
    print(f"\n{'='*72}")
    print(f"  Test A: BTC/USDT:USDT (OKX 永续合约, 5x, $1,000)")
    print(f"{'='*72}")
    btc_results = run_single_pair("BTC_USDT_USDT", "BTC/USDT:USDT")

    # ================================================================
    # Test B: ETH
    # ================================================================
    print(f"\n{'='*72}")
    print(f"  Test B: ETH/USDT:USDT (OKX 永续合约, 5x, $1,000)")
    print(f"{'='*72}")
    eth_results = run_single_pair("ETH_USDT_USDT", "ETH/USDT:USDT")

    # ================================================================
    # Print Results
    # ================================================================
    print(f"\n{'='*72}")
    print(f"  TEST RESULTS SUMMARY")
    print(f"{'='*72}")

    print_results(btc_results, "BTC/USDT:USDT")
    print()
    print_results(eth_results, "ETH/USDT:USDT")

    # ================================================================
    # Comparison Table
    # ================================================================
    print(f"\n{'='*72}")
    print(f"  综合对比表")
    print(f"{'='*72}")
    print(f"  {'指标':<22} {'BTC-AI':<16} {'BTC-随机':<14} {'BTC-纯多':<14}", end="")
    print(f" {'ETH-AI':<14} {'ETH-随机':<14} {'ETH-纯多':<14}")
    print(f"  {'-'*22} {'-'*16} {'-'*14} {'-'*14} {'-'*14} {'-'*14} {'-'*14}")

    metrics = [
        ("最终资产", "final", "${:>10,.2f}", True, True),
        ("总收益率", "return_pct", "{:>+9.2f}%", True, False),
        ("爆仓", "liquidated", "{}", True, False),
        ("最大回撤", "max_dd_pct", "{:>9.2f}%", True, False),
        ("胜率", "win_rate", "{:>8.1f}%", True, False),
        ("盈亏比", "profit_factor", "{:>10}", True, False),
        ("夏普比率", "sharpe_ratio", "{:>9.2f}", True, False),
        ("最大连续亏损", "max_consecutive_losses", "{:>8}", True, False),
        ("做多/做空", None, None, False, True),
        ("总交易数", "total_trades", "{:>9}", True, False),
    ]

    for label, key, fmt, use_fmt, is_special in metrics:
        if is_special and label == "做多/做空":
            print(f"  {'做多/做空':<22} "
                  f"{btc_results['AI']['longs']}/{btc_results['AI']['shorts']:<12} "
                  f"{btc_results['Random']['longs']}/{btc_results['Random']['shorts']:<10} "
                  f"{btc_results['PureLong']['longs']}/{btc_results['PureLong']['shorts']:<10} "
                  f"{eth_results['AI']['longs']}/{eth_results['AI']['shorts']:<10} "
                  f"{eth_results['Random']['longs']}/{eth_results['Random']['shorts']:<10} "
                  f"{eth_results['PureLong']['longs']}/{eth_results['PureLong']['shorts']:<10}")
            continue

        if use_fmt:
            btc_ai = fmt.format(btc_results["AI"][key]) if key else ""
            btc_rd = fmt.format(btc_results["Random"][key]) if key else ""
            btc_pl = fmt.format(btc_results["PureLong"][key]) if key else ""
            eth_ai = fmt.format(eth_results["AI"][key]) if key else ""
            eth_rd = fmt.format(eth_results["Random"][key]) if key else ""
            eth_pl = fmt.format(eth_results["PureLong"][key]) if key else ""

            # Special handling for boolean
            if key == "liquidated":
                btc_ai = "是" if btc_results["AI"][key] else "否"
                btc_rd = "是" if btc_results["Random"][key] else "否"
                btc_pl = "是" if btc_results["PureLong"][key] else "否"
                eth_ai = "是" if eth_results["AI"][key] else "否"
                eth_rd = "是" if eth_results["Random"][key] else "否"
                eth_pl = "是" if eth_results["PureLong"][key] else "否"

            print(f"  {label:<22} {btc_ai:<16} {btc_rd:<14} {btc_pl:<14} {eth_ai:<14} {eth_rd:<14} {eth_pl:<14}")

    # ================================================================
    # AI Performance Assessment
    # ================================================================
    btc_assess = assess_ai_performance(btc_results, "BTC/USDT:USDT")
    eth_assess = assess_ai_performance(eth_results, "ETH/USDT:USDT")
    all_assessments = btc_assess + eth_assess
    print_ai_assessment(all_assessments)

    # ================================================================
    # Risk Assessment
    # ================================================================
    print(f"\n")
    print_risk_assessment({"BTC": btc_results, "ETH": eth_results})

    # ================================================================
    # Recommendations
    # ================================================================
    recs = generate_recommendations({"BTC": btc_results, "ETH": eth_results})
    print(f"\n")
    print_recommendations(recs)

    # ================================================================
    # Execution Summary
    # ================================================================
    total_time = time.time() - t_start
    print(f"\n{'='*72}")
    print(f"  执行摘要")
    print(f"{'='*72}")
    print(f"  总耗时: {total_time:.1f}秒")
    print(f"  测试对: BTC/USDT:USDT, ETH/USDT:USDT")
    print(f"  策略数: 3 (AI方向预测, 随机方向, 纯多头)")
    print(f"  数据: OKX 4h永续合约, 1074根K线")
    print(f"  日期范围: 2025-12-31 至 2026-06-28")
    print(f"{'='*72}")

    # Save JSON
    all_results = {
        "BTC/USDT:USDT": btc_results,
        "ETH/USDT:USDT": eth_results,
    }
    json_path = generate_json_report(all_results, all_assessments, recs)

    print(f"\n  测试完成.")

    # Return results for analysis
    return all_results


if __name__ == "__main__":
    main()
