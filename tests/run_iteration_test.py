"""Iteration test runner — saves reports to ethereum-ai-trader/reports/"""
import sys; sys.path.insert(0, '.')
import json, numpy as np, pandas as pd
from datetime import datetime
from pathlib import Path

MD = '../ethereum-ai-trader/models'
REPORTS = '../ethereum-ai-trader/reports'

def run_test(pair_safe, pair_name, lev, pos, sl, conf, n_max=200):
    df = pd.read_feather(f'user_data/data/okx/{pair_safe}-4h-futures.feather')
    df['date'] = pd.to_datetime(df['date']); df = df.sort_values('date')

    from engine.features import FeatureEngineer
    from engine.direction_predictor import DirectionPredictor
    fe = FeatureEngineer(); features = fe.compute_price_features(df)
    dp = DirectionPredictor(model_dir=MD)
    try: dp.load()
    except: dp.train(features)
    preds = dp.predict(features)

    eq = 1000.0; peak = 1000.0; dd = 0.0; trades = []; liq = False
    long_t = short_t = long_w = short_w = 0

    for i in range(50, min(len(df)-1, len(features)-1)):
        o, c, h, l = map(float, [df['open'].iloc[i], df['close'].iloc[i], df['high'].iloc[i], df['low'].iloc[i]])
        if not preds or not preds[i] or preds[i]['confidence'] < conf: continue
        er = preds[i]['expected_return']
        if abs(er) < 0.002: continue

        is_long = er > 0
        pnl = eq * pos * (((c/o - 1) if is_long else (1 - c/o)) * lev)
        sl_price = o * (1 - sl/lev) if is_long else o * (1 + sl/lev)
        liq_hit = (is_long and l <= sl_price) or (not is_long and h >= sl_price)
        if liq_hit: pnl = -eq * pos * sl
        pnl = max(pnl, -eq * pos * sl)
        eq += pnl

        if is_long: long_t += 1
        else: short_t += 1
        if pnl > 0:
            if is_long: long_w += 1
            else: short_w += 1
        trades.append(pnl)
        if eq > peak: peak = eq
        dd = max(dd, (peak-eq)/peak if peak > 0 else 0)
        if eq <= 10: liq = True; break
        if len(trades) >= n_max: break

    n = len(trades); wins = [t for t in trades if t > 0]; losses = [t for t in trades if t <= 0]
    return {
        'pair': pair_name, 'lev': lev, 'pos': pos, 'sl': sl, 'conf': conf,
        'final': round(eq, 2), 'return_pct': round((eq/1000-1)*100, 1),
        'max_dd_pct': round(dd*100, 1), 'liq': liq, 'trades': n,
        'wins': len(wins), 'losses': len(losses),
        'win_rate': round(len(wins)/n*100, 1) if n else 0,
        'avg_win': round(np.mean(wins), 2) if wins else 0,
        'avg_loss': round(np.mean(losses), 2) if losses else 0,
        'long_trades': long_t, 'short_trades': short_t,
        'long_wr': round(long_w/long_t*100,1) if long_t else 0,
        'short_wr': round(short_w/short_t*100,1) if short_t else 0,
        'sharpe': round((np.mean([t/1000 for t in trades])/max(np.std([t/1000 for t in trades]),1e-10)*np.sqrt(365*6)),2) if n>5 else 0,
    }

# Run 6 configs
configs = [('Conservative', 2, 0.20, 0.05, 0.55), ('Balanced', 3, 0.20, 0.05, 0.55), ('Aggressive', 3, 0.30, 0.08, 0.55)]
results = []

print('='*70)
print('  Iteration Test #1 — Risk-Controlled AI Predictor')
print('='*70)

for ps, pn in [('BTC_USDT_USDT','BTC/USDT'), ('ETH_USDT_USDT','ETH/USDT')]:
    print(f'\n{pn}:')
    for cn, lev, pos, sl, conf in configs:
        r = run_test(ps, pn, lev, pos, sl, conf, n_max=200)
        results.append(r)
        st = 'LIQ!' if r['liq'] else 'OK'
        print(f'  {cn:14s}: ${r["final"]:>7,.0f} ({r["return_pct"]:>+7.1f}%) DD={r["max_dd_pct"]:>5.1f}% WR={r["win_rate"]:>5.1f}% T={r["trades"]:>3d} {st}')

best_btc = max([r for r in results if 'BTC' in r['pair'] and not r['liq']], key=lambda r: r['return_pct'])
best_eth = max([r for r in results if 'ETH' in r['pair'] and not r['liq']], key=lambda r: r['return_pct'])
print(f'\nBest BTC: lev={best_btc["lev"]} pos={best_btc["pos"]} -> {best_btc["return_pct"]:+.1f}% DD={best_btc["max_dd_pct"]:.1f}%')
print(f'Best ETH: lev={best_eth["lev"]} pos={best_eth["pos"]} -> {best_eth["return_pct"]:+.1f}% DD={best_eth["max_dd_pct"]:.1f}%')

report = {'timestamp': datetime.now().isoformat(), 'iteration': 1, 'test_count': len(results), 'results': results, 'best_btc': best_btc, 'best_eth': best_eth}
ts = datetime.now().strftime('%Y%m%d_%H%M%S')
Path(REPORTS).mkdir(parents=True, exist_ok=True)
with open(f'{REPORTS}/iter001_{ts}.json', 'w') as f:
    json.dump(report, f, indent=2, default=str)
print(f'\nReport: reports/iter001_{ts}.json')
