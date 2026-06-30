"""Rule-based Donchian breakout strategy (Plan D, multi-strategy variant).

Entry: price breaks above N-bar high → long; below N-bar low → short.
Exit: ATR stop-loss, ATR take-profit, or opposite breakout.

Targets BTC, where EMA trend-following failed (0/8 windows). Breakout aims
to catch BTC's large directional moves without the whipsaw problem that
killed EMA cross signals in ranging periods.

Paired with TrendBacktest (same SimPosition/metric interface as trend_strategy).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class BreakoutParams:
    """Tunable breakout parameters (subject to walk-forward search)."""
    donchian_period: int = 20     # lookback for high/low breakout
    atr_period: int = 14
    sl_atr_mult: float = 2.0
    tp_atr_mult: float = 4.0
    regime_filter: bool = True    # skip non-trending regimes (reuse rule-based)
    trend_filter: bool = True     # only break out in the big-trend direction
    trend_filter_bars: int = 20
    max_hold_bars: int = 120


@dataclass
class BreakoutSignal:
    action: str          # "long" | "short" | "hold"
    regime: str
    atr: float
    reason: str = ""


def _regime(features: pd.DataFrame) -> pd.Series:
    """Reuse the rule-based regime from trend_strategy (consistent labeling)."""
    from engine.trend_strategy import compute_regime_rulebased
    return compute_regime_rulebased(features)


class BreakoutStrategy:
    """Donchian channel breakout."""

    TREND_REGIMES = {"TRENDING_STRONG", "TRENDING_WEAK"}

    def __init__(self, params: Optional[BreakoutParams] = None):
        self.params = params or BreakoutParams()

    def _donchian(self, high: pd.Series, low: pd.Series, period: int):
        """Rolling N-bar high/low, EXCLUDING the current bar (avoids lookahead)."""
        upper = high.rolling(period).max().shift(1)
        lower = low.rolling(period).min().shift(1)
        return upper, lower

    def _atr(self, features: pd.DataFrame, period: int) -> pd.Series:
        if "atr_14" in features.columns and period == 14:
            return features["atr_14"]
        h, l, c = features["high"], features["low"], features["close"]
        prev_c = c.shift(1)
        tr = pd.concat([(h - l), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
        return tr.ewm(alpha=1 / period, adjust=False).mean()

    def compute_signals(self, features: pd.DataFrame) -> list[BreakoutSignal]:
        p = self.params
        high = features["high"]; low = features["low"]; close = features["close"]
        atr = self._atr(features, p.atr_period)
        upper, lower = self._donchian(high, low, p.donchian_period)
        regime = _regime(features)

        # Big-trend direction via slow EMA slope (close-based, long window)
        ema_slow = close.ewm(span=max(50, p.donchian_period * 2), adjust=False).mean()
        slow_slope = ema_slow.diff(p.trend_filter_bars) / (ema_slow.shift(p.trend_filter_bars) + 1e-10)

        signals: list[BreakoutSignal] = []
        for i in range(len(features)):
            r = regime.iloc[i]
            if (pd.isna(r) or pd.isna(atr.iloc[i]) or pd.isna(upper.iloc[i])
                    or pd.isna(lower.iloc[i]) or pd.isna(slow_slope.iloc[i])):
                signals.append(BreakoutSignal("hold", "UNKNOWN", 0.0, "warmup"))
                continue

            c = close.iloc[i]
            up = upper.iloc[i]
            lo = lower.iloc[i]
            a = atr.iloc[i]
            big_up = slow_slope.iloc[i] > 0
            trending = r in self.TREND_REGIMES if p.regime_filter else True

            long_ok = c > up
            short_ok = c < lo

            # trend_filter: only break out in the big-trend direction
            if p.trend_filter:
                long_ok = long_ok and big_up
                short_ok = short_ok and (not big_up)

            if long_ok and trending:
                signals.append(BreakoutSignal("long", r, a, "breakout up"))
            elif short_ok and trending:
                signals.append(BreakoutSignal("short", r, a, "breakout down"))
            else:
                signals.append(BreakoutSignal("hold", r, a, "no breakout"))
        return signals

    def compute_sl_tp(self, entry_price: float, atr: float, side: str) -> tuple[float, float]:
        p = self.params
        sl_dist = atr * p.sl_atr_mult
        tp_dist = atr * p.tp_atr_mult
        if side == "long":
            return entry_price - sl_dist, entry_price + tp_dist
        return entry_price + sl_dist, entry_price - tp_dist

    def should_exit(self, pos, signal: BreakoutSignal, bar_idx: int) -> tuple[bool, str]:
        p = self.params
        # Opposite breakout signal → exit
        if pos.side == "long" and signal.action == "short":
            return True, "opposite_breakout"
        if pos.side == "short" and signal.action == "long":
            return True, "opposite_breakout"
        if bar_idx - pos.entry_idx >= p.max_hold_bars:
            return True, "max_hold"
        return False, ""
