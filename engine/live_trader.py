"""Standalone Live Trader v2.0 — AI/trend mode + sim_broker execution.

PRINCIPLE: 除了钱是假的，其他必须真。
- Real OKX public market data (no API key, no ccxt, no live trading)
- AI decisions executed through SimBroker (virtual money)
- Supports --mode ai (1h AI pipeline) or --mode trend (4h trend strategy)
- Supports --aggressive (lower thresholds, higher leverage cap)

NEVER connects to live OKX trading. Market data fetches are READ-ONLY public
endpoints. No API key is ever loaded.

Usage:
  python -m engine.live_trader                       # default: ai mode, conservative
  python -m engine.live_trader --mode trend          # 4h trend strategy
  python -m engine.live_trader --aggressive          # aggressive risk params
  python -m engine.live_trader --mode trend --aggressive
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, ".")

from engine.sim_broker import SimBroker, SimConfig
from engine.database import Database
from engine.features import FeatureEngineer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
    force=True,
)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
logger = logging.getLogger(__name__)

# ============================================================================
# Configuration
# ============================================================================

PAIRS = ["BTC/USDT:USDT", "ETH/USDT:USDT"]
TIMEFRAME = "1h"
CHECK_INTERVAL_SECONDS = 300  # 5 minutes

# OKX public market data (NO API KEY needed)
OKX_CANDLES_URL = "https://www.okx.com/api/v5/market/candles"
OKX_TICKER_URL = "https://www.okx.com/api/v5/market/ticker"
OKX_INST_MAP = {
    "BTC/USDT:USDT": "BTC-USDT-SWAP",
    "ETH/USDT:USDT": "ETH-USDT-SWAP",
}

# Binance public market data (fallback when OKX unreachable)
BINANCE_CANDLES_URL = "https://api.binance.com/api/v3/klines"
BINANCE_INST_MAP = {
    "BTC/USDT:USDT": "BTCUSDT",
    "ETH/USDT:USDT": "ETHUSDT",
}

# Conservative defaults (overridden by --aggressive)
DEFAULTS = {
    "leverage": 5,
    "position_pct": 0.20,
    "min_confidence": 0.55,
    "stop_loss_pct": 0.02,
    "take_profit_pct": 0.04,
    "min_signal": 0.0003,
}
AGGRESSIVE = {
    "leverage": 10,   # 10x gives breathing room
    "position_pct": 1.0,
    "min_confidence": 0.45,
    "stop_loss_pct": 0.03,   # wider SL, fewer false stops
    "take_profit_pct": 0.08,  # bigger TP target
    "min_signal": 0.0001,
}


# ============================================================================
# Market data (READ-ONLY public endpoints)
# ============================================================================


def fetch_ohlcv(pair: str, timeframe: str = "1h", limit: int = 300) -> Optional[pd.DataFrame]:
    """Fetch OHLCV from OKX public API, fallback to Binance if OKX fails.

    No auth needed for either exchange (public market data only).
    """
    # --- Try OKX first ---
    inst = OKX_INST_MAP.get(pair, pair.replace("/", "-").replace(":USDT", "-SWAP"))
    params = {"instId": inst, "bar": timeframe, "limit": str(limit)}
    try:
        r = requests.get(OKX_CANDLES_URL, params=params, timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        if data:
            # OKX returns newest first; reverse to oldest first
            rows = list(reversed(data))
            df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "vol", "volCcy", "volCcyQuote", "confirm"])
            df["date"] = pd.to_datetime(df["date"].astype(int), unit="ms")
            for c in ["open", "high", "low", "close", "vol"]:
                df[c] = df[c].astype(float)
            df = df[["date", "open", "high", "low", "close", "vol"]].rename(columns={"vol": "volume"})
            logger.debug(f"OHLCV {pair}: {len(df)} rows from OKX")
            return df
    except Exception as e:
        logger.warning(f"OKX OHLCV failed for {pair}: {e}, trying Binance...")

    # --- Fallback: Binance ---
    binance_sym = BINANCE_INST_MAP.get(pair)
    if not binance_sym:
        logger.error(f"fetch_ohlcv failed: unknown pair {pair}")
        return None
    # Map timeframe: OKX "1h" → Binance "1h", "4H" → "4h", "1D" → "1d"
    tf_map = {"1H": "1h", "4H": "4h", "1D": "1d"}
    binance_tf = tf_map.get(timeframe.upper(), timeframe.lower())
    try:
        r = requests.get(BINANCE_CANDLES_URL,
                         params={"symbol": binance_sym, "interval": binance_tf, "limit": limit},
                         timeout=10)
        r.raise_for_status()
        data = r.json()
        if data:
            # Binance returns oldest first already
            df = pd.DataFrame(data, columns=["date", "open", "high", "low", "close", "volume",
                                              "close_time", "quote_vol", "trades", "taker_buy_base", "taker_buy_quote", "ignore"])
            df["date"] = pd.to_datetime(df["date"].astype(int), unit="ms")
            for c in ["open", "high", "low", "close", "volume"]:
                df[c] = df[c].astype(float)
            df = df[["date", "open", "high", "low", "close", "volume"]]
            logger.info(f"OHLCV {pair}: {len(df)} rows from Binance (fallback)")
            return df
    except Exception as e:
        logger.warning(f"Binance OHLCV also failed for {pair}: {e}, trying local feather...")

    # --- Fallback 2: Local feather (offline mode) ---
    try:
        from engine.trainer import load_historical_data
        df = load_historical_data("user_data/data", pair, timeframe=timeframe)
        if df is not None and len(df) > 0:
            df["date"] = pd.to_datetime(df["date"])
            # Return last `limit` rows
            df = df.tail(limit).reset_index(drop=True)
            logger.info(f"OHLCV {pair}: {len(df)} rows from local feather (offline mode)")
            return df
    except Exception as e:
        logger.error(f"Local feather also failed for {pair}: {e}")
    return None


# ============================================================================
# LiveTrader
# ============================================================================


class LiveTrader:
    """Main trading loop. AI/trend decisions → SimBroker execution."""

    def __init__(self, mode: str = "ai", aggressive: bool = False,
                 db_path: str = "sim_trader.db", initial_equity: float = 1000.0):
        self.mode = mode
        self.aggressive = aggressive
        self.params = AGGRESSIVE if aggressive else DEFAULTS

        # Initialize sim broker
        config = SimConfig(
            initial_equity=initial_equity,
            max_leverage=self.params["leverage"],
            max_position_pct=self.params["position_pct"],
            min_confidence=self.params["min_confidence"],
            aggressive=aggressive,
        )
        self.broker = SimBroker(db_path=db_path, config=config)
        self.db = self.broker.db

        # AI pipeline components (lazy-loaded)
        self._fe = FeatureEngineer()
        self._rc = None  # RegimeClassifier
        self._dp = None  # DirectionPredictor
        self._rl = None  # RlSignalAgent
        self._arb = None  # DecisionArbitrator
        self._rl_warned = False

        # For trend mode
        self._trend_strat = None

        logger.info(f"LiveTrader started: mode={mode} aggressive={aggressive} "
                    f"equity=${initial_equity} params={self.params}")

    # ------------------------------------------------------------------
    # AI pipeline (mode='ai') — reuses existing engine modules
    # ------------------------------------------------------------------

    def _init_ai_pipeline(self):
        """Lazy-load AI models."""
        if self._rc is not None:
            return
        from engine.regime_classifier import RegimeClassifier
        from engine.direction_predictor import DirectionPredictor
        from engine.decision_arbitrator import DecisionArbitrator, RiskCalculator

        model_dir = str(Path("./models"))
        self._rc = RegimeClassifier(model_dir=model_dir)
        self._dp = DirectionPredictor(model_dir=model_dir)
        try:
            self._rc.load()
            self._dp.load()
            logger.info("AI models loaded")
        except Exception as e:
            logger.warning(f"AI models load failed: {e}")

        self._arb = DecisionArbitrator(RiskCalculator())

        # Optional RL
        try:
            from engine.rl_signal import RlSignalAgent
            self._rl = RlSignalAgent(model_dir=model_dir)
            if not self._rl.load():
                logger.info("RL model not available — LightGBM-only mode")
                self._rl = None
        except Exception as e:
            logger.info(f"RL unavailable: {e}")
            self._rl = None

    def _run_ai_pipeline(self, df: pd.DataFrame) -> Optional[dict]:
        """Run the 5-layer AI pipeline. Returns decision dict or None."""
        self._init_ai_pipeline()
        try:
            features = self._fe.compute_price_features(df)
            preds = self._dp.predict(features.iloc[-1:])
        except Exception as e:
            logger.error(f"AI pipeline failed: {e}")
            return None

        if not preds or not preds[-1]:
            return None

        # RL dual-signal (corrected fusion)
        rl_action = None
        if self._rl is not None:
            try:
                rl_action = self._rl.predict(features)
            except Exception:
                rl_action = None

        p = preds[-1]
        er = p["expected_return"]
        conf = p["confidence"]

        # RL veto (corrected: rl.predict returns list[dict])
        if rl_action:
            try:
                rl_er = rl_action[-1].get("expected_return", 0)
                rl_dir = "long" if rl_er > 0.001 else "short" if rl_er < -0.001 else None
            except (IndexError, AttributeError):
                rl_dir = None

            if rl_dir in ("long", "short"):
                lgbm_dir = "long" if er > 0.001 else "short" if er < -0.001 else "hold"
                if lgbm_dir != rl_dir and lgbm_dir != "hold":
                    logger.info(f"[DUAL] RL({rl_dir}) vs LGBM({lgbm_dir}) — RL overrides")
                    er = 0.003 if rl_dir == "long" else -0.003
                    conf = max(conf, 0.65)

        if conf < self.params["min_confidence"]:
            return {"action": "HOLD", "reason": f"conf {conf:.2f}<{self.params['min_confidence']}",
                    "confidence": conf, "expected_return": er}
        if abs(er) < self.params["min_signal"]:
            return {"action": "HOLD", "reason": "signal below noise floor",
                    "confidence": conf, "expected_return": er}

        # EMA50 trend filter — DISABLED per user request
        # e50 = df["close"].ewm(span=50).mean().iloc[-1]
        # pr = float(df["close"].iloc[-1])
        # if (er > 0 and pr < e50) or (er < 0 and pr > e50):
        #     return {"action": "HOLD", "reason": "counter-trend blocked (EMA50)",
        #             "confidence": conf, "expected_return": er}

        # Regime
        regime_list = self._rc.predict(features.iloc[-1:])
        regime = regime_list[-1] if regime_list and regime_list[-1] else "TRENDING_WEAK"

        atr_pct = float(features["atr_ratio"].iloc[-1]) if "atr_ratio" in features.columns else 0.015
        decision = self._arb.decide(
            account_equity=self.broker.total_equity(), current_positions=[],
            regime=regime, expected_return=er,
            confidence=conf, max_drawdown=p["max_drawdown"],
            atr_pct=atr_pct,
            adaptive_confidence=self.params["min_confidence"],
            adaptive_position_scalar=1.0,
        )

        # Override arbitrator output with aggressive params where applicable
        pos_pct = max(decision.position_size_pct, self.params["position_pct"])
        lev = max(decision.leverage, self.params["leverage"])
        sl = max(decision.stop_loss_pct, self.params["stop_loss_pct"])
        tp = max(decision.take_profit_pct, self.params["take_profit_pct"])
        return {
            "action": decision.action.value,
            "reason": decision.reason,
            "expected_return": er,
            "confidence": conf,
            "position_size_pct": pos_pct,
            "stop_loss_pct": sl,
            "take_profit_pct": tp,
            "leverage": lev,
            "regime": regime,
        }

    # ------------------------------------------------------------------
    # Trend pipeline (mode='trend') — uses trend_strategy
    # ------------------------------------------------------------------

    def _run_trend_pipeline(self, df: pd.DataFrame) -> Optional[dict]:
        """Run 4h trend strategy on resampled data."""
        if self._trend_strat is None:
            from engine.trend_strategy import TrendStrategy, TrendParams
            params = TrendParams(
                ema_fast=9, ema_slow=100, sl_atr_mult=2.0, tp_atr_mult=6.0,
                regime_filter=True, slope_confirm=True, trend_filter=True,
            )
            self._trend_strat = TrendStrategy(params)
            logger.info("Trend strategy initialized (4h, ema9/100, trend_filter)")

        # Resample to 4h if input is 1h
        if TIMEFRAME == "1h":
            df_4h = df.set_index("date").resample("4h").agg({
                "open": "first", "high": "max", "low": "min",
                "close": "last", "volume": "sum",
            }).dropna().reset_index()
        else:
            df_4h = df

        features = self._fe.compute_price_features(df_4h)
        signals = self._trend_strat.compute_signals(features)
        sig = signals[-1]

        if sig.action not in ("long", "short"):
            return {"action": "HOLD", "reason": sig.reason,
                    "confidence": 0.5, "expected_return": 0.0,
                    "regime": sig.regime}

        # Trend strategy gives direction; use ATR-based SL/TP from params
        return {
            "action": sig.action.upper(),
            "reason": sig.reason,
            "expected_return": 0.01 if sig.action == "long" else -0.01,
            "confidence": 0.65,  # trend signals get fixed confidence
            "position_size_pct": self.params["position_pct"],
            "stop_loss_pct": self._trend_strat.params.sl_atr_mult * float(features["atr_ratio"].iloc[-1] if "atr_ratio" in features.columns else 0.015),
            "take_profit_pct": self._trend_strat.params.tp_atr_mult * float(features["atr_ratio"].iloc[-1] if "atr_ratio" in features.columns else 0.015),
            "leverage": 2,  # conservative for trend mode
            "regime": sig.regime,
        }

    # ------------------------------------------------------------------
    # Breakout pipeline (mode='breakout') — Donchian channel breakout
    # ------------------------------------------------------------------

    def _init_breakout(self):
        if self._trend_strat is None:
            from engine.breakout_strategy import BreakoutStrategy, BreakoutParams
            params = BreakoutParams(donchian_period=20, atr_period=14,
                                    sl_atr_mult=2.0, tp_atr_mult=4.0,
                                    regime_filter=True, trend_filter=True)
            self._trend_strat = BreakoutStrategy(params)
            logger.info("Breakout strategy initialized (Donchian20)")

    def _run_breakout_pipeline(self, df: pd.DataFrame) -> Optional[dict]:
        self._init_breakout()
        try:
            features = self._fe.compute_price_features(df)
            sig = self._trend_strat.compute_signals(features)[-1]
        except Exception as e:
            logger.error(f"Breakout pipeline failed: {e}")
            return None
        if sig.action == "hold":
            return {"action": "HOLD", "reason": sig.reason,
                    "confidence": 0.5, "expected_return": 0.0, "regime": sig.regime}
        return {"action": sig.action.upper(), "reason": sig.reason,
                "expected_return": 0.015 if sig.action == "long" else -0.015,
                "confidence": 0.60, "position_size_pct": self.params["position_pct"],
                "stop_loss_pct": self.params["stop_loss_pct"],
                "take_profit_pct": self.params["take_profit_pct"],
                "leverage": 3, "regime": sig.regime}

    # ------------------------------------------------------------------
    # RL pipeline (mode='rl') — pure PPO reinforcement learning
    # ------------------------------------------------------------------

    def _run_rl_pipeline(self, df: pd.DataFrame) -> Optional[dict]:
        if self._rl is None:
            self._init_ai_pipeline()
        if self._rl is None or not self._rl.is_loaded:
            return {"action": "HOLD", "reason": "RL model not trained",
                    "confidence": 0.0, "expected_return": 0.0}
        try:
            features = self._fe.compute_price_features(df)
            preds = self._rl.predict(features)
        except Exception as e:
            logger.error(f"RL pipeline failed: {e}")
            return None
        if not preds:
            return {"action": "HOLD", "reason": "RL insufficient data",
                    "confidence": 0.0, "expected_return": 0.0}
        p = preds[-1]
        er, conf = p.get("expected_return", 0), p.get("confidence", 0.5)
        if conf < self.params["min_confidence"]:
            return {"action": "HOLD", "reason": f"RL conf {conf:.2f}",
                    "confidence": conf, "expected_return": er}
        if abs(er) < self.params["min_signal"]:
            return {"action": "HOLD", "reason": "RL signal below noise",
                    "confidence": conf, "expected_return": er}
        return {"action": "LONG" if er > 0 else "SHORT",
                "reason": f"RL PPO | er={er:.4f}", "expected_return": er,
                "confidence": conf, "position_size_pct": self.params["position_pct"],
                "stop_loss_pct": self.params["stop_loss_pct"],
                "take_profit_pct": self.params["take_profit_pct"],
                "leverage": self.params["leverage"], "regime": "UNKNOWN"}

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run_once(self):
        """One iteration: fetch data → pipeline → sim_broker."""
        for pair in PAIRS:
            df = fetch_ohlcv(pair, TIMEFRAME, limit=300)
            if df is None or len(df) < 60:
                logger.warning(f"{pair}: insufficient data ({len(df) if df is not None else 0} rows)")
                continue

            if self.mode == "ai":
                decision = self._run_ai_pipeline(df)
            elif self.mode == "trend":
                decision = self._run_trend_pipeline(df)
            elif self.mode == "breakout":
                decision = self._run_breakout_pipeline(df)
            elif self.mode == "rl":
                decision = self._run_rl_pipeline(df)
            else:
                decision = self._run_ai_pipeline(df)

            if decision is None:
                logger.warning(f"{pair}: pipeline returned None")
                continue

            action = decision["action"]

            # Log decision to DB
            self.db.log_decision({
                "pair": pair, "action": action,
                "confidence": decision.get("confidence", 0),
                "expected_return": decision.get("expected_return", 0),
                "position_size_pct": decision.get("position_size_pct", 0),
                "stop_loss_pct": decision.get("stop_loss_pct", 0),
                "take_profit_pct": decision.get("take_profit_pct", 0),
                "leverage": decision.get("leverage", self.params["leverage"]),
                "reason": decision.get("reason", ""),
                "executed": action.upper() in ("LONG", "SHORT"),
                "mode": self.mode,
                "aggressive": self.aggressive,
            })

            if action.upper() in ("LONG", "SHORT"):
                side = action.lower()
                price = float(df["close"].iloc[-1])
                logger.info(f"[SIGNAL] {pair} {action} @ ${price:,.2f} | {decision.get('reason', '')[:60]}")

                # Auto-close opposite position (flip)
                opposite = "short" if side == "long" else "long"
                for pid, pos in list(self.broker.open_positions.items()):
                    if pos.pair == pair and pos.side == opposite:
                        logger.info(f"[FLIP] {pair}: closing {opposite} #{pid} before opening {side}")
                        self.broker._close(pos, price, "signal_flip")

                order_decision = {
                    "pair": pair,
                    "side": side,
                    "position_size_pct": decision.get("position_size_pct", self.params["position_pct"]),
                    "leverage": decision.get("leverage", self.params["leverage"]),
                    "stop_loss_pct": decision.get("stop_loss_pct", self.params["stop_loss_pct"]),
                    "take_profit_pct": decision.get("take_profit_pct", self.params["take_profit_pct"]),
                    "confidence": decision.get("confidence", 0.5),
                    "reason": decision.get("reason", ""),
                    "mode": self.mode,
                    "aggressive": self.aggressive,
                }
                pos_id = self.broker.open_order(order_decision)
                if pos_id is None:
                    logger.info(f"  [REJECTED] {pair} {side} — broker declined")
            else:
                logger.info(f"[{action}] {pair} @ ${df['close'].iloc[-1]:,.2f} | {decision.get('reason', '')[:50]}")

        # Check SL/TP and snapshot after each cycle
        self.broker.check_positions()
        self.broker.snapshot_equity()
        logger.info(f"[HEARTBEAT] equity=${self.broker.total_equity():.2f} "
                    f"open={len(self.broker.open_positions)} mode={self.mode}")

    async def run_loop(self):
        """Main async loop: AI every 15min + SL/TP every 5s + snapshot every 30s."""
        logger.info(f"Live Trader started. Mode={self.mode} Aggressive={self.aggressive}")
        logger.info(f"Pairs={PAIRS} | Initial equity=${self.broker.config.initial_equity}")

        # Start background loops
        sl_tp_task = asyncio.create_task(self.broker.sl_tp_loop(interval=5.0))
        snap_task = asyncio.create_task(self.broker.snapshot_loop(interval=30.0))

        # Main AI decision loop
        try:
            while True:
                try:
                    self.run_once()
                except Exception as e:
                    logger.error(f"Loop error: {e}", exc_info=True)
                await asyncio.sleep(CHECK_INTERVAL_SECONDS)
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("Shutting down...")
        finally:
            sl_tp_task.cancel()
            snap_task.cancel()
            self.broker.stop()


# ============================================================================
# CLI
# ============================================================================


def main():
    parser = argparse.ArgumentParser(description="Live Trader v2.0 — SimBroker mode")
    parser.add_argument("--mode", choices=["ai", "trend", "breakout", "rl"], default="ai",
                        help="ai=1h AI pipeline, trend=4h trend strategy (default: ai)")
    parser.add_argument("--aggressive", action="store_true",
                        help="Aggressive risk params (lower thresholds, higher leverage)")
    parser.add_argument("--db", default="sim_trader.db", help="SQLite database path")
    parser.add_argument("--equity", type=float, default=1000.0, help="Initial equity (USDT)")
    args = parser.parse_args()

    print("=" * 60)
    print("  SIMULATION TRADER — Virtual money, real market data")
    print(f"  Mode: {args.mode} | Aggressive: {args.aggressive}")
    print(f"  Initial equity: ${args.equity}")
    print("  NEVER connects to live trading. OKX public data only.")
    print("=" * 60)

    trader = LiveTrader(
        mode=args.mode,
        aggressive=args.aggressive,
        db_path=args.db,
        initial_equity=args.equity,
    )
    asyncio.run(trader.run_loop())


if __name__ == "__main__":
    main()
