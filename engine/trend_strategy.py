"""Rule-based EMA trend-following strategy (Plan D).

No ML models. Entry/exit are transparent rules:
  - Enter LONG when close > EMA(fast) and EMA(fast) > EMA(slow)
  - Enter SHORT when close < EMA(fast) and EMA(fast) < EMA(slow)
  - Skip entries in non-trending regimes (rule-based regime, not the ML one)
  - Exit on ATR stop-loss, ATR take-profit, or trend reversal (EMA cross back)

Designed to be paired with TrendBacktest for walk-forward validation.
Does NOT import any ML module (direction_predictor / regime_classifier).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class TrendParams:
    """Tunable strategy parameters (subject to walk-forward grid search)."""
    ema_fast: int = 21
    ema_slow: int = 50
    atr_period: int = 14
    sl_atr_mult: float = 2.0      # stop loss = ATR * mult
    tp_atr_mult: float = 3.0      # take profit = ATR * mult
    regime_filter: bool = True    # skip entries in ranging / high-vol regimes
    slope_confirm: bool = False   # require EMA slope aligned with entry direction
    slope_bars: int = 4           # bars over which slope is measured
    trend_filter: bool = False    # only trade in the slow-EMA direction (no counter-trend)
    trend_filter_bars: int = 20   # bars over which slow-EMA slope is measured
    max_hold_bars: int = 168      # exit after N bars (~7 days on 1h) to avoid stale trades


@dataclass
class TrendSignal:
    """One bar's strategy output."""
    action: str          # "long" | "short" | "exit" | "hold"
    regime: str          # rule-based regime label
    atr: float           # ATR at this bar (for SL/TP sizing)
    reason: str = ""


def compute_regime_rulebased(features: pd.DataFrame) -> pd.Series:
    """Rule-based regime label (NOT the ML classifier).

    Uses columns already computed by FeatureEngineer.compute_price_features:
    adx_14, atr_ratio, bb_width. Returns a Series of regime strings aligned to
    features index. NaN where inputs are NaN (warmup).
    """
    adx = features.get("adx_14", pd.Series(np.nan, index=features.index))
    atr_ratio = features.get("atr_ratio", pd.Series(np.nan, index=features.index))
    bb_width = features.get("bb_width", pd.Series(np.nan, index=features.index))

    # Rolling 85th percentile of atr_ratio → HIGH_VOLATILITY threshold
    atr_high = atr_ratio.rolling(168, min_periods=24).quantile(0.85)
    bb_med = bb_width.rolling(168, min_periods=24).median()

    labels = pd.Series("RANGING_WIDE", index=features.index, dtype=str)
    # High volatility takes priority
    labels[atr_ratio > atr_high] = "HIGH_VOLATILITY"
    # Trending
    labels[(adx > 25) & (labels == "RANGING_WIDE")] = "TRENDING_STRONG"
    labels[((adx > 18) & (adx <= 25)) & (labels == "RANGING_WIDE")] = "TRENDING_WEAK"
    # Tight ranging (low ADX + narrow BB)
    tight_mask = (adx <= 18) & (bb_width < bb_med) & (labels == "RANGING_WIDE")
    labels[tight_mask] = "RANGING_TIGHT"
    # Low volatility (low ADX + low ATR)
    low_vol_mask = (adx <= 18) & (atr_ratio < atr_ratio.rolling(168, min_periods=24).quantile(0.15)) & (labels == "RANGING_WIDE")
    labels[low_vol_mask] = "LOW_VOLATILITY"

    # Mark warmup rows as NaN
    warmup = adx.isna() | atr_ratio.isna()
    labels[warmup] = np.nan
    return labels


class TrendStrategy:
    """Pure rule-based EMA trend-following strategy.

    compute_signals() returns one TrendSignal per bar. The backtester decides
    entry/exit by combining the signal with open-position state.
    """

    # Regimes where new entries are allowed (trending only)
    TREND_REGIMES = {"TRENDING_STRONG", "TRENDING_WEAK"}

    def __init__(self, params: Optional[TrendParams] = None):
        self.params = params or TrendParams()

    def _compute_emas(self, close: pd.Series) -> tuple[pd.Series, pd.Series]:
        """Compute fast/slow EMAs (independent of features.py preset periods)."""
        p = self.params
        ema_fast = close.ewm(span=p.ema_fast, adjust=False).mean()
        ema_slow = close.ewm(span=p.ema_slow, adjust=False).mean()
        return ema_fast, ema_slow

    def compute_signals(self, features: pd.DataFrame) -> list[TrendSignal]:
        """Compute a TrendSignal for every bar.

        Args:
            features: DataFrame from FeatureEngineer.compute_price_features.
                      Must contain 'close', 'atr_14' (or compute ATR here).
        """
        p = self.params
        close = features["close"]
        # Use features.py ATR if present, else compute our own
        if "atr_14" in features.columns:
            atr = features["atr_14"]
        else:
            atr = self._compute_atr(features, p.atr_period)

        ema_fast, ema_slow = self._compute_emas(close)
        regime = compute_regime_rulebased(features)

        # EMA slope (rate of change over slope_bars) — used for trend confirmation
        slope = ema_fast.diff(p.slope_bars) / (ema_fast.shift(p.slope_bars) + 1e-10)
        # Slow-EMA slope over a longer window — the "big trend" direction.
        # When trend_filter is on, shorts are blocked in a rising big trend
        # and longs in a falling one (kills counter-trend whipsaws).
        slow_slope = ema_slow.diff(p.trend_filter_bars) / (ema_slow.shift(p.trend_filter_bars) + 1e-10)

        signals: list[TrendSignal] = []
        for i in range(len(features)):
            r = regime.iloc[i]
            if pd.isna(r) or pd.isna(atr.iloc[i]) or pd.isna(ema_fast.iloc[i]) or pd.isna(ema_slow.iloc[i]):
                signals.append(TrendSignal("hold", "UNKNOWN", 0.0, "warmup"))
                continue

            c = close.iloc[i]
            ef = ema_fast.iloc[i]
            es = ema_slow.iloc[i]
            a = atr.iloc[i]
            sl = slope.iloc[i] if not pd.isna(slope.iloc[i]) else 0.0
            big_up = slow_slope.iloc[i] > 0 if not pd.isna(slow_slope.iloc[i]) else True
            trending = r in self.TREND_REGIMES if p.regime_filter else True

            if c > ef and ef > es:
                # Uptrend; require positive slope if slope_confirm is on
                slope_ok = (sl > 0) if p.slope_confirm else True
                # trend_filter: longs only when big trend is UP
                big_ok = big_up if p.trend_filter else True
                if trending and slope_ok and big_ok:
                    signals.append(TrendSignal("long", r, a, "uptrend"))
                else:
                    reason = (f"uptrend but {r} regime" if not trending
                              else "uptrend but flat slope" if not slope_ok
                              else "uptrend but big trend down")
                    signals.append(TrendSignal("hold", r, a, reason))
            elif c < ef and ef < es:
                slope_ok = (sl < 0) if p.slope_confirm else True
                # trend_filter: shorts only when big trend is DOWN (blocks the W3 counter-trend shorts)
                big_ok = (not big_up) if p.trend_filter else True
                if trending and slope_ok and big_ok:
                    signals.append(TrendSignal("short", r, a, "downtrend"))
                else:
                    reason = (f"downtrend but {r} regime" if not trending
                              else "downtrend but flat slope" if not slope_ok
                              else "downtrend but big trend up")
                    signals.append(TrendSignal("hold", r, a, reason))
                    signals.append(TrendSignal("hold", r, a, reason))
            else:
                signals.append(TrendSignal("hold", r, a, "no clear trend"))
        return signals

    @staticmethod
    def _compute_atr(features: pd.DataFrame, period: int) -> pd.Series:
        """Fallback ATR computation if features lacks atr_14."""
        high = features["high"]; low = features["low"]; close = features["close"]
        prev_close = close.shift(1)
        tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        return tr.ewm(alpha=1 / period, adjust=False).mean()

    def compute_sl_tp(self, entry_price: float, atr: float, side: str) -> tuple[float, float]:
        """Compute stop-loss and take-profit prices for a new position."""
        p = self.params
        sl_dist = atr * p.sl_atr_mult
        tp_dist = atr * p.tp_atr_mult
        if side == "long":
            return entry_price - sl_dist, entry_price + tp_dist
        return entry_price + sl_dist, entry_price - tp_dist

    def should_exit(self, pos, signal: TrendSignal, bar_idx: int) -> tuple[bool, str]:
        """Decide whether an open position should exit at this bar.

        pos: object with .side, .entry_price, .stop_loss, .take_profit, .entry_idx, .entry_atr
        Returns (exit_now, reason).
        """
        p = self.params
        # Trend reversal: EMA cross flipped against the position
        if pos.side == "long" and signal.action == "short":
            return True, "trend_reversal"
        if pos.side == "short" and signal.action == "long":
            return True, "trend_reversal"
        # Max hold
        if bar_idx - pos.entry_idx >= p.max_hold_bars:
            return True, "max_hold"
        return False, ""
