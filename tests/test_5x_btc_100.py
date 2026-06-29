"""1000 USDT, 5x BTC perpetual, 100 trades stress test."""
import sys; sys.path.insert(0, '.')
import numpy as np; import pandas as pd

df = pd.read_feather('user_data/data/okx/BTC_USDT_USDT-4h-futures.feather')
df['date'] = pd.to_datetime(df['date'])
df = df.sort_values('date')

initial = 1000.0; leverage = 5
equity = initial; peak = initial; max_dd = 0.0
trades = []; liquidated = False

for i in range(1, len(df)):
    o, c, h, l = map(float, [df['open'].iloc[i], df['close'].iloc[i], df['high'].iloc[i], df['low'].iloc[i]])
    entry = o; exit_p = c
    pnl_pct = (exit_p / entry - 1) * leverage
    pnl = equity * pnl_pct  # full position each trade

    liq = entry * (1 - 1.0 / leverage)  # 20% drop for 5x
    if l <= liq:
        pnl = -equity; liquidated = True
        trades.append({'i': i, 'entry': entry, 'exit': liq, 'pnl': pnl, 'pnl_pct': -100, 'liq': True})
        equity += pnl; break

    equity += pnl
    trades.append({'i': i, 'entry': entry, 'exit': exit_p, 'pnl': pnl, 'pnl_pct': pnl_pct * 100, 'liq': False})
    if equity > peak: peak = equity
    dd = (peak - equity) / peak if peak > 0 else 0
    if dd > max_dd: max_dd = dd
    if equity <= 0: break
    if len(trades) >= 100: break  # Stop after exactly 100 trades

n = len(trades)
wins = [t for t in trades if t['pnl'] > 0]
losses = [t for t in trades if t['pnl'] <= 0]

print('='*50)
print('  1000 USDT, 5x BTC, 100次交易测试')
print('='*50)
print(f'  初始资金:  ${initial:,.0f}')
print(f'  最终资金:  ${equity:,.2f}')
print(f'  总收益率:  {((equity/initial)-1)*100:+.1f}%')
print(f'  杠杆倍数:  {leverage}x')
print(f'  爆仓:      {"是 (第{}根K线)".format(trades[-1]["i"]) if liquidated else "否"}')
print(f'  最大回撤:  {max_dd*100:.1f}%')
print(f'  交易次数:  {n}')
print(f'  盈利次数:  {len(wins)} ({len(wins)/n*100:.0f}%)')
print(f'  亏损次数:  {len(losses)} ({len(losses)/n*100:.0f}%)')
if wins:  print(f'  平均盈利:  ${np.mean([t["pnl"] for t in wins]):,.2f}')
if losses: print(f'  平均亏损:  ${np.mean([t["pnl"] for t in losses]):,.2f}')
print(f'  最大盈利:  ${max(t["pnl"] for t in trades):,.2f}')
print(f'  最大亏损:  ${min(t["pnl"] for t in trades):,.2f}')

print(f'\n  交易明细 (首5 + 末5):')
for t in trades[:5]:
    print(f'    K{t["i"]:4d}: entry=${t["entry"]:,.0f} exit=${t["exit"]:,.0f} pnl=${t["pnl"]:+,.2f} ({t["pnl_pct"]:+.1f}%)')
if len(trades) > 10:
    print(f'    ...')
for t in trades[-5:]:
    print(f'    K{t["i"]:4d}: entry=${t["entry"]:,.0f} exit=${t["exit"]:,.0f} pnl=${t["pnl"]:+,.2f} ({t["pnl_pct"]:+.1f}%)')

print(f'\n  资金曲线:')
for m in [0, n//4, n//2, 3*n//4, n-1]:
    if m < len(trades):
        eq = initial + sum(t['pnl'] for t in trades[:m+1])
        print(f'    第{m+1:3d}笔后: ${eq:,.2f} ({(eq/initial-1)*100:+.1f}%)')

print(f'\n  结论: {"存活" if not liquidated else "爆仓"}, 最终 {"盈利" if equity > initial else "亏损"}')
