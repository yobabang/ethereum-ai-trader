"""Feature engineering pipeline for AI trading decisions.

Computes 40+ technical indicators from OHLCV data, plus orderbook and
derivatives market features. Designed to be compatible with FreqAI's
feature format while being standalone for the AI decision core.
"""

import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Minimum candles required for stable indicator calculation
MIN_CANDLES = 50


class FeatureEngineer:
    """Computes all features needed by the AI decision core.

    Three feature families:
    1. Price features: 40+ technical indicators from OHLCV
    2. Orderbook features: spread, depth, imbalance
    3. Derivatives features: funding rate, open interest, long/short ratio
    """

    # ------------------------------------------------------------------
    # Price features
    # ------------------------------------------------------------------

    def compute_price_features(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        """Compute all price-based technical indicators.

        Args:
            ohlcv: DataFrame with columns [open, high, low, close, volume]
                   indexed by datetime.

        Returns:
            DataFrame with original columns plus indicator columns.

        Raises:
            ValueError: If fewer than MIN_CANDLES rows provided.
        """
        if len(ohlcv) < MIN_CANDLES:
            raise ValueError(
                f"Need at least {MIN_CANDLES} candles, got {len(ohlcv)}"
            )

        df = ohlcv.copy()

        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        volume = df["volume"].astype(float)
        open_ = df["open"].astype(float)

        # ---- Momentum indicators ----
        df["rsi_14"] = self._rsi(close, period=14)
        df["rsi_28"] = self._rsi(close, period=28)

        macd, signal, hist = self._macd(close)
        df["macd"] = macd
        df["macd_signal"] = signal
        df["macd_hist"] = hist

        df["roc_6"] = self._roc(close, period=6)
        df["roc_12"] = self._roc(close, period=12)

        # ---- Trend indicators ----
        df["ema_9"] = self._ema(close, period=9)
        df["ema_21"] = self._ema(close, period=21)
        df["ema_50"] = self._ema(close, period=50)

        df["sma_20"] = self._sma(close, period=20)
        df["sma_50"] = self._sma(close, period=50)

        df["adx_14"] = self._adx(high, low, close, period=14)

        # ---- Volatility indicators ----
        df["atr_14"] = self._atr(high, low, close, period=14)
        df["atr_ratio"] = df["atr_14"] / close  # Normalized ATR
        df["atr_pct_28"] = self._atr(high, low, close, period=28) / close * 100

        bb_upper, bb_middle, bb_lower = self._bollinger(close, period=20, std=2)
        df["bb_upper"] = bb_upper
        df["bb_middle"] = bb_middle
        df["bb_lower"] = bb_lower
        df["bb_width"] = (bb_upper - bb_lower) / bb_middle
        df["bb_position"] = (close - bb_lower) / (bb_upper - bb_lower + 1e-10)

        # ---- Volume indicators ----
        df["obv"] = self._obv(close, volume)
        df["volume_sma_20"] = self._sma(volume, period=20)
        df["volume_ratio"] = volume / df["volume_sma_20"]

        # ---- Return-based features ----
        df["returns_1"] = close.pct_change(1)
        df["returns_4"] = close.pct_change(4)
        df["returns_24"] = close.pct_change(24)

        # ---- Volatility regime ----
        df["volatility_24"] = df["returns_1"].rolling(24).std()
        df["volatility_ratio"] = df["volatility_24"] / (
            df["volatility_24"].rolling(96, min_periods=24).mean()
        )

        # ---- Price position features ----
        df["high_low_ratio"] = (close - low) / (high - low + 1e-10)
        df["close_vs_sma20"] = close / df["sma_20"] - 1
        df["close_vs_sma50"] = close / df["sma_50"] - 1

        # ---- EMA cross features ----
        df["ema_cross_9_21"] = df["ema_9"] / df["ema_21"] - 1
        df["ema_cross_21_50"] = df["ema_21"] / df["ema_50"] - 1

        # ---- OHLCV pattern features ----
        df["body_ratio"] = (close - open_) / (high - low + 1e-10)
        df["upper_shadow"] = (high - np.maximum(close, open_)) / (high - low + 1e-10)
        df["lower_shadow"] = (np.minimum(close, open_) - low) / (high - low + 1e-10)

        # ---- Stochastic RSI ----
        df["stoch_rsi_k"], df["stoch_rsi_d"] = self._stoch_rsi(close, period=14)

        # ---- CCI ----
        df["cci_20"] = self._cci(high, low, close, period=20)

        # ---- MFI ----
        df["mfi_14"] = self._mfi(high, low, close, volume, period=14)

        # ---- Williams %R ----
        df["williams_r_14"] = self._williams_r(high, low, close, period=14)

        # ---- EMA slope features (trend acceleration) ----
        df["ema_9_slope"] = df["ema_9"].diff(4) / df["ema_9"].shift(4)
        df["ema_21_slope"] = df["ema_21"].diff(4) / df["ema_21"].shift(4)

        # ---- Logarithmic features ----
        df["log_returns_1"] = np.log(close / close.shift(1))
        df["log_returns_4"] = np.log(close / close.shift(4))

        return df

    # ------------------------------------------------------------------
    # Orderbook features
    # ------------------------------------------------------------------

    def compute_orderbook_features(self, orderbook: dict) -> dict[str, float]:
        """Compute features from a single orderbook snapshot.

        Args:
            orderbook: dict with 'bids' and 'asks', each a list of [price, size].

        Returns:
            dict of scalar features.
        """
        bids = np.array(orderbook["bids"], dtype=float)
        asks = np.array(orderbook["asks"], dtype=float)

        best_bid = bids[0, 0]
        best_ask = asks[0, 0]
        mid = (best_bid + best_ask) / 2

        spread_pct = (best_ask - best_bid) / mid * 100

        bid_vol_5 = bids[:5, 1].sum()
        ask_vol_5 = asks[:5, 1].sum()
        bid_vol_10 = bids[:10, 1].sum()
        ask_vol_10 = asks[:10, 1].sum()
        bid_vol_all = bids[:, 1].sum()
        ask_vol_all = asks[:, 1].sum()

        imbalance = (bid_vol_all - ask_vol_all) / (bid_vol_all + ask_vol_all + 1e-10)

        return {
            "spread_pct": round(spread_pct, 6),
            "imbalance": round(float(imbalance), 6),
            "bid_depth_5": round(float(bid_vol_5), 2),
            "ask_depth_5": round(float(ask_vol_5), 2),
            "bid_depth_10": round(float(bid_vol_10), 2),
            "ask_depth_10": round(float(ask_vol_10), 2),
            "depth_ratio_5": round(float(bid_vol_5 / (ask_vol_5 + 1e-10)), 4),
            "depth_ratio_10": round(float(bid_vol_10 / (ask_vol_10 + 1e-10)), 4),
        }

    # ------------------------------------------------------------------
    # Derivatives features
    # ------------------------------------------------------------------

    def compute_derivatives_features(self, derivatives: dict) -> dict[str, float]:
        """Compute features from derivatives market data.

        Args:
            derivatives: dict with keys:
                funding_rate (float): Current funding rate
                open_interest (float): Open interest in USDT
                long_short_ratio (float): Long/short ratio

        Returns:
            dict of scalar features including a funding signal.
        """
        funding_rate = float(derivatives.get("funding_rate", 0))
        open_interest = float(derivatives.get("open_interest", 0))
        ls_ratio = float(derivatives.get("long_short_ratio", 1.0))

        # Funding signal: extreme positive = too many longs = bearish
        #   >  0.001 (0.1%) → bearish signal
        #   < -0.001         → bullish signal
        if funding_rate > 0.001:
            funding_signal = -min(funding_rate / 0.001, 3.0)  # cap at -3
        elif funding_rate < -0.001:
            funding_signal = min(abs(funding_rate) / 0.001, 3.0)  # cap at +3
        else:
            funding_signal = 0.0

        return {
            "funding_rate": round(funding_rate, 8),
            "open_interest": round(open_interest, 2),
            "long_short_ratio": round(ls_ratio, 4),
            "funding_signal": round(funding_signal, 4),
            "oi_intensity": round(np.log1p(abs(funding_rate)) * np.log1p(open_interest), 4),
        }

    # ==================================================================
    # Indicator primitives (no external dependency on TA-Lib)
    # ==================================================================

    @staticmethod
    def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
        rs = avg_gain / (avg_loss + 1e-10)
        return 100 - (100 / (1 + rs))

    @staticmethod
    def _macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()
        macd = ema_fast - ema_slow
        macd_signal = macd.ewm(span=signal, adjust=False).mean()
        macd_hist = macd - macd_signal
        return macd, macd_signal, macd_hist

    @staticmethod
    def _ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def _sma(series: pd.Series, period: int) -> pd.Series:
        return series.rolling(period).mean()

    @staticmethod
    def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        prev_close = close.shift(1)
        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.ewm(alpha=1 / period, adjust=False).mean()

    @staticmethod
    def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        prev_close = close.shift(1)
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low

        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)

        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr_ = tr.ewm(alpha=1 / period, adjust=False).mean()

        plus_di = 100 * pd.Series(plus_dm, index=close.index).ewm(alpha=1 / period, adjust=False).mean() / (atr_ + 1e-10)
        minus_di = 100 * pd.Series(minus_dm, index=close.index).ewm(alpha=1 / period, adjust=False).mean() / (atr_ + 1e-10)

        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
        return dx.ewm(alpha=1 / period, adjust=False).mean()

    @staticmethod
    def _bollinger(close: pd.Series, period: int = 20, std: int = 2):
        middle = close.rolling(period).mean()
        std_dev = close.rolling(period).std()
        upper = middle + std * std_dev
        lower = middle - std * std_dev
        return upper, middle, lower

    @staticmethod
    def _obv(close: pd.Series, volume: pd.Series) -> pd.Series:
        direction = np.sign(close.diff()).fillna(0)
        return (direction * volume).cumsum()

    @staticmethod
    def _roc(series: pd.Series, period: int) -> pd.Series:
        return (series / series.shift(period) - 1) * 100

    @staticmethod
    def _stoch_rsi(close: pd.Series, period: int = 14):
        """Stochastic RSI (K and D lines)."""
        rsi = FeatureEngineer._rsi(close, period)
        rsi_min = rsi.rolling(period).min()
        rsi_max = rsi.rolling(period).max()
        stoch_k = (rsi - rsi_min) / (rsi_max - rsi_min + 1e-10)
        stoch_d = stoch_k.rolling(3).mean()
        return stoch_k, stoch_d

    @staticmethod
    def _cci(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20) -> pd.Series:
        tp = (high + low + close) / 3
        sma_tp = tp.rolling(period).mean()
        mad = tp.rolling(period).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
        return (tp - sma_tp) / (0.015 * mad + 1e-10)

    @staticmethod
    def _mfi(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, period: int = 14) -> pd.Series:
        tp = (high + low + close) / 3
        raw_money_flow = tp * volume
        delta = tp.diff()
        pos_flow = raw_money_flow.where(delta > 0, 0)
        neg_flow = raw_money_flow.where(delta < 0, 0)
        pos_sum = pos_flow.rolling(period).sum()
        neg_sum = neg_flow.rolling(period).sum()
        mfr = pos_sum / (neg_sum + 1e-10)
        return 100 - (100 / (1 + mfr))

    @staticmethod
    def _williams_r(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        highest = high.rolling(period).max()
        lowest = low.rolling(period).min()
        return (highest - close) / (highest - lowest + 1e-10) * -100
