"""Crypto Trader Boot — system check + ETH simulation + status report"""
import os, sys, json, time
sys.path.insert(0, '.')
import numpy as np; import pandas as pd
from datetime import datetime
from pathlib import Path

# Load from environment or .env file
os.environ.setdefault('OKX_API_KEY', '')
os.environ.setdefault('OKX_API_SECRET', '')
os.environ.setdefault('OKX_API_PASSPHRASE', '')

MD = Path('../ethereum-ai-trader/models')
JOURNAL = Path('../ethereum-ai-trader/journal')
JOURNAL.mkdir(parents=True, exist_ok=True)

print('='*55)
print('  CRYPTO TRADER — ETH 5x Trading System')
print('='*55)

# 1. OKX Connection
import ccxt
okx = ccxt.okx({
    'apiKey': os.environ['OKX_API_KEY'],
    'secret': os.environ['OKX_API_SECRET'],
    'password': os.environ['OKX_API_PASSPHRASE'],
    'enableRateLimit': True,
    'proxies': {'http': 'socks5h://127.0.0.1:10808', 'https': 'socks5h://127.0.0.1:10808'},
    'options': {'defaultType': 'swap'}
})
okx.load_markets()
b = okx.fetch_balance()
usdt_total = b.get('USDT', {}).get('total', 0)
print(f'[1] OKX: {usdt_total} USDT | Connected')

# 2. Models
for f in ['regime_classifier.pkl', 'direction_predictor.pkl']:
    ok = (MD / f).exists()
    sz = (MD / f).stat().st_size / 1024 if ok else 0
    print(f'[2] Model {f}: {"OK" if ok else "MISSING"} ({sz:.0f} KB)')

# 3. ETH Simulation
from engine.features import FeatureEngineer
from engine.direction_predictor import DirectionPredictor
fe = FeatureEngineer()
dp = DirectionPredictor(model_dir=str(MD))
df = pd.read_feather('user_data/data/okx/ETH_USDT_USDT-4h-futures.feather')
df['date'] = pd.to_datetime(df['date']); df = df.sort_values('date')
features = fe.compute_price_features(df)
try: dp.load()
except: dp.train(features)
preds = dp.predict(features)

eq = float(usdt_total) if usdt_total > 0 else 1000.0
start_eq = eq; peak = eq; dd = 0.0; t = 0; w = 0; liq = False
for i in range(50, len(df)-1):
    if not preds[i] or preds[i]['confidence'] < 0.60: continue
    er = preds[i]['expected_return']
    if abs(er) < 0.002: continue
    e50 = df['close'].ewm(span=50).mean().iloc[i]; pr = df['close'].iloc[i]
    if (er>0 and pr<e50) or (er<0 and pr>e50): continue
    o,c,h,l = map(float, [df['open'].iloc[i], df['close'].iloc[i], df['high'].iloc[i], df['low'].iloc[i]])
    lg = er > 0
    pnl = eq * 0.20 * (((c/o-1) if lg else (1-c/o)) * 5)
    slp = o*(1-0.08/5) if lg else o*(1+0.08/5)
    if (lg and l<=slp) or (not lg and h>=slp): pnl = -eq * 0.20 * 0.08
    pnl = max(pnl, -eq * 0.20 * 0.08); eq += pnl
    if pnl>0: w+=1; t+=1
    if eq>peak: peak=eq
    dd = max(dd, (peak-eq)/peak if peak>0 else 0)
    if eq<=start_eq*0.1: liq=True; break
    if t>=100: break

ret = round((eq/start_eq-1)*100, 1)
wr = round(w/t*100, 1) if t else 0
ddr = round(dd*100, 1)
print(f'[3] Sim: ${start_eq:.2f} -> ${eq:.2f} ({ret:+.1f}%) DD={ddr:.1f}% WR={wr:.1f}% T={t} Liq={liq}')

# 4. Live ETH price
ticker = okx.fetch_ticker('ETH/USDT:USDT')
eth_price = ticker['last']
print(f'[4] LIVE ETH/USDT: ${eth_price:,.2f}')

# 5. Recommendation
print(f'\n{"="*55}')
if not liq and ret > -10:
    print(f'  STATUS: READY for dry-run')
    print(f'  CMD:   python -m freqtrade trade -c ../ethereum-ai-trader/config.json --dry-run')
else:
    print(f'  STATUS: REVIEW parameters first')
print(f'{"="*55}')

# Save
status = {'timestamp': datetime.now().isoformat(), 'account': usdt_total, 'eth_price': eth_price,
    'sim_return_pct': ret, 'sim_dd_pct': ddr, 'sim_wr_pct': wr, 'sim_trades': t, 'models_ok': True}
with open(JOURNAL / 'status.json', 'w') as f: json.dump(status, f, indent=2)
print(f'\nStatus: journal/status.json')
