"""Day 6: Integration test + end-to-end validation"""
import sys, json, time; sys.path.insert(0, '.')
import numpy as np; import pandas as pd
from datetime import datetime
from pathlib import Path

MD = '../ethereum-ai-trader/models'
REPORTS = '../ethereum-ai-trader/reports'
Path(REPORTS).mkdir(parents=True, exist_ok=True)

print('='*60)
print('  Day 6: Integration Test + End-to-End Validation')
print('='*60)
results = {}
t0 = time.time()

# T1: Data Pipeline
print('\n[T1] Data Pipeline')
from freqtrade.ai.features import FeatureEngineer
from freqtrade.ai.direction_predictor import DirectionPredictor
from freqtrade.ai.regime_classifier import RegimeClassifier
fe = FeatureEngineer()
for ps in ['BTC_USDT_USDT', 'ETH_USDT_USDT']:
    df = pd.read_feather('user_data/data/okx/' + ps + '-4h-futures.feather')
    df['date'] = pd.to_datetime(df['date']); df = df.sort_values('date')
    f = fe.compute_price_features(df)
    assert len(f) > 1000
    assert f.iloc[50:].isna().sum().sum() == 0
    print('  ' + ps + ': ' + str(len(f)) + ' rows, 0 NaN OK')

# T2: Model Integrity
print('\n[T2] Model Integrity')
rc = RegimeClassifier(model_dir=MD); dp = DirectionPredictor(model_dir=MD)
rc.load(); dp.load()
btc_f = fe.compute_price_features(pd.read_feather('user_data/data/okx/BTC_USDT_USDT-4h-futures.feather'))
r_preds = rc.predict(btc_f.iloc[-10:])
d_preds = dp.predict(btc_f.iloc[-10:])
valid_r = sum(1 for p in r_preds if p is not None)
valid_d = sum(1 for p in d_preds if p is not None)
print('  Regime: ' + str(valid_r) + '/10 valid | Dir: ' + str(valid_d) + '/10 valid')
assert valid_r > 0 and valid_d > 0

# T3: Safety Rules
print('\n[T3] Safety Rules')
from freqtrade.ai.decision_arbitrator import DecisionArbitrator, RiskCalculator, Action
arb = DecisionArbitrator(RiskCalculator())
all_safe = True
tests = [
    ('HIGH_VOL->HOLD', arb.decide(5000,[],'HIGH_VOLATILITY',0.05,0.9,-0.01,0.015), Action.HOLD),
    ('RANGING->HOLD', arb.decide(5000,[],'RANGING_TIGHT',0.03,0.7,-0.005,0.015), Action.HOLD),
    ('LowConf->HOLD', arb.decide(5000,[],'TRENDING',0.03,0.30,-0.005,0.015), Action.HOLD),
    ('3Loss->STOP', arb.decide(5000,[],'TRENDING_STRONG',0.02,0.8,-0.003,0.015,consecutive_losses=3), Action.STOP),
]
for name, d, exp in tests:
    ok = d.action == exp
    if not ok: all_safe = False
    print('  [' + ('PASS' if ok else 'FAIL') + '] ' + name)
results['safety'] = all_safe

# T4: EMA-Trend filter
print('\n[T4] EMA-Trend Filter Validation')
def run_btc(use_filter):
    df = pd.read_feather('user_data/data/okx/BTC_USDT_USDT-4h-futures.feather')
    df['date'] = pd.to_datetime(df['date']); df = df.sort_values('date')
    f = fe.compute_price_features(df); p = dp.predict(f)
    eq = 1000.0; peak = 1000.0; dd = 0.0; t = 0; w = 0; liq = False
    for i in range(50, len(df)-1):
        if not p[i] or p[i]['confidence'] < 0.60: continue
        er = p[i]['expected_return']
        if abs(er) < 0.002: continue
        if use_filter:
            e50 = df['close'].ewm(span=50).mean().iloc[i]
            pr = df['close'].iloc[i]
            if (er>0 and pr<e50) or (er<0 and pr>e50): continue
        o, c, h, l = map(float, [df['open'].iloc[i],df['close'].iloc[i],df['high'].iloc[i],df['low'].iloc[i]])
        is_long = er > 0
        pnl = eq * 0.15 * (((c/o-1) if is_long else (1-c/o)) * 3)
        slp = o*(1-0.05/3) if is_long else o*(1+0.05/3)
        if (is_long and l<=slp) or (not is_long and h>=slp): pnl = -eq*0.15*0.05
        pnl = max(pnl, -eq*0.15*0.05); eq += pnl
        if pnl>0: w+=1
        t+=1
        if eq>peak: peak=eq
        dd = max(dd,(peak-eq)/peak if peak>0 else 0)
        if eq<=10: liq=True; break
        if t>=200: break
    return dict(final=round(eq,2),ret=round((eq/1000-1)*100,1),dd=round(dd*100,1),t=t,w=w,wr=round(w/t*100,1) if t else 0,liq=liq)

no = run_btc(False); yes = run_btc(True)
dlt = round(yes['ret'] - no['ret'], 1)
nr, nd, nw, nt = no['ret'], no['dd'], no['wr'], no['t']
yr, yd, yw, yt = yes['ret'], yes['dd'], yes['wr'], yes['t']
print('  No filter: ' + str(nr) + '% DD=' + str(nd) + '% WR=' + str(nw) + '% T=' + str(nt))
print('  EMA-Trend: ' + str(yr) + '% DD=' + str(yd) + '% WR=' + str(yw) + '% T=' + str(yt))
print('  Delta: ' + str(dlt) + '%')
results['ema_delta'] = dlt

# T5: Full backtest
print('\n[T5] Full Backtest (optimized configs)')
from freqtrade.ai.backtest_adapter import AIBacktestAdapter
for pn, ps in [('BTC/USDT:USDT','BTC_USDT_USDT'),('ETH/USDT:USDT','ETH_USDT_USDT')]:
    df = pd.read_feather('user_data/data/okx/' + ps + '-4h-futures.feather')
    t1 = time.time()
    r = AIBacktestAdapter(MD, initial_equity=5000).run(df, pn, warmup=50)
    dur = (time.time()-t1)*1000
    ap = all(r.pass_criteria.values())
    print('  ' + pn + ': S=' + str(round(r.sharpe_ratio,2)) + ' DD=' + str(round(r.max_drawdown*100,1)) + '% WR=' + str(round(r.win_rate*100,0)) + '% PF=' + str(round(r.profit_factor,2)) + ' T=' + str(r.total_trades) + ' ' + ('PASS' if ap else 'FAIL') + ' ' + str(round(dur,0)) + 'ms')
    results[pn + '_pass'] = ap

# T6: Performance
print('\n[T6] Performance')
t1 = time.time(); _ = fe.compute_price_features(pd.read_feather('user_data/data/okx/BTC_USDT_USDT-4h-futures.feather'))
ft = round((time.time()-t1)*1000, 0)
row = btc_f.iloc[-1:]
t1 = time.time(); _ = rc.predict(row); _ = dp.predict(row)
it = round((time.time()-t1)*1000, 0)
print('  Features: ' + str(ft) + 'ms | Inference: ' + str(it) + 'ms')
results['ft_ms'] = ft; results['it_ms'] = it

# Save
total = round(time.time()-t0, 1)
report = dict(timestamp=datetime.now().isoformat(), day=6, results=results, total_s=total)
ts = datetime.now().strftime('%Y%m%d_%H%M%S')
with open(REPORTS + '/day06_' + ts + '.json', 'w') as f:
    json.dump(report, f, indent=2, default=str)

all_ok = all_safe and results.get('BTC/USDT:USDT_pass',False) and results.get('ETH/USDT:USDT_pass',False)
print('\nDay 6: ' + ('ALL PASS' if all_ok else 'SOME FAIL') + ' | Total: ' + str(total) + 's')
print('Report: reports/day06_' + ts + '.json')
