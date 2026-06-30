"""Standalone backtester for the rule-based trend strategy (Plan D).

Candle-by-candle simulation with ATR stop-loss / take-profit, trading fees,
and funding cost. No ML dependency. Produces the same metric set as
backtest_adapter so results are comparable.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from engine.trend_strategy import TrendParams, TrendStrategy

logger = logging.getLogger(__name__)


@dataclass
class TPosition:
    """An open simulated position."""
    side: str               # "long" | "short"
    entry_idx: int
    entry_price: float
    entry_time: pd.Timestamp
    amount: float           # base currency
    leverage: int
    stop_loss: float
    take_profit: float
    entry_atr: float
    exit_idx: int = -1
    exit_price: float = 0.0
    exit_time: Optional[pd.Timestamp] = None
    pnl: float = 0.0
    pnl_pct: float = 0.0
    exit_reason: str = ""


@dataclass
class TrendResult:
    total_return: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0
    avg_duration_hours: float = 0.0
    max_consecutive_losses: int = 0
    equity_curve: list = field(default_factory=list)
    trades: list = field(default_factory=list)
    start_date: str = ""
    end_date: str = ""
    candles_processed: int = 0

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
            "avg_duration_hours": round(self.avg_duration_hours, 1),
            "start_date": self.start_date,
            "end_date": self.end_date,
            "candles_processed": self.candles_processed,
        }


class TrendBacktest:
    """Backtest the TrendStrategy on OHLCV data.

    Usage:
        bt = TrendBacktest(initial_equity=5000, leverage=3, trading_fee=0.0005)
        result = bt.run(ohlcv, params=TrendParams(ema_fast=21, ema_slow=50))
    """

    def __init__(
        self,
        initial_equity: float = 5000.0,
        position_pct: float = 0.30,   # 30% equity per trade (trend needs room)
        leverage: int = 3,
        trading_fee: float = 0.0005,  # 0.05% taker
        slippage: float = 0.0005,     # 0.05%
        funding_rate: float = 0.0001, # 0.01% per 8h
    ):
        self.initial_equity = initial_equity
        self.position_pct = position_pct
        self.leverage = leverage
        self.trading_fee = trading_fee
        self.slippage = slippage
        self.funding_rate = funding_rate

    def run(self, ohlcv: pd.DataFrame, params: Optional[TrendParams] = None,
            strategy=None, warmup: int = 100) -> TrendResult:
        """Run candle-by-candle backtest.

        Args:
            ohlcv: DataFrame with date/open/high/low/close/volume.
            params: TrendParams (default if None). Ignored if `strategy` is given.
            strategy: Optional pre-built strategy object (must implement
                compute_signals / compute_sl_tp / should_exit). Lets this
                backtester run any rule-based strategy, not just TrendStrategy.
            warmup: bars to skip for indicator warmup.
        """
        from engine.features import FeatureEngineer

        fe = FeatureEngineer()
        features = fe.compute_price_features(ohlcv)

        if strategy is None:
            params = params or TrendParams()
            strategy = TrendStrategy(params)
        signals = strategy.compute_signals(features)

        equity = self.initial_equity
        equity_curve = [equity]
        peak = equity
        max_dd = 0.0

        open_pos: Optional[TPosition] = None  # single position only
        closed: list[TPosition] = []
        daily_returns: list[float] = []
        prev_equity_close = equity

        date_series = pd.to_datetime(ohlcv["date"]) if "date" in ohlcv.columns else None
        current_day = date_series.iloc[warmup].date() if date_series is not None else None

        if date_series is not None:
            features.index = date_series

        for i in range(warmup, len(features) - 1):
            date = features.index[i]
            high = float(ohlcv["high"].iloc[i])
            low = float(ohlcv["low"].iloc[i])
            close = float(ohlcv["close"].iloc[i])
            sig = signals[i]

            # ---- Track daily returns ----
            day = date.date() if hasattr(date, "date") else None
            if current_day and day and day != current_day:
                dr = (equity - prev_equity_close) / prev_equity_close if prev_equity_close > 0 else 0
                daily_returns.append(dr)
                prev_equity_close = equity
                current_day = day

            # ---- Manage open position: check SL/TP/reversal ----
            if open_pos is not None:
                exit_price = None
                exit_reason = ""
                # Stop-loss / take-profit hit (intrabar, conservative: use high/low)
                if open_pos.side == "long":
                    if low <= open_pos.stop_loss:
                        exit_price, exit_reason = open_pos.stop_loss, "stop_loss"
                    elif high >= open_pos.take_profit:
                        exit_price, exit_reason = open_pos.take_profit, "take_profit"
                else:
                    if high >= open_pos.stop_loss:
                        exit_price, exit_reason = open_pos.stop_loss, "stop_loss"
                    elif low <= open_pos.take_profit:
                        exit_price, exit_reason = open_pos.take_profit, "take_profit"

                # Trend reversal / max hold (decided at bar close)
                if exit_price is None:
                    rev_exit, rev_reason = strategy.should_exit(open_pos, sig, i)
                    if rev_exit:
                        exit_price, exit_reason = close, rev_reason

                if exit_price is not None:
                    self._close_position(open_pos, exit_price, i, date, exit_reason, equity)
                    equity += open_pos.pnl
                    closed.append(open_pos)
                    open_pos = None

            # ---- Funding fee every 8 bars (8h on 1h data) ----
            if i % 8 == 0 and open_pos is not None:
                sign = 1.0 if open_pos.side == "long" else -1.0
                equity -= open_pos.amount * close * self.funding_rate * open_pos.leverage * sign

            # ---- New entry (only if flat) ----
            if open_pos is None and sig.action in ("long", "short") and sig.atr > 0:
                entry_price = close * (1 + self.slippage) if sig.action == "long" else close * (1 - self.slippage)
                sl, tp = strategy.compute_sl_tp(entry_price, sig.atr, sig.action)
                # Regime strength: smaller position in weak trends
                strength = 1.0 if sig.regime == "TRENDING_STRONG" else 0.6
                pos_size = equity * self.position_pct * strength
                amount = (pos_size * self.leverage) / entry_price
                open_pos = TPosition(
                    side=sig.action, entry_idx=i, entry_price=entry_price,
                    entry_time=date, amount=amount, leverage=self.leverage,
                    stop_loss=sl, take_profit=tp, entry_atr=sig.atr,
                )
                # Entry fee
                equity -= amount * entry_price * self.trading_fee

            # ---- Update equity curve (mark-to-market) ----
            if open_pos is not None:
                if open_pos.side == "long":
                    unreal = open_pos.amount * (close - open_pos.entry_price) * open_pos.leverage
                else:
                    unreal = open_pos.amount * (open_pos.entry_price - close) * open_pos.leverage
                total_equity = equity + unreal
            else:
                total_equity = equity
            equity_curve.append(total_equity)
            if total_equity > peak:
                peak = total_equity
            dd = (peak - total_equity) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd

        # ---- Close any remaining position at last close ----
        if open_pos is not None:
            last_close = float(ohlcv["close"].iloc[-1])
            self._close_position(open_pos, last_close, len(features) - 1,
                                 features.index[-1], "end_of_data", equity)
            equity += open_pos.pnl
            closed.append(open_pos)

        return self._compile_result(closed, equity_curve, max_dd, daily_returns, date_series, ohlcv)

    def _close_position(self, pos: TPosition, exit_price: float, exit_idx: int,
                        exit_time, reason: str, equity_before: float):
        """Finalize a position's P&L (exit fee + leverage-adjusted return)."""
        # Exit fee
        exit_fee = pos.amount * exit_price * self.trading_fee
        if pos.side == "long":
            gross_pct = (exit_price / pos.entry_price - 1) * pos.leverage
        else:
            gross_pct = (1 - exit_price / pos.entry_price) * pos.leverage
        # Subtract round-trip fee impact (entry fee already deducted from equity at entry)
        net_pct = gross_pct - self.trading_fee - self.trading_fee  # approx fee drag on notional
        pos.pnl_pct = net_pct
        pos.pnl = pos.amount * pos.entry_price * net_pct - exit_fee
        pos.exit_idx = exit_idx
        pos.exit_price = exit_price
        pos.exit_time = exit_time
        pos.exit_reason = reason

    def _compile_result(self, closed: list[TPosition], equity_curve: list,
                        max_dd: float, daily_returns: list, date_series, ohlcv) -> TrendResult:
        res = TrendResult()
        res.max_drawdown = max_dd
        res.equity_curve = equity_curve
        res.trades = [t.__dict__ for t in closed]
        res.total_trades = len(closed)
        res.candles_processed = len(ohlcv)
        if date_series is not None and len(date_series) > 0:
            res.start_date = str(date_series.iloc[0])
            res.end_date = str(date_series.iloc[-1])

        if not closed:
            res.total_return = (equity_curve[-1] / self.initial_equity - 1) if equity_curve else 0.0
            return res

        wins = [t for t in closed if t.pnl > 0]
        losses = [t for t in closed if t.pnl <= 0]
        res.winning_trades = len(wins)
        res.losing_trades = len(losses)
        res.win_rate = len(wins) / len(closed) if closed else 0.0
        res.avg_win = np.mean([t.pnl for t in wins]) if wins else 0.0
        res.avg_loss = np.mean([t.pnl for t in losses]) if losses else 0.0
        res.best_trade = max((t.pnl for t in closed), default=0.0)
        res.worst_trade = min((t.pnl for t in closed), default=0.0)
        gross_profit = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))
        res.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        res.total_return = (equity_curve[-1] / self.initial_equity - 1) if equity_curve else 0.0

        # Avg duration
        durations = []
        for t in closed:
            if t.exit_idx > t.entry_idx:
                durations.append(t.exit_idx - t.entry_idx)
        res.avg_duration_hours = np.mean(durations) if durations else 0.0

        # Max consecutive losses
        streak = 0; max_streak = 0
        for t in closed:
            if t.pnl <= 0:
                streak += 1; max_streak = max(max_streak, streak)
            else:
                streak = 0
        res.max_consecutive_losses = max_streak

        # Sharpe / Sortino from daily returns
        if len(daily_returns) > 5:
            dr = np.array(daily_returns)
            std = dr.std()
            res.sharpe_ratio = (dr.mean() / std * np.sqrt(365)) if std > 0 else 0.0
            downside = dr[dr < 0]
            dstd = downside.std() if len(downside) > 0 else 0
            res.sortino_ratio = (dr.mean() / dstd * np.sqrt(365)) if dstd > 0 else 0.0

        return res
