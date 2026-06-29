"""AI direction predictor vs random — 5x leverage real data test."""
import sys; sys.path.insert(0, '.')
import numpy as np; import pandas as pd
from pathlib import Path

MD = '../ethereum-ai-trader/models'

def run_comparison(pair_safe, pair_name, initial=1000, leverage=5):
    df = pd.read_feather(f'user_data/data/okx/{pair_safe}-4h-futures.feather')
    df['date'] = pd.to_datetime(df['date']); df = df.sort_values('date')

    # ---- Load AI models ----
    from freqtrade.ai.features import FeatureEngineer
    from freqtrade.ai.direction_predictor import DirectionPredictor
    from freqtrade.ai.regime_classifier import RegimeClassifier

    fe = FeatureEngineer()
    features = fe.compute_price_features(df)

    rc = RegimeClassifier(model_dir=MD)
    dp = DirectionPredictor(model_dir=MD)
    try:
        rc.load(); dp.load()
    except:
        rc.train(features); dp.train(features)

    # Get AI predictions for ALL candles
    ai_preds = dp.predict(features)
    regime_preds = rc.predict(features)

    # ---- Run AI-directed test ----
    eq_ai = initial; peak_ai = initial; dd_ai = 0.0
    trades_ai = []; liq_ai = False

    # ---- Run random-direction test (same data, same candles) ----
    eq_rand = initial; peak_rand = initial; dd_rand = 0.0
    trades_rand = []; liq_rand = False

    for i in range(50, len(df) - 1):  # Skip warmup
        o, c, h, l = map(float, [df['open'].iloc[i], df['close'].iloc[i], df['high'].iloc[i], df['low'].iloc[i]])

        # === AI DIRECTION ===
        if ai_preds and ai_preds[i] and ai_preds[i]['confidence'] >= 0.50:
            ai_long = ai_preds[i]['expected_return'] > 0
        else:
            ai_long = None  # Skip uncertain

        # === RANDOM DIRECTION ===
        rand_long = (i % 2 == 0)  # Alternating

        # Execute AI trade
        if ai_long is not None and eq_ai > 10:
            pnl_pct = ((c/o - 1) if ai_long else (1 - c/o)) * leverage
            pnl = eq_ai * pnl_pct
            liq_p = o * (1 - 1.0/leverage) if ai_long else o * (1 + 1.0/leverage)
            liq_hit = (ai_long and l <= liq_p) or (not ai_long and h >= liq_p)
            if liq_hit:
                pnl = -eq_ai; liq_ai = True
                trades_ai.append({'pnl': pnl, 'liq': True})
                eq_ai += pnl
            else:
                eq_ai += pnl
                trades_ai.append({'pnl': pnl, 'liq': False})
                if eq_ai > peak_ai: peak_ai = eq_ai
                dd = (peak_ai - eq_ai) / peak_ai if peak_ai > 0 else 0
                if dd > dd_ai: dd_ai = dd

        # Execute random trade
        if eq_rand > 10:
            pnl_pct_r = ((c/o - 1) if rand_long else (1 - c/o)) * leverage
            pnl_r = eq_rand * pnl_pct_r
            liq_pr = o * (1 - 1.0/leverage) if rand_long else o * (1 + 1.0/leverage)
            liq_hit_r = (rand_long and l <= liq_pr) or (not rand_long and h >= liq_pr)
            if liq_hit_r:
                pnl_r = -eq_rand; liq_rand = True
                trades_rand.append({'pnl': pnl_r, 'liq': True})
                eq_rand += pnl_r
            else:
                eq_rand += pnl_r
                trades_rand.append({'pnl': pnl_r, 'liq': False})
                if eq_rand > peak_rand: peak_rand = eq_rand
                dd = (peak_rand - eq_rand) / peak_rand if peak_rand > 0 else 0
                if dd > dd_rand: dd_rand = dd

        if liq_ai or liq_rand: break
        if len(trades_ai) >= 200: break

    def stats(name, eq, peak, dd, trades, liq):
        n = len(trades); wins = [t for t in trades if t['pnl'] > 0]
        losses = [t for t in trades if t['pnl'] <= 0]
        return {
            'name': name, 'final': eq, 'return_pct': (eq/initial-1)*100,
            'liquidated': liq, 'max_dd': dd*100, 'trades': n,
            'wins': len(wins), 'losses': len(losses),
            'win_rate': len(wins)/n*100 if n else 0,
            'avg_win': np.mean([t['pnl'] for t in wins]) if wins else 0,
            'avg_loss': np.mean([t['pnl'] for t in losses]) if losses else 0,
            'max_win': max(t['pnl'] for t in trades) if trades else 0,
            'max_loss': min(t['pnl'] for t in trades) if trades else 0,
        }

    return (
        stats('AI方向', eq_ai, peak_ai, dd_ai, trades_ai, liq_ai),
        stats('随机方向', eq_rand, peak_rand, dd_rand, trades_rand, liq_rand),
    )

# Run comparison
for pair_safe, pair_name in [('BTC_USDT_USDT', 'BTC/USDT'), ('ETH_USDT_USDT', 'ETH/USDT')]:
    ai, rand = run_comparison(pair_safe, pair_name)

    print(f"\n{'='*55}")
    print(f"  {pair_name} — AI方向 vs 随机方向 (5x, 1000 USDT)")
    print(f"{'='*55}")

    for r in [ai, rand]:
        icon = 'AI' if 'AI' in r['name'] else 'RD'
        liq_str = '爆仓' if r['liquidated'] else '存活'
        print(f"\n  [{icon}] {r['name']}: $1,000 -> ${r['final']:,.2f} ({r['return_pct']:+.1f}%) | {liq_str}")
        print(f"       交易{r['trades']}笔 | 胜率{r['win_rate']:.0f}% | 回撤{r['max_dd']:.1f}%")
        print(f"       均盈+${r['avg_win']:,.2f} | 均亏-${abs(r['avg_loss']):,.2f} | 最大+${r['max_win']:,.0f} / ${r['max_loss']:,.0f}")

    # Compare
    winner = 'AI' if ai['return_pct'] > rand['return_pct'] else '随机'
    diff = abs(ai['return_pct'] - rand['return_pct'])
    print(f"\n  >>> {winner}胜出, 差距 {diff:.1f}% | AI胜率{ai['win_rate']:.0f}% vs 随机{rand['win_rate']:.0f}%")
