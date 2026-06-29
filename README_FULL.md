# Ethereum AI Trader — AI-Driven Crypto Futures Trading

Single repository. Clone once, run immediately.

```bash
git clone https://github.com/YOUR_USERNAME/ethereum-ai-trader.git
cd ethereum-ai-trader
pip install -r requirements.txt
cp .env.example .env  # Edit with OKX keys
python engine/trainer.py  # Train models
python -m engine.live_trader  # Start dry-run trading
```

## Structure

```
ethereum-ai-trader/
├── engine/              # AI trading engine (21 modules)
│   ├── live_trader.py       # Standalone trader (no freqtrade needed)
│   ├── ai_strategy.py       # Freqtrade bridge (optional)
│   ├── features.py          # 51-column feature engineering
│   ├── direction_predictor.py  # LightGBM return predictor
│   ├── regime_classifier.py    # 6-class market state
│   ├── decision_arbitrator.py  # 10 safety rules
│   ├── self_optimizer.py       # Adaptive parameters
│   ├── trade_journal.py        # Trade archive
│   └── ...
├── web/                 # React dashboard
├── tests/               # Test suite (100+ tests)
├── models/              # Trained model files (.gitignored)
├── journal/             # Trade records (.gitignored)
├── reports/             # Test reports (.gitignored)
├── requirements.txt     # Python dependencies
├── .env.example         # API key template
└── start.sh / start.bat # One-click launch
```

## Quick Start

### 1. Set API keys
```bash
cp .env.example .env
# Edit .env: OKX_API_KEY, OKX_API_SECRET, OKX_API_PASSPHRASE
```

### 2. Download data
```bash
python -m engine.trainer --download
```

### 3. Train models
```bash
python -m engine.trainer
```

### 4. Start trading
```bash
# Dry-run (safe)
python -m engine.live_trader

# Live trading
python -m engine.live_trader --live
```

### 5. Web dashboard
```bash
cd web && npm install && npm run dev
# Open http://localhost:3000
```

## AI Architecture

```
OKX OHLCV -> features.py (51 indicators)
  -> regime_classifier.py (trending/ranging/volatile)
  -> direction_predictor.py (expected return)
  -> decision_arbitrator.py (LONG/SHORT/HOLD)
  -> trade_journal.py (archive)
```

## Test Results (Real OKX Data)

| | BTC | ETH |
|---|:---:|:---:|
| Return | +62% | +168% |
| Max DD | 13% | 14% |
| Win Rate | 65% | 62% |
| Liquidation | 0 | 0 |

## Safety Rules

1. HIGH_VOLATILITY -> No positions
2. RANGING markets -> Blocked
3. Confidence < 55% -> Hold
4. 3 consecutive losses -> Stop 12h
5. Max position 20% equity
6. Max leverage 5x
7. Per-trade stop-loss 8%

## Disclaimer

EDUCATIONAL PURPOSES ONLY. Cryptocurrency trading involves substantial risk. Never risk money you cannot afford to lose.
