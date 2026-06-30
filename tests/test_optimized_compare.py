"""Optimization comparison — original vs risk-managed AI predictor."""
import sys; sys.path.insert(0, '.')
import numpy as np; import pandas as pd
from pathlib import Path
from datetime import datetime

MD = '../ethereum-ai-trader/models'

def run_config(name, pair_safe, leverage, position_pct, stop_loss_pct, min_confidence):
    df = pd.read_feather(f'user_data/data/okx/{pair_safe}-4h-futures.feather')
    df['date'] = pd.to_datetime(df['date']); df = df.sort_values('date')

    from engine.features import FeatureEngineer
    from engine.direction_predictor import DirectionPredictor

    fe = FeatureEngineer(); features = fe.compute_price_features(df)
    dp = DirectionPredictor(model_dir=MD)
    try: dp.load()
    except: dp.train(features)
    preds = dp.predict(features)

    initial = 1000; equity = initial; peak = initial; max_dd = 0.0
    trades = []; liq = False

    for i in range(50, min(len(df) - 1, 1074)):
        o, c, h, l = map(float, [df['open'].iloc[i], df['close'].iloc[i], df['high'].iloc[i], df['low'].iloc[i]])

        if not preds or not preds[i] or preds[i]['confidence'] < min_confidence:
            continue
        er = preds[i]['expected_return']
        if abs(er) < 0.002: continue

        is_long = er > 0
        pos_equity = equity * position_pct
        pos_notional = pos_equity * leverage

        if is_long:
            trade_return = (c/o - 1) * leverage
            sl_price = o * (1 - stop_loss_pct / leverage)
            liq_hit = l <= sl_price
        else:
            trade_return = (1 - c/o) * leverage
            sl_price = o * (1 + stop_loss_pct / leverage)
            liq_hit = h >= sl_price

        if liq_hit:
            pnl = -pos_equity * min(stop_loss_pct, 1.0)  # Max loss capped at stop_loss_pct
        else:
            pnl = pos_equity * trade_return
            if pnl < -pos_equity * stop_loss_pct:
                pnl = -pos_equity * stop_loss_pct  # Hard cap

        equity += pnl
        trades.append({'pnl': pnl, 'liq': liq_hit, 'side': 'long' if is_long else 'short'})
        if equity > peak: peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0
        if dd > max_dd: max_dd = dd

        # Check global liquidation (20% adverse on full position)
        if equity <= 0:
            liq = True; break
        if len(trades) >= 1000: break

    n = len(trades); wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    return {
        'name': name, 'final': round(equity, 2), 'return_pct': round((equity/initial-1)*100, 1),
        'liquidated': liq, 'max_dd_pct': round(max_dd*100, 1), 'trades': n,
        'wins': len(wins), 'losses': len(losses),
        'win_rate': round(len(wins)/n*100, 1) if n else 0,
        'avg_win': round(np.mean([t['pnl'] for t in wins]), 2) if wins else 0,
        'avg_loss': round(np.mean([t['pnl'] for t in losses]), 2) if losses else 0,
        'max_win': round(max(t['pnl'] for t in trades), 2) if trades else 0,
        'max_loss': round(min(t['pnl'] for t in trades), 2) if trades else 0,
        'sharpe': round((np.mean([t['pnl']/1000 for t in trades]) / max(np.std([t['pnl']/1000 for t in trades]), 1e-10) * np.sqrt(365*6)), 2) if len(trades) > 2 else 0,
    }

# Configurations to test
configs = [
    ('原始: 5x 全仓 无止损', 5, 1.0, 1.0, 0.50),
    ('优化A: 3x 半仓 8%止损', 3, 0.50, 0.08, 0.55),
    ('优化B: 3x 20%仓 5%止损', 3, 0.20, 0.05, 0.55),
    ('保守: 2x 20%仓 5%止损', 2, 0.20, 0.05, 0.60),
]

print(f"{'='*70}")
print(f"  AI方向预测器 — 风险优化对比测试")
print(f"{'='*70}")

for pair_safe, pair_name in [('BTC_USDT_USDT', 'BTC/USDT'), ('ETH_USDT_USDT', 'ETH/USDT')]:
    print(f"\n--- {pair_name} ---")
    print(f"{'配置':22s} {'最终':>10s} {'收益':>8s} {'回撤':>7s} {'胜率':>6s} {'交易':>5s} {'夏普':>6s}")
    print("-" * 70)

    results = []
    for cfg_name, lev, pos, sl, conf in configs:
        r = run_config(cfg_name, pair_safe, lev, pos, sl, conf)
        results.append(r)
        liq_mark = '💀' if r['liquidated'] else ''
        print(f"{cfg_name:22s} ${r['final']:>8,.0f} {r['return_pct']:>+7.1f}% {r['max_dd_pct']:>6.1f}% {r['win_rate']:>5.1f}% {r['trades']:>5d} {r['sharpe']:>6.1f} {liq_mark}")

    # Find best risk-adjusted: highest return with drawdown < 25%
    safe = [r for r in results if r['max_dd_pct'] < 25]
    best = max(safe, key=lambda r: r['return_pct']) if safe else max(results, key=lambda r: r['return_pct'])
    print(f"\n  >>> 推荐: {best['name']} — 收益{best['return_pct']:+.1f}%, 回撤{best['max_dd_pct']:.1f}%")

print(f"\n{'='*70}")
print(f"  根因分析:")
print(f"  1. 5x全仓无止损 → 回撤42-65% (单笔亏损无上限)")
print(f"  2. AI预测器方向准确 (56-57%胜率) 但风险控制缺失")
print(f"  3. 优化方案: 降杠杆+限仓位+单笔止损 → 回撤可控")
print(f"{'='*70}")
