"""AI Backtest Adapter — Phase 4.

Walks the AI decision pipeline through historical OHLCV data
candle-by-candle, simulating trades with stop-loss, take-profit,
leverage, and funding fees. Produces a full backtest report.

This is a lightweight standalone backtester — it does NOT depend
on freqtrade's Backtesting class. Instead it directly calls the
AI modules on each candle, which is what we need to validate.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from engine.decision_arbitrator import Action

logger = logging.getLogger(__name__)


@dataclass
class SimPosition:
    """A simulated position during backtesting."""

    pair: str
    side: str  # "long" | "short"
    entry_idx: int
    entry_price: float
    entry_time: pd.Timestamp
    amount: float  # in base currency (BTC/ETH)
    leverage: int
    stop_loss: float
    take_profit: float
    exit_idx: int = -1
    exit_price: float = 0.0
    exit_time: pd.Timestamp | None = None
    pnl: float = 0.0
    pnl_pct: float = 0.0
    exit_reason: str = ""


@dataclass
class BacktestResult:
    """Full backtest report."""

    # Core metrics
    total_return: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0

    # Trade stats
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0
    avg_duration_hours: float = 0.0

    # Risk
    max_consecutive_losses: int = 0
    daily_sharpe: float = 0.0

    # Equity curve
    equity_curve: list[float] = field(default_factory=list)
    drawdown_curve: list[float] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)

    # Meta
    start_date: str = ""
    end_date: str = ""
    candles_processed: int = 0
    pass_criteria: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "total_return_pct": round(self.total_return * 100, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 3),
            "sortino_ratio": round(self.sortino_ratio, 3),
            "max_drawdown_pct": round(self.max_drawdown * 100, 2),
            "win_rate_pct": round(self.win_rate * 100, 1),
            "profit_factor": round(self.profit_factor, 2),
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "avg_win": round(self.avg_win, 2),
            "avg_loss": round(self.avg_loss, 2),
            "best_trade": round(self.best_trade, 2),
            "worst_trade": round(self.worst_trade, 2),
            "max_consecutive_losses": self.max_consecutive_losses,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "candles_processed": self.candles_processed,
            "pass_criteria": self.pass_criteria,
        }


class AIBacktestAdapter:
    """Standalone backtester for the AI trading pipeline.

    Usage:
        adapter = AIBacktestAdapter(model_dir="./models")
        result = adapter.run(ohlcv_df, pair="BTC/USDT:USDT")
        print(result.to_dict())
    """

    def __init__(
        self,
        model_dir: str = "./models",
        initial_equity: float = 5000.0,
        max_position_pct: float = 0.20,
        max_leverage: int = 5,
        funding_rate: float = 0.0001,  # 0.01% per 8h
        trading_fee: float = 0.0005,  # 0.05% taker
    ):
        self.model_dir = model_dir
        self.initial_equity = initial_equity
        self.max_position_pct = max_position_pct
        self.max_leverage = max_leverage
        self.funding_rate = funding_rate
        self.trading_fee = trading_fee

    # ------------------------------------------------------------------
    # Main backtest loop
    # ------------------------------------------------------------------

    def run(
        self,
        ohlcv: pd.DataFrame,
        pair: str = "BTC/USDT:USDT",
        warmup: int = 50,
        derivatives: pd.DataFrame | None = None,
    ) -> BacktestResult:
        """Run backtest on historical OHLCV data.

        Args:
            ohlcv: DataFrame with [open, high, low, close, volume].
            pair: Trading pair name.
            warmup: Candles to skip for indicator warmup.
            derivatives: Optional per-candle derivatives DataFrame. When given,
                funding/OI/long-short features are merged in, and the real
                funding_rate (if present) drives both funding-fee cost and the
                safety rule 5 (extreme funding → reject counter-crowd direction).

        Returns:
            BacktestResult with full metrics.
        """
        # ---- Compute features (merge derivatives if provided) ----
        from engine.features import FeatureEngineer

        fe = FeatureEngineer()
        features = fe.compute_all_features(ohlcv, derivatives)
        logger.info(f"Features computed: {len(features)} rows, {len(features.columns)} cols")

        # Pre-extract funding signal series for the safety rule (None if no derivatives)
        has_funding = "funding_signal" in features.columns
        funding_signal_series = features["funding_signal"] if has_funding else None
        funding_rate_series = features["funding_rate"] if "funding_rate" in features.columns else None
        if has_funding:
            logger.info("Derivatives merged — funding_signal safety rule ACTIVE")

        # ---- Load models ----
        from engine.direction_predictor import DirectionPredictor
        from engine.regime_classifier import RegimeClassifier

        rc = RegimeClassifier(model_dir=self.model_dir)
        dp = DirectionPredictor(model_dir=self.model_dir)
        try:
            rc.load()
            dp.load()
            logger.info("Models loaded for backtest")
        except FileNotFoundError:
            logger.warning("No trained models — training on backtest data")
            rc.train(features)
            dp.train(features)

        # ---- Initialize ----
        equity = self.initial_equity
        equity_curve: list[float] = [equity]
        drawdown_curve: list[float] = [0.0]
        peak = equity

        open_positions: list[SimPosition] = []
        closed_trades: list[SimPosition] = []
        daily_returns: list[float] = []

        prev_close = equity
        # Use ohlcv 'date' column directly for daily tracking (robust)
        date_series = pd.to_datetime(ohlcv['date']) if 'date' in ohlcv.columns else None
        current_day = date_series.iloc[warmup].date() if date_series is not None else None

        # Ensure features index is compatible
        if date_series is not None:
            features.index = date_series

        # ---- Candle-by-candle simulation ----
        for i in range(warmup, len(features) - 1):
            row = features.iloc[i : i + 1]
            date = features.index[i]
            price = ohlcv["close"].iloc[i]
            high_price = ohlcv["high"].iloc[i]
            low_price = ohlcv["low"].iloc[i]
            next_open = ohlcv["open"].iloc[i + 1]
            atr_pct = features["atr_ratio"].iloc[i] if "atr_ratio" in features.columns else 0.015

            # ---- Track daily P&L ----
            day = date.date() if hasattr(date, 'date') else None
            if current_day and day and day != current_day:
                daily_return = (equity - prev_close) / prev_close if prev_close > 0 else 0
                daily_returns.append(daily_return)
                prev_close = equity
                current_day = day

            # ---- Check existing positions ----
            positions_to_close: list[SimPosition] = []
            for pos in open_positions:
                exit_price = None
                exit_reason = ""

                # Stop-loss hit?
                if pos.side == "long":
                    if low_price <= pos.stop_loss:
                        exit_price = pos.stop_loss
                        exit_reason = "stop_loss"
                    elif high_price >= pos.take_profit:
                        exit_price = pos.take_profit
                        exit_reason = "take_profit"
                else:  # short
                    if high_price >= pos.stop_loss:
                        exit_price = pos.stop_loss
                        exit_reason = "stop_loss"
                    elif low_price <= pos.take_profit:
                        exit_price = pos.take_profit
                        exit_reason = "take_profit"

                if exit_price:
                    pos.exit_idx = i
                    pos.exit_price = exit_price
                    pos.exit_time = date
                    pos.exit_reason = exit_reason

                    # Calculate P&L
                    if pos.side == "long":
                        pos.pnl_pct = (exit_price / pos.entry_price - 1) * pos.leverage
                    else:
                        pos.pnl_pct = (1 - exit_price / pos.entry_price) * pos.leverage

                    # Deduct fees (entry + exit)
                    pos.pnl_pct -= self.trading_fee * 2 * pos.leverage
                    pos.pnl = pos.amount * pos.entry_price * pos.pnl_pct

                    equity += pos.pnl
                    positions_to_close.append(pos)

            for pos in positions_to_close:
                open_positions.remove(pos)
                closed_trades.append(pos)

            # ---- Funding fee (every 8h on 1h data = every 8 candles; use real rate if available) ----
            if i % 8 == 0:
                # Real funding rate from derivatives if present, else fallback to constant
                fr = (funding_rate_series.iloc[i]
                      if funding_rate_series is not None and pd.notna(funding_rate_series.iloc[i])
                      else self.funding_rate)
                for pos in open_positions:
                    # Long pays funding when rate>0, short pays when rate<0
                    sign = 1.0 if pos.side == "long" else -1.0
                    funding_cost = pos.amount * price * fr * pos.leverage * sign
                    equity -= funding_cost

            # ---- AI Decision for new entry ----
            if len(open_positions) < 2:  # Max 2 concurrent positions
                regime_preds = rc.predict(row)
                regime = regime_preds[-1] if regime_preds and regime_preds[-1] else "RANGING_WIDE"

                preds = dp.predict(row)
                if preds and preds[-1]:
                    er = preds[-1]["expected_return"]
                    conf = preds[-1]["confidence"]
                    mdd = preds[-1]["max_drawdown"]
                else:
                    er, conf, mdd = 0.0, 0.3, -0.05

                # Simplified arbitrator check
                direction = "long" if er > 0.002 else "short" if er < -0.002 else None

                # Safety rule 5: extreme funding rejects the crowded direction.
                # funding_signal > 2 → longs crowded → reject long
                # funding_signal < -2 → shorts crowded → reject short
                if direction and funding_signal_series is not None:
                    fsig = funding_signal_series.iloc[i]
                    if pd.notna(fsig):
                        if fsig > 2.0 and direction == "long":
                            direction = None  # extreme positive funding, reject long
                        elif fsig < -2.0 and direction == "short":
                            direction = None  # extreme negative funding, reject short

                if direction and conf >= 0.55 and regime != "HIGH_VOLATILITY":
                    # Check no same-direction losing position
                    has_losing_same = any(
                        p.side == direction and p.pnl_pct < 0 for p in open_positions
                    )
                    if not has_losing_same:
                        # Open position
                        sl_pct = max(atr_pct * 1.5, 0.005)
                        tp_pct = sl_pct * 2.0
                        leverage = min(5, max(2, int(3 * conf)))

                        position_size = equity * self.max_position_pct * conf
                        amount = (position_size * leverage) / price

                        if direction == "long":
                            sl_price = price * (1 - sl_pct)
                            tp_price = price * (1 + tp_pct)
                        else:
                            sl_price = price * (1 + sl_pct)
                            tp_price = price * (1 - tp_pct)

                        open_positions.append(
                            SimPosition(
                                pair=pair,
                                side=direction,
                                entry_idx=i,
                                entry_price=price,
                                entry_time=date,
                                amount=amount,
                                leverage=leverage,
                                stop_loss=sl_price,
                                take_profit=tp_price,
                            )
                        )

            # ---- Update equity curve ----
            unrealized_pnl = 0.0
            for pos in open_positions:
                if pos.side == "long":
                    unrealized_pnl += pos.amount * (price - pos.entry_price) * pos.leverage
                else:
                    unrealized_pnl += pos.amount * (pos.entry_price - price) * pos.leverage

            total_equity = equity + unrealized_pnl
            equity_curve.append(total_equity)

            if total_equity > peak:
                peak = total_equity
            dd = (peak - total_equity) / peak if peak > 0 else 0.0
            drawdown_curve.append(dd)

        # ---- Compile results ----
        result = BacktestResult()
        result.candles_processed = len(features) - warmup
        result.start_date = str(features.index[warmup])
        result.end_date = str(features.index[-1])

        # Trade metrics
        if closed_trades:
            result.total_trades = len(closed_trades)
            wins = [t for t in closed_trades if t.pnl > 0]
            losses = [t for t in closed_trades if t.pnl <= 0]
            result.winning_trades = len(wins)
            result.losing_trades = len(losses)
            result.win_rate = len(wins) / len(closed_trades)
            result.avg_win = np.mean([t.pnl for t in wins]) if wins else 0.0
            result.avg_loss = abs(np.mean([t.pnl for t in losses])) if losses else 0.0
            result.best_trade = max(t.pnl for t in closed_trades)
            result.worst_trade = min(t.pnl for t in closed_trades)

            # Profit factor
            gross_profit = sum(t.pnl for t in wins)
            gross_loss = abs(sum(t.pnl for t in losses)) if losses else 1e-10
            result.profit_factor = gross_profit / gross_loss

            # Duration
            durations = [(t.exit_idx - t.entry_idx) for t in closed_trades]
            result.avg_duration_hours = np.mean(durations) * 4  # 4h candles

            # Consecutive losses
            max_consec = 0
            current_consec = 0
            for t in closed_trades:
                if t.pnl <= 0:
                    current_consec += 1
                    max_consec = max(max_consec, current_consec)
                else:
                    current_consec = 0
            result.max_consecutive_losses = max_consec

        # Total return
        result.total_return = (equity - self.initial_equity) / self.initial_equity

        # Max drawdown
        result.max_drawdown = max(drawdown_curve) if drawdown_curve else 0.0

        # Sharpe (from daily returns)
        if len(daily_returns) >= 2:
            dr = np.array(daily_returns)
            result.sharpe_ratio = float(np.mean(dr) / (np.std(dr) + 1e-10) * np.sqrt(365))

            # Sortino (downside only)
            downside = dr[dr < 0]
            if len(downside) > 0 and np.std(downside) > 0:
                result.sortino_ratio = float(np.mean(dr) / np.std(downside) * np.sqrt(365))

        # Store curves
        result.equity_curve = equity_curve
        result.drawdown_curve = drawdown_curve

        # Trades for logging
        result.trades = [
            {
                "pair": t.pair,
                "side": t.side,
                "entry_time": str(t.entry_time),
                "exit_time": str(t.exit_time),
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "pnl": round(t.pnl, 2),
                "pnl_pct": round(t.pnl_pct * 100, 2),
                "exit_reason": t.exit_reason,
                "leverage": t.leverage,
            }
            for t in closed_trades
        ]

        # Pass/fail against SPEC minimums
        result.pass_criteria = {
            "sharpe_gt_0.5": result.sharpe_ratio > 0.5,
            "max_drawdown_lt_15pct": result.max_drawdown < 0.15,
            "win_rate_gt_40pct": result.win_rate > 0.40,
            "profit_factor_gt_1.5": result.profit_factor > 1.5,
        }

        passed = all(result.pass_criteria.values())
        logger.info(
            f"Backtest complete: {result.total_trades} trades, "
            f"Sharpe={result.sharpe_ratio:.3f}, MaxDD={result.max_drawdown:.2%}, "
            f"WinRate={result.win_rate:.1%} — {'PASS' if passed else 'FAIL'}"
        )

        return result
