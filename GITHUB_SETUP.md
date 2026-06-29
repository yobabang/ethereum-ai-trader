# GitHub Backup Instructions

## Two Repositories

This project consists of two repositories:

### 1. ethereum-ai-trader/ (Project Config & Docs)
```
cd ethereum-ai-trader
git remote add origin https://github.com/YOUR_USERNAME/ethereum-ai-trader.git
git push -u origin master
```

### 2. freqtrade/ (AI Trading Engine — Deep Fork)
```
cd freqtrade
git remote add origin https://github.com/YOUR_USERNAME/ethereum-ai-freqtrade.git
git push -u origin develop
```

## Security Checklist Before Push

- [x] No API keys in code (all use env vars)
- [x] api.txt in .gitignore
- [x] config.json in .gitignore
- [x] models/ in .gitignore (large binary files)
- [x] journal/ in .gitignore (runtime data)
- [x] reports/ in .gitignore (generated files)
- [x] user_data/ in .gitignore

## Setup After Clone

```bash
# 1. Clone both repos
git clone https://github.com/YOUR_USERNAME/ethereum-ai-trader.git
git clone https://github.com/YOUR_USERNAME/ethereum-ai-freqtrade.git freqtrade

# 2. Set up Python venv
cd freqtrade
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt

# 3. Configure API keys
cp ../ethereum-ai-trader/.env.example .env
# Edit .env with your OKX credentials

# 4. Download historical data
python -m freqtrade download-data -c ../ethereum-ai-trader/config.json \
  --pairs BTC/USDT:USDT ETH/USDT:USDT --timeframes 4h --trading-mode futures

# 5. Train models
python -m freqtrade.ai.trainer --config ../ethereum-ai-trader/config.json

# 6. Start trading
python -m freqtrade.ai.live_trader          # dry-run
python -m freqtrade.ai.live_trader --live    # real trading
```

## What's NOT in Git (intentional)

- `api.txt` — API keys
- `config.json` — Exchange configuration
- `models/*.pkl` — Trained model files (regenerate with trainer)
- `user_data/` — Historical OHLCV data (download with freqtrade)
- `journal/` — Trade records (runtime generated)
- `reports/` — Test reports (runtime generated)
