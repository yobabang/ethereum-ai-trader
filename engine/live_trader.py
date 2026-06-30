"""Standalone Live Trader — sync ccxt + AI models, no freqtrade dependency.

Runs a simple event loop:
  1. Fetch latest OHLCV from OKX
  2. Compute features
  3. Run AI pipeline (regime -> direction -> risk -> decision)
  4. Execute trade on OKX (or dry-run simulate)
  5. Log to journal

Usage:
  python -m engine.live_trader          # dry-run
  python -m engine.live_trader --live    # REAL trading
"""

import os, sys, json, time, logging
sys.path.insert(0, '.')
import numpy as np; import pandas as pd
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

import ccxt

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', stream=sys.stdout, force=True)
# Force unbuffered output for real-time monitoring
sys.stdout.reconfigure(line_buffering=True) if hasattr(sys.stdout, 'reconfigure') else None
logger = logging.getLogger(__name__)

MD = Path('./models')
JOURNAL = Path('./journal')
JOURNAL.mkdir(parents=True, exist_ok=True)

# OKX credentials
OKX_CONFIG = {
    'apiKey': os.environ.get('OKX_API_KEY', ''),
    'secret': os.environ.get('OKX_API_SECRET', ''),
    'password': os.environ.get('OKX_API_PASSPHRASE', ''),
    'enableRateLimit': True,
    'proxies': {'http': 'socks5h://127.0.0.1:10808', 'https': 'socks5h://127.0.0.1:10808'},
    'options': {'defaultType': 'swap'},
}

PAIRS = ['ETH/USDT:USDT', 'BTC/USDT:USDT']
TIMEFRAME = '1h'
CHECK_INTERVAL_MINUTES = 15  # Check every 15 minutes

# Risk params (from iteration tests)
LEVERAGE = 10
POSITION_PCT = 0.20
STOP_LOSS_PCT = 0.08
MIN_CONFIDENCE = 0.45  # Aggressive mode
MIN_SIGNAL = 0.0003  # 0.03%


class LiveTrader:
    def __init__(self, live: bool = False):
        self.live = live
        self.exchange: Optional[ccxt.Exchange] = None
        self._last_ohlcv: dict[str, pd.DataFrame] = {}
        self._open_positions: dict = {}
        self._trade_count = 0
        self._rl_warned = False
        self._journal = self._init_journal()

    def _init_journal(self):
        from engine.trade_journal import TradeJournal
        return TradeJournal(str(JOURNAL))

    def connect(self):
        self.exchange = ccxt.okx(OKX_CONFIG)
        self.exchange.load_markets()
        logger.info(f"Connected to OKX. {len(self.exchange.markets)} markets loaded")
        for pair in PAIRS:
            if pair in self.exchange.markets:
                logger.info(f"  {pair}: available")
        # Check balance
        if self.live:
            b = self.exchange.fetch_balance()
            usdt = b.get('USDT', {}).get('total', 0)
            logger.info(f"  Balance: {usdt} USDT")
            self.exchange.set_leverage(LEVERAGE, pair)

    def _fetch_ohlcv(self, pair: str) -> pd.DataFrame:
        ohlcv = self.exchange.fetch_ohlcv(pair, TIMEFRAME, limit=300)
        df = pd.DataFrame(ohlcv, columns=['date', 'open', 'high', 'low', 'close', 'volume'])
        df['date'] = pd.to_datetime(df['date'], unit='ms')
        return df.sort_values('date')

    def _run_ai_pipeline(self, df: pd.DataFrame) -> Optional[dict]:
        """Run the full AI pipeline and return a decision."""
        from engine.features import FeatureEngineer
        from engine.direction_predictor import DirectionPredictor
        from engine.decision_arbitrator import DecisionArbitrator, RiskCalculator

        fe = FeatureEngineer()
        dp = DirectionPredictor(model_dir=str(MD))
        arb = DecisionArbitrator(RiskCalculator())

        try:
            features = fe.compute_price_features(df)
            dp.load()
            preds = dp.predict(features.iloc[-1:])
        except Exception as e:
            logger.error(f"AI pipeline failed: {e}")
            return None

        if not preds or not preds[-1]:
            return None

        # RL dual-signal (warn once if model missing)
        rl_action = None
        try:
            from engine.rl_signal import RlSignalAgent
            rl = RlSignalAgent(model_dir=str(MD))
            if rl.load():
                rl_action = rl.predict(features)
            elif not self._rl_warned:
                logger.info("RL model not trained — using LightGBM-only mode")
                self._rl_warned = True
        except Exception as e:
            if not self._rl_warned:
                logger.info(f"RL not available: {e}")
                self._rl_warned = True

        p = preds[-1]
        er = p['expected_return']
        conf = p['confidence']

        # RL-LightGBM fusion: if RL disagrees with high confidence, RL vetoes.
        # rl.predict() returns a list[dict]; extract the RL direction from it.
        # (Previously this compared a list to a string, so the veto was dead code.)
        rl_dir = None
        if rl_action:
            try:
                rl_er = rl_action[-1].get('expected_return', 0)
                rl_dir = 'long' if rl_er > 0.001 else 'short' if rl_er < -0.001 else None
            except (IndexError, AttributeError, TypeError):
                rl_dir = None

        if rl_dir in ('long', 'short'):
            lgbm_action = 'long' if er > 0.001 else 'short' if er < -0.001 else 'hold'
            if lgbm_action != rl_dir and lgbm_action != 'hold':
                logger.info(f"[DUAL] RL({rl_dir}) vs LGBM({lgbm_action}) — RL overrides")
                er = 0.003 if rl_dir == 'long' else -0.003
                conf = max(conf, 0.65)

        if conf < MIN_CONFIDENCE:
            return {'action': 'HOLD', 'reason': f'Confidence {conf:.2f} < {MIN_CONFIDENCE}',
                    'confidence': conf, 'expected_return': er, 'position_size_pct': 0}
        if abs(er) < MIN_SIGNAL:
            return {'action': 'HOLD', 'reason': f'Signal {er:.5f} below noise floor',
                    'confidence': conf, 'expected_return': er, 'position_size_pct': 0}

        # EMA-Trend filter
        e50 = df['close'].ewm(span=50).mean().iloc[-1]
        pr = float(df['close'].iloc[-1])
        if (er > 0 and pr < e50) or (er < 0 and pr > e50):
            return {'action': 'HOLD', 'reason': f'Counter-trend blocked (EMA50={e50:.0f})',
                    'confidence': conf, 'expected_return': er, 'position_size_pct': 0}

        atr_pct = float(features['atr_ratio'].iloc[-1]) if 'atr_ratio' in features.columns else 0.015

        decision = arb.decide(
            account_equity=735.0, current_positions=[],
            regime='TRENDING_WEAK', expected_return=er,
            confidence=conf, max_drawdown=p['max_drawdown'],
            atr_pct=atr_pct,
            adaptive_confidence=MIN_CONFIDENCE, adaptive_position_scalar=1.0,
        )

        return {
            'action': decision.action.value,
            'reason': decision.reason,
            'expected_return': er,
            'confidence': conf,
            'position_size_pct': decision.position_size_pct,
            'stop_loss_pct': decision.stop_loss_pct,
            'take_profit_pct': decision.take_profit_pct,
            'leverage': decision.leverage,
        }

    def run_once(self):
        """One iteration of the trading loop."""
        for pair in PAIRS:
            df = self._fetch_ohlcv(pair)
            decision = self._run_ai_pipeline(df)

            if decision is None:
                logger.warning(f"{pair}: AI pipeline returned None")
                continue

            action = decision['action']

            # Log decision
            self._journal.record_decision(
                action=action, reason=decision.get('reason', ''),
                confidence=decision.get('confidence', 0),
                expected_return=decision.get('expected_return', 0),
                position_size_pct=decision.get('position_size_pct', 0),
                stop_loss_pct=decision.get('stop_loss_pct', 0),
                take_profit_pct=decision.get('take_profit_pct', 0),
                leverage=decision.get('leverage', LEVERAGE),
            )

            # Execute — check existing position first
            price = float(df['close'].iloc[-1])
            if action in ('long', 'short'):
                # Check existing position
                existing_side = None
                try:
                    pos = self.exchange.fetch_position(pair)
                    contracts = float(pos.get('contracts', 0) or 0)
                    if contracts > 0:
                        existing_side = pos.get('side', '')
                except Exception:
                    pass

                if existing_side:
                    if existing_side == action:
                        logger.info(f"[SKIP] {pair}: already {action}, holding")
                        # Check if SL was hit (position disappeared since last check)
                        if pair in self._open_positions and self._open_positions[pair] != existing_side:
                            logger.info(f"[EXIT] {pair}: position closed (SL/flip)")
                            self._journal.record_exit(
                                trade_id=f"{pair}_{existing_side}",
                                exit_price=price, pnl=0, pnl_pct=0,
                                exit_reason='stop_loss_or_flip', duration_hours=0
                            )
                        continue
                    else:
                        logger.info(f"[FLIP] {pair}: closing {existing_side} -> opening {action}")
                        try:
                            close_side = 'buy' if existing_side == 'short' else 'sell'
                            self.exchange.create_order(pair, 'market', close_side, contracts, None,
                                {'reduceOnly': True, 'posSide': existing_side})
                            logger.info(f"  Closed existing {existing_side}")
                            # Record exit
                            self._journal.record_exit(
                                trade_id=f"{pair}_{existing_side}",
                                exit_price=price, pnl=0, pnl_pct=0,
                                exit_reason='signal_flip', duration_hours=0
                            )
                        except Exception as e:
                            logger.warning(f"  Close failed: {e}")
                logger.info(f"[SIGNAL] {pair} {action.upper()} @ ${price:,.2f} | {decision['reason'][:80]}")
                self._trade_count += 1

                if self.live:
                    self._place_order(pair, action, decision, price)
                else:
                    logger.info(f"  [DRY-RUN] Would place {action.upper()} order")

                # Record entry to journal
                self._journal.record_entry(
                    pair=pair, side=action, entry_price=price, amount=0.01,
                    leverage=decision.get('leverage', LEVERAGE),
                    stop_loss=price * (1 - decision['stop_loss_pct']) if action == 'long' else price * (1 + decision['stop_loss_pct']),
                    take_profit=price * (1 + decision['take_profit_pct']) if action == 'long' else price * (1 - decision['take_profit_pct']),
                    confidence=decision['confidence'],
                    expected_return=decision['expected_return'],
                    regime='TRENDING_WEAK',
                )
            else:
                logger.info(f"[{action.upper()}] {pair} @ ${price:,.2f} | {decision['reason'][:60]}")

    def _place_order(self, pair: str, side: str, decision: dict, price: float):
        """Place a real order on OKX with isolated margin + stop-loss."""
        try:
            okx_side = 'buy' if side == 'long' else 'sell'
            sl_pct = decision.get('stop_loss_pct', STOP_LOSS_PCT)
            lev = decision.get('leverage', LEVERAGE)

            # Entry order (long_short_mode requires posSide)
            pos_side = 'short' if side == 'short' else 'long'
            order = self.exchange.create_order(
                symbol=pair, type='market', side=okx_side,
                amount=0.01, params={'leverage': lev, 'posSide': 'short' if side == 'short' else 'long', 'tdMode': 'isolated'}
            )
            logger.info(f"  ORDER: {order['id']} {okx_side} @ ~${price:,.2f} | SL={sl_pct*100:.0f}%")

            # Stop-loss
            sl_side = 'buy' if okx_side == 'sell' else 'sell'
            sl_price = round(price * (1 + sl_pct / lev) if okx_side == 'sell' else price * (1 - sl_pct / lev), 2)
            try:
                self.exchange.create_order(
                    symbol=pair, type='stop', side=sl_side,
                    amount=0.01, price=sl_price,
                    params={'stopLossPrice': sl_price, 'reduceOnly': True, 'posSide': 'short' if side == 'short' else 'long'}
                )
                logger.info(f"  SL: {sl_side} @ ${sl_price:,.2f}")
            except Exception as sle:
                logger.warning(f"  SL FAILED: {sle}")

            # AI Take-profit: based on predicted return
            expected_ret = abs(decision.get('expected_return', 0.005))
            tp_pct = max(expected_ret, 0.005)  # minimum 0.5% TP
            tp_price = round(price * (1 - tp_pct) if okx_side == 'sell' else price * (1 + tp_pct), 2)
            try:
                self.exchange.create_order(
                    symbol=pair, type='limit', side=sl_side,
                    amount=0.01, price=tp_price,
                    params={'reduceOnly': True, 'posSide': 'short' if side == 'short' else 'long'}
                )
                logger.info(f"  TP: {sl_side} @ ${tp_price:,.2f} (AI target {tp_pct*100:.1f}%)")
            except Exception as tpe:
                logger.warning(f"  TP FAILED: {tpe}")

        except Exception as e:
            logger.error(f"  ORDER FAILED: {e}")

    def run_loop(self):
        """Main trading loop."""
        self.connect()
        logger.info(f"Live Trader started. Mode: {'LIVE' if self.live else 'DRY-RUN'}")
        logger.info(f"Pairs: {PAIRS} | Leverage: {LEVERAGE}x | Check: {CHECK_INTERVAL_MINUTES}min")
        logger.info(f"Risk: {POSITION_PCT*100:.0f}% pos, {STOP_LOSS_PCT*100:.0f}% SL, {MIN_CONFIDENCE*100:.0f}% min conf")

        while True:
            try:
                self.run_once()
            except Exception as e:
                logger.error(f"Loop error: {e}", exc_info=True)
                self._journal.record_anomaly('loop_error', str(e)[:200], 'warning')

            self._trade_count += 1
            mins = CHECK_INTERVAL_MINUTES
            logger.info(f"[HEARTBEAT] Cycle #{self._trade_count} done | Next in {mins}min | ETH 10x DRY-RUN")
            time.sleep(mins * 60)


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--live', action='store_true', help='REAL trading (default: dry-run)')
    args = p.parse_args()

    if args.live:
        print("=" * 55)
        print("  WARNING: LIVE TRADING MODE")
        print("  Real orders will be placed on OKX")
        print("=" * 55)
        print("Type 'YES' to confirm: ", end='')
        confirm = input().strip()
        if confirm != 'YES':
            print("Aborted.")
            sys.exit(0)

    trader = LiveTrader(live=args.live)
    trader.run_loop()


if __name__ == '__main__':
    main()
