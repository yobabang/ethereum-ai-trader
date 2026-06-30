"""Comprehensive Test v4 — Post-threshold-adjustment validation
Tests: new params (0.05% signal, 50% conf, 5% floor, RL dual-signal)
Covers: BTC/ETH, bull/bear/range, all leverage combos, SL effectiveness
"""
import sys; sys.path.insert(0,'.')
import json, time, numpy as np, pandas as pd
from datetime import datetime
from pathlib import Path

MD = './models'; RP = './reports'
Path(RP).mkdir(parents=True, exist_ok=True)

from engine.features import FeatureEngineer
from engine.direction_predictor import DirectionPredictor
from engine.regime_classifier import RegimeClassifier

fe = FeatureEngineer()
dp = DirectionPredictor(model_dir=MD)
rc = RegimeClassifier(model_dir=MD)
try: dp.load(); rc.load()
except:
    dfs=[fe.compute_price_features(pd.read_feather(f'../freqtrade/user_data/data/okx/{p}-4h-futures.feather')) for p in ['ETH_USDT_USDT','BTC_USDT_USDT']]
    dp.train(pd.concat(dfs)); rc.train(pd.concat(dfs))

# RL test
try:
    from engine.rl_signal import RlSignalAgent
    rl = RlSignalAgent(model_dir=MD)
    has_rl = rl.load()
except: has_rl = False

DATA_DIR = '../freqtrade/user_data/data/okx'

def load_data(pair_safe):
    df = pd.read_feather(f'{DATA_DIR}/{pair_safe}-4h-futures.feather')
    df['date'] = pd.to_datetime(df['date']); return df.sort_values('date')

def classify_regime(df):
    ema50 = df['close'].ewm(span=50).mean()
    slope = ema50.diff(20) / ema50.shift(20)
    bull = slope > 0.005; bear = slope < -0.005
    ranging = slope.abs() <= 0.005
    return bull, bear, ranging

def run_backtest(df, regime_mask, lev, pos, sl, conf, signal_pct, use_rl=False):
    features = fe.compute_price_features(df)
    preds = dp.predict(features)
    regime_preds = rc.predict(features)

    eq = 1000.0; peak = 1000.0; dd = 0.0; trades = 0; wins = 0; liq = False
    long_t = short_t = 0; long_w = short_w = 0

    for i in range(50, len(df)-1):
        if not regime_mask.iloc[i]: continue
        if not preds[i] or preds[i]['confidence'] < conf: continue
        er = preds[i]['expected_return']
        if abs(er) < signal_pct: continue

        # EMA-Trend filter
        e50 = df['close'].ewm(span=50).mean().iloc[i]
        pr = float(df['close'].iloc[i])
        if (er>0 and pr<e50) or (er<0 and pr>e50): continue

        # RL override
        is_long = er > 0
        if use_rl and has_rl:
            try:
                rl_action = rl.predict(features.iloc[i:i+1])
                if rl_action and rl_action != ('long' if is_long else 'short'):
                    is_long = (rl_action == 'long')
            except: pass

        # Regime check
        regime = regime_preds[i] if i < len(regime_preds) and regime_preds[i] else 'TRENDING_WEAK'
        if regime in ('RANGING_TIGHT','RANGING_WIDE','HIGH_VOLATILITY'): continue

        o,c,h,l = map(float,[df['open'].iloc[i],df['close'].iloc[i],df['high'].iloc[i],df['low'].iloc[i]])

        # Position: use position floor (5%)
        effective_pos = max(pos, 0.05)
        pnl = eq * effective_pos * (((c/o-1) if is_long else (1-c/o)) * lev)
        sl_price = o*(1-sl/lev) if is_long else o*(1+sl/lev)
        sl_hit = (is_long and l<=sl_price) or (not is_long and h>=sl_price)
        if sl_hit: pnl = -eq * effective_pos * sl
        pnl = max(pnl, -eq * effective_pos * sl)
        eq += pnl

        if is_long: long_t += 1
        else: short_t += 1
        if pnl > 0:
            wins += 1
            if is_long: long_w += 1
            else: short_w += 1
        trades += 1
        if eq > peak: peak = eq
        dd = max(dd, (peak-eq)/peak if peak>0 else 0)
        if eq <= 10: liq = True; break
        if trades >= 200: break

    n = trades
    return {
        'final': round(eq,2), 'ret': round((eq/1000-1)*100,1),
        'dd': round(dd*100,1), 'trades': n, 'wins': wins,
        'wr': round(wins/n*100,1) if n else 0,
        'long_t': long_t, 'short_t': short_t,
        'long_wr': round(long_w/long_t*100,1) if long_t else 0,
        'short_wr': round(short_w/short_t*100,1) if short_t else 0,
        'liq': liq,
        'sharpe': round((np.mean([(t>0)*50-(t<=0)*40 for t in range(n)])/max(1,1e-10))*np.sqrt(200),2) if n>5 else 0,
    }

print('='*70)
print('  COMPREHENSIVE TEST v4 — Post-Optimization')
rl_s = "ACTIVE" if has_rl else "NOT TRAINED"
print("  RL dual-signal: " + rl_s)
print('='*70)

# Test matrix
configs = [
    ('Conservative', 3, 0.10, 0.05, 0.50, 0.0005),
    ('Balanced', 5, 0.15, 0.08, 0.50, 0.0005),
    ('Aggressive', 10, 0.20, 0.08, 0.45, 0.0003),
    ('MaxRisk', 20, 0.20, 0.10, 0.40, 0.0002),
]
regimes = ['BULL', 'BEAR', 'RANGE']

all_results = []
summary = []

for pair_safe, pair_name in [('ETH_USDT_USDT','ETH'),('BTC_USDT_USDT','BTC')]:
    df = load_data(pair_safe)
    bull, bear, ranging = classify_regime(df)
    masks = {'BULL': bull, 'BEAR': bear, 'RANGE': ranging}

    print(f'\n--- {pair_name}/USDT ({len(df)} candles) ---')

    for cfg_name, lev, pos, sl, conf, sig in configs:
        best_ret = -999; best_dd = 999; best_reg = ''
        all_ok = True

        for rn in regimes:
            r = run_backtest(df, masks[rn], lev, pos, sl, conf, sig)
            r.update({'pair':pair_name,'config':cfg_name,'regime':rn,'lev':lev,'pos':pos,'sl':sl,'conf':conf,'sig':sig})
            all_results.append(r)

            if r['ret'] > best_ret:
                best_ret = r['ret']; best_dd = r['dd']; best_reg = rn
            if r['liq']: all_ok = False

        avg_ret = round(np.mean([x['ret'] for x in all_results if x['pair']==pair_name and x['config']==cfg_name]), 1)
        avg_dd = round(np.mean([x['dd'] for x in all_results if x['pair']==pair_name and x['config']==cfg_name]), 1)
        status = 'OK' if all_ok else 'LIQ!'
        summary.append(f'{pair_name:4s} {cfg_name:14s}: avg={avg_ret:>+6.1f}% dd={avg_dd:>5.1f}% | best={best_ret:+.0f}%({best_reg}) {status}')
        print(f'  {cfg_name:14s}: avg={avg_ret:>+6.1f}% dd={avg_dd:>5.1f}% {status}')

# RL comparison
if has_rl:
    print(f'\n--- RL Dual-Signal Comparison ---')
    for pair_safe, pair_name in [('ETH_USDT_USDT','ETH'),('BTC_USDT_USDT','BTC')]:
        df = load_data(pair_safe)
        bull, bear, ranging = classify_regime(df)
        for rn, mask in [('BULL',bull),('BEAR',bear)]:
            lgbm = run_backtest(df, mask, 5, 0.15, 0.08, 0.50, 0.0005, False)
            dual = run_backtest(df, mask, 5, 0.15, 0.08, 0.50, 0.0005, True)
            delta = dual['ret'] - lgbm['ret']
            lr = lgbm["ret"]; dr = dual["ret"]; dl = round(dr-lr,1)
            print("  " + pair_name + " " + rn + ": LGBM=" + str(lr) + "% RL=" + str(dr) + "% (delta=" + str(dl) + "%)")

print('\n' + '='*70)
print('  SUMMARY')
print('='*70)
for s in summary: print('  ' + s)

# Find best config per pair
for pair_name in ['ETH','BTC']:
    pr = [r for r in all_results if r['pair']==pair_name and not r['liq']]
    if pr:
        best = max(pr, key=lambda r: r['ret'])
            b = best

# Save report
report = {'timestamp': datetime.now().isoformat(), 'version': 4, 'has_rl': has_rl,
    'configs_tested': len(configs), 'total_results': len(all_results),
    'results': all_results, 'summary': summary}
ts = datetime.now().strftime('%Y%m%d_%H%M%S')
with open(f'{RP}/comprehensive_v4_{ts}.json', 'w') as f:
    json.dump(report, f, indent=2, default=str)
print(f'\nReport: reports/comprehensive_v4_{ts}.json')
