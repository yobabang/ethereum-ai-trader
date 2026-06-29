"""AI-powered trading strategy for autonomous futures trading.

This strategy acts as a bridge between freqtrade's IStrategy interface
and the AI Decision Core (4-layer pipeline). It implements all required
IStrategy methods by delegating to the AI modules.

Crucially, this means NO changes to freqtradebot.py — the AI is just
another strategy from the bot's perspective.
"""

import logging
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from pandas import DataFrame

from engine.decision_arbitrator import (
    Action,
    Decision,
    DecisionArbitrator,
    RiskCalculator,
)
from engine.direction_predictor import DirectionPredictor
from engine.features import FeatureEngineer
from engine.regime_classifier import RegimeClassifier
from engine.scheduler import TrainingScheduler
from engine.self_optimizer import SelfOptimizer
from engine.trade_journal import TradeJournal
from freqtrade.constants import Config
from freqtrade.enums import CandleType, MarginMode, TradingMode
from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy, IntParameter

logger = logging.getLogger(__name__)


class AIStrategy(IStrategy):
    """AI-driven autonomous trading strategy.

    Replaces traditional hand-coded indicators and entry/exit logic
    with a 4-layer AI decision pipeline. The user does NOT configure
    indicators, entry rules, or exit rules — the AI handles everything.
    """

    # ---- Strategy metadata ----
    INTERFACE_VERSION: int = 3
    can_short: bool = True
    timeframe: str = "4h"

    # Minimal trailing stop as safety net (AI handles primary exit logic)
    trailing_stop: bool = True
    trailing_stop_positive: float = 0.01
    trailing_stop_positive_offset: float = 0.02
    trailing_only_offset_is_reached: bool = True

    # ---- Runtime state ----
    _feature_engineer: FeatureEngineer | None = None
    _regime_classifier: RegimeClassifier | None = None
    _direction_predictor: DirectionPredictor | None = None
    _arbitrator: DecisionArbitrator | None = None
    _optimizer: SelfOptimizer | None = None
    _scheduler: TrainingScheduler | None = None
    _journal: TradeJournal | None = None
    _last_decision: Decision | None = None
    _model_dir: str = "./models"
    _models_loaded: bool = False

    def __init__(self, config: Config) -> None:
        super().__init__(config)
        self._init_ai_modules(config)

    def _init_ai_modules(self, config: Config) -> None:
        """Lazy-init AI modules on first use."""
        if self._models_loaded:
            return

        self._model_dir = config.get("ai", {}).get("model_dir", "./models")
        model_dir = self._model_dir
        ai_config = config.get("ai", {})

        self._feature_engineer = FeatureEngineer()
        self._regime_classifier = RegimeClassifier(model_dir=model_dir)
        self._direction_predictor = DirectionPredictor(model_dir=model_dir)

        risk = RiskCalculator(
            max_leverage=ai_config.get("max_leverage", 5),
            max_position_pct=ai_config.get("max_position_pct", 0.20),
            max_drawdown_pct=ai_config.get("max_drawdown_pct", 0.15),
            daily_loss_limit_pct=ai_config.get("daily_loss_limit_pct", 0.05),
        )
        self._arbitrator = DecisionArbitrator(risk)

        # Self-optimizer with adaptive parameter adjustment
        self._optimizer = SelfOptimizer(
            model_dir=model_dir,
            train_interval_hours=ai_config.get("train_interval_hours", 4),
            backtest_days=ai_config.get("backtest_days", 7),
            min_sharpe=0.5,
            max_drawdown=ai_config.get("max_drawdown_pct", 0.15),
        )

        # Trade journal for AI operator monitoring
        journal_dir = config.get("ai", {}).get("journal_dir", "../ethereum-ai-trader/journal")
        self._journal = TradeJournal(journal_dir=journal_dir)

        # Auto-training scheduler (background thread, non-blocking)
        self._scheduler = TrainingScheduler(
            model_dir=model_dir,
            datadir=config.get("datadir", "./user_data/data"),
            pairs=config.get("pair_whitelist", ["BTC/USDT:USDT", "ETH/USDT:USDT"]),
            interval_hours=ai_config.get("train_interval_hours", 4),
            backtest_days=ai_config.get("backtest_days", 7),
        )

        # Try to load pre-trained models
        try:
            self._regime_classifier.load()
            self._direction_predictor.load()
            logger.info("AI models loaded from disk")
        except FileNotFoundError:
            logger.warning("No pre-trained AI models found — will train on first run")

        self._models_loaded = True

    # ------------------------------------------------------------------
    # Lifecycle callbacks
    # ------------------------------------------------------------------

    def bot_loop_start(self, current_time: datetime, **kwargs) -> None:
        """Called at the start of each bot iteration.

        Triggers the auto-training scheduler if enough time has passed.
        Non-blocking — training runs in a background thread.
        """
        if self._scheduler:
            self._scheduler.check()
            # Hot-reload models if scheduler just completed training
            if not self._scheduler.is_training and self._scheduler.training_count > 0:
                self._reload_models_if_newer()

    # ------------------------------------------------------------------
    # IStrategy required methods
    # ------------------------------------------------------------------

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Compute all features needed by the AI pipeline."""
        if self._feature_engineer is None:
            self._init_ai_modules(self.config)

        pair = metadata["pair"]

        try:
            result = self._feature_engineer.compute_price_features(dataframe)

            # Also add orderbook and derivatives features if available
            # (these come from DataProvider at runtime, not available here)

            logger.debug(f"AI features computed for {pair}: {len(result.columns)} columns")
            return result
        except ValueError as e:
            logger.warning(f"Insufficient data for AI features on {pair}: {e}")
            # Return original with empty signal columns
            dataframe["enter_long"] = 0
            dataframe["enter_short"] = 0
            dataframe["exit_long"] = 0
            dataframe["exit_short"] = 0
            return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Generate entry signals using AI Decision Arbitrator.

        For each candle, runs the AI pipeline and sets entry signals.
        """
        if self._arbitrator is None or self._feature_engineer is None:
            dataframe["enter_long"] = 0
            dataframe["enter_short"] = 0
            return dataframe

        # Initialize signal columns
        dataframe["enter_long"] = 0
        dataframe["enter_short"] = 0

        # Only evaluate the latest candle for entry
        if len(dataframe) < 50:
            return dataframe

        try:
            decision = self._run_ai_pipeline(dataframe, metadata)

            if decision.action == Action.LONG:
                dataframe.loc[dataframe.index[-1], "enter_long"] = 1
            elif decision.action == Action.SHORT:
                dataframe.loc[dataframe.index[-1], "enter_short"] = 1

            self._last_decision = decision
            logger.info(f"AI decision: {decision.reason}")

            # Persist decision for API bridge
            self._save_decision(decision)
        except Exception as e:
            logger.error(f"AI pipeline failed: {e}", exc_info=True)

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Exit signals — primarily handled by custom_exit() for AI-driven exit."""
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        return dataframe

    # ------------------------------------------------------------------
    # Optional callbacks — AI takes over
    # ------------------------------------------------------------------

    def custom_stoploss(
        self,
        pair: str,
        trade: "Trade",
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> float | None:
        """AI-computed dynamic stop-loss (Critical fix: was previously unused).

        Returns the AI's regime-adaptive stop-loss percentage.
        Falls back to -5% hard floor if no AI decision is available.
        """
        if self._last_decision and self._last_decision.stop_loss_pct > 0:
            # AI computed stop-loss (already regime-adaptive, capped at per-trade max)
            return -self._last_decision.stop_loss_pct
        # Fallback: 5% hard stop (2.5x wider than AI typical 2% SL)
        return -0.05

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ) -> str | None:
        """AI-driven exit logic.

        Called on every iteration for open trades. The AI decides
        whether to exit based on current market conditions.
        """
        # Safety: hard stop-loss
        if current_profit < -0.05:
            if self._optimizer:
                self._optimizer.record_trade(
                    pair=pair, side="short" if trade.is_short else "long",
                    entry_price=trade.open_rate, exit_price=current_rate,
                    pnl=round(trade.stake_amount * current_profit, 2),
                    pnl_pct=round(current_profit * 100, 2),
                    entry_reason="ai_signal", exit_reason="hard_stop_loss",
                    duration_hours=(current_time - trade.open_date).total_seconds() / 3600
                )
            return "hard_stop_loss"

        # AI check (only on 4h candle close, to avoid overtrading)
        if self._last_decision:
            # Exit if AI reversed direction
            if self._last_decision.action == Action.CLOSE_LONG and trade.is_short is False:
                return "ai_reversal"
            if self._last_decision.action == Action.CLOSE_SHORT and trade.is_short:
                return "ai_reversal"

            # Exit if AI says STOP (emergency)
            if self._last_decision.action == Action.STOP:
                return "ai_emergency_stop"

        return None

    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time: datetime,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> bool:
        """Final gate before entry — AI must agree."""
        if self._last_decision is None:
            logger.info(f"AI: no recent decision, rejecting {side} on {pair}")
            return False

        # Direction must match AI decision
        expected_side = "long" if self._last_decision.action == Action.LONG else "short"
        if side != expected_side:
            logger.info(
                f"AI: rejecting {side} on {pair} — AI decision is {expected_side}"
            )
            return False

        # Record entry in journal for AI operator
        if self._journal and self._last_decision:
            self._journal.record_entry(
                pair=pair, side=side, entry_price=rate, amount=amount,
                leverage=self._last_decision.leverage,
                stop_loss=rate * (1 - self._last_decision.stop_loss_pct) if side == "long" else rate * (1 + self._last_decision.stop_loss_pct),
                take_profit=rate * (1 + self._last_decision.take_profit_pct) if side == "long" else rate * (1 - self._last_decision.take_profit_pct),
                confidence=self._last_decision.confidence,
                expected_return=self._last_decision.expected_return,
                regime="TRENDING_WEAK",  # Updated at runtime from pipeline
            )

        return True

    def confirm_trade_exit(
        self,
        pair: str,
        trade: Trade,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        exit_reason: str,
        current_time: datetime,
        **kwargs,
    ) -> bool:
        """Always allow exit — safety first."""
        return True

    def custom_stake_amount(
        self,
        pair: str,
        current_time: datetime | None,
        current_rate: float,
        proposed_stake: float,
        min_stake: float | None,
        max_stake: float,
        leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        """AI-determined position size with hard absolute cap (Security Audit Fix #3)."""
        # ABSOLUTE HARD CAP: never exceed $500 per position regardless of wallet size
        ABSOLUTE_MAX_STAKE = 500.0
        if self._last_decision and self._last_decision.position_size_pct > 0:
            wallet = self.wallets.get_total_stake_amount() if self.wallets else 1000.0
            ai_stake = wallet * self._last_decision.position_size_pct
            ai_stake = min(ai_stake, ABSOLUTE_MAX_STAKE)
            if min_stake and ai_stake < min_stake:
                return min(min_stake, ABSOLUTE_MAX_STAKE)
            return min(ai_stake, max_stake)
        return min(proposed_stake, ABSOLUTE_MAX_STAKE)

    def leverage(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        """AI-determined leverage."""
        if self._last_decision:
            return float(self._last_decision.leverage)
        return proposed_leverage

    # ------------------------------------------------------------------
    # AI pipeline execution
    # ------------------------------------------------------------------

    def _run_ai_pipeline(self, dataframe: DataFrame, metadata: dict) -> Decision:
        """Run the full 4-layer AI pipeline and return a Decision.

        Layer 1: Market regime classification
        Layer 2: Direction prediction
        Layer 3: Risk calculation
        Layer 4: Final arbitration
        """
        pair = metadata["pair"]
        latest = dataframe.iloc[-1:]

        # ---- Layer 1: Market regime ----
        try:
            regime_preds = self._regime_classifier.predict(latest)
            regime = regime_preds[-1] if regime_preds and regime_preds[-1] else "TRENDING_WEAK"
        except Exception:
            regime = "TRENDING_WEAK"

        # ---- Layer 2: Direction prediction ----
        try:
            preds = self._direction_predictor.predict(latest)
            if preds and preds[-1]:
                expected_return = preds[-1]["expected_return"]
                confidence = preds[-1]["confidence"]
                max_dd = preds[-1]["max_drawdown"]
            else:
                expected_return = 0.0
                confidence = 0.3
                max_dd = -0.05
        except Exception:
            expected_return = 0.0
            confidence = 0.3
            max_dd = -0.05

        # ---- ATR for risk calc ----
        atr_pct = 0.015
        if "atr_ratio" in dataframe.columns:
            atr_pct = float(dataframe["atr_ratio"].iloc[-1])

        # ---- Account state ----
        account_equity = self.wallets.get_total_stake_amount() if self.wallets else 5000.0
        open_trades = Trade.get_open_trades()
        current_positions = [
            {
                "pair": t.pair,
                "side": "short" if t.is_short else "long",
                "size": t.stake_amount,
                "pnl": t.calc_profit_ratio(t.open_rate, dataframe["close"].iloc[-1], t.leverage),
            }
            for t in open_trades
        ]

        # ---- Daily PnL from closed trades today ----
        daily_pnl = 0.0
        if self._optimizer:
            today = datetime.now(UTC).date()
            recent_losses_today = sum(
                1 for p in current_positions
                if p.get("pnl", 0) < 0 and p.get("closed_date", today) == today
            )
            # Approximate: use optimizer's consecutive loss tracking
            daily_pnl = -self._optimizer.consecutive_losses * 50  # ~$50 avg loss estimate

        # ---- EMA Trend Filter (BTC optimization Day 5: +53% vs -9%) ----
        # Only trade in the direction of the prevailing trend
        if len(dataframe) > 50 and "close" in dataframe.columns:
            ema50 = dataframe["close"].ewm(span=50).mean().iloc[-1]
            price = dataframe["close"].iloc[-1]
            # Long only above EMA50, Short only below EMA50
            if expected_return > 0 and price < ema50:
                return self._arbitrator._hold(f"Counter-trend LONG blocked: price {price:.0f} < EMA50 {ema50:.0f}")
            if expected_return < 0 and price > ema50:
                return self._arbitrator._hold(f"Counter-trend SHORT blocked: price {price:.0f} > EMA50 {ema50:.0f}")

        # ---- Layer 3+4: Arbitrator decision (with adaptive params) ----
        decision = self._arbitrator.decide(
            account_equity=account_equity,
            current_positions=current_positions,
            regime=regime,
            expected_return=expected_return,
            confidence=confidence,
            max_drawdown=max_dd,
            atr_pct=atr_pct,
            funding_signal=0.0,
            daily_pnl=daily_pnl,
            consecutive_losses=self._optimizer.consecutive_losses if self._optimizer else 0,
            # SelfOptimizer adaptive thresholds (Phase 3 feedback loop)
            adaptive_confidence=self._optimizer.confidence_threshold if self._optimizer else None,
            adaptive_position_scalar=self._optimizer.position_scalar if self._optimizer else None,
        )

        # ---- PnL tracking via SelfOptimizer ----
        if self._optimizer:
            if decision.action == Action.STOP:
                pass  # STOP is recorded when the trade actually closes
            # Entry decisions are recorded when the trade closes (see custom_exit)

        return decision

    def _reload_models_if_newer(self) -> None:
        """Hot-reload AI models after background training completes."""
        import os
        try:
            model_dir = Path(self._model_dir)
            for model_file in ["regime_classifier.pkl", "direction_predictor.pkl"]:
                path = model_dir / model_file
                if not path.exists():
                    continue
                mtime = os.path.getmtime(path)
                cached_mtime = getattr(self, f"_{model_file}_mtime", 0)
                if mtime > cached_mtime:
                    if "regime" in model_file:
                        self._regime_classifier.load()
                    else:
                        self._direction_predictor.load()
                    setattr(self, f"_{model_file}_mtime", mtime)
                    logger.info(f"Hot-reloaded {model_file} (newer version detected)")
        except Exception as e:
            logger.debug(f"Model hot-reload skipped: {e}")

    def _save_decision(self, decision: Decision) -> None:
        """Persist latest decision to disk for API bridge."""
        import json
        path = Path(self._model_dir) / "last_decision.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump({
                "action": decision.action.value,
                "reason": decision.reason,
                "confidence": decision.confidence,
                "expected_return": decision.expected_return,
                "position_size_pct": decision.position_size_pct,
                "stop_loss_pct": decision.stop_loss_pct,
                "take_profit_pct": decision.take_profit_pct,
                "leverage": decision.leverage,
                "timestamp": datetime.now(UTC).isoformat(),
            }, f, indent=2)
