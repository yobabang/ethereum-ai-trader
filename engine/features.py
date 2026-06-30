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


def _to_datetime(s: pd.Series) -> pd.Series:
    """Robustly convert a date column to datetime.

    Handles three input forms so OHLCV and derivatives always align:
      - integer (ms epoch, as written by pull_derivatives_data.py) → unit='ms'
      - datetime-like → passthrough via pd.to_datetime
      - ISO string → pd.to_datetime
    pd.to_datetime defaults to NANOseconds for ints, which silently misparses
    ms-epoch values; this avoids that trap.
    """
    if pd.api.types.is_integer_dtype(s):
        return pd.to_datetime(s, unit="ms")
    return pd.to_datetime(s)

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

    # ------------------------------------------------------------------
    # Derivatives time-series features (for training / backtest)
    # ------------------------------------------------------------------

    @staticmethod
    def _funding_signal_scalar(funding_rate: float) -> float:
        """Map a single funding rate to a directional signal.

        Mirrors the logic in compute_derivatives_features so the series
        version and the snapshot version agree.
        > 0.001 (0.1%) → bearish (negative), capped at -3
        < -0.001       → bullish (positive), capped at +3
        """
        if funding_rate > 0.001:
            return -min(funding_rate / 0.001, 3.0)
        if funding_rate < -0.001:
            return min(abs(funding_rate) / 0.001, 3.0)
        return 0.0

    def compute_derivatives_series(self, df_deriv: pd.DataFrame) -> pd.DataFrame:
        """Build time-series derivatives features for training/backtest.

        Unlike compute_derivatives_features (single snapshot → scalars), this
        takes a per-candle derivatives DataFrame and produces rolling z-scores,
        change rates, and extreme flags — the features with actual predictive
        lead on short-horizon returns.

        Args:
            df_deriv: DataFrame with a 'date' column plus any of:
                funding_rate, open_interest, long_short_ratio,
                taker_buy_sell_ratio. Must be sorted by date, aligned to the
                same timeframe/grid as the OHLCV it will be merged with.

        Returns:
            DataFrame with 'date' + derivatives feature columns. Rows where
            the source data is NaN stay NaN (downstream training drops them).
        """
        out = pd.DataFrame({"date": _to_datetime(df_deriv["date"])})
        d = df_deriv.copy()
        d["date"] = _to_datetime(d["date"])

        # ---- Funding rate features ----
        if "funding_rate" in d.columns:
            fr = pd.to_numeric(d["funding_rate"], errors="coerce").ffill()
            out["funding_rate"] = fr.values
            # Snapshot funding_signal (same logic as compute_derivatives_features)
            out["funding_signal"] = fr.apply(self._funding_signal_scalar).values
            # Rolling z-scores (24h and 7d for 1h data)
            for win, suffix in ((24, "24"), (168, "168")):
                roll = fr.rolling(win, min_periods=max(win // 4, 5))
                mean = roll.mean()
                std = roll.std()
                out[f"funding_rate_zscore_{suffix}"] = ((fr - mean) / (std + 1e-10)).values
            out["funding_rate_max_abs_24"] = fr.abs().rolling(24, min_periods=6).max().values
            out["funding_signal_mean_24"] = out["funding_signal"].rolling(24, min_periods=6).mean().values
            out["funding_signal_extreme"] = (out["funding_signal"].abs() >= 2.0).astype(float).values
            # Was the past 24h ever at an extreme? (rolling any)
            out["funding_signal_extreme_24"] = (
                out["funding_signal"].abs().rolling(24, min_periods=6).max() >= 2.0
            ).astype(float).values

        # ---- Open interest features ----
        if "open_interest" in d.columns:
            oi = pd.to_numeric(d["open_interest"], errors="coerce").ffill()
            out["open_interest"] = oi.values
            out["open_interest_change_1h"] = oi.pct_change().values
            out["open_interest_change_24h"] = oi.pct_change(24).values
            roll_oi = oi.rolling(24, min_periods=6)
            out["open_interest_zscore_24"] = (
                (oi - roll_oi.mean()) / (roll_oi.std() + 1e-10)
            ).values

        # ---- Long/short ratio features ----
        if "long_short_ratio" in d.columns:
            ls = pd.to_numeric(d["long_short_ratio"], errors="coerce").ffill()
            out["long_short_ratio"] = ls.values
            roll_ls = ls.rolling(24, min_periods=6)
            out["long_short_ratio_zscore_24"] = (
                (ls - roll_ls.mean()) / (roll_ls.std() + 1e-10)
            ).values
            # Extreme crowding: ratio > 1.5 (longs crowded) or < 0.67 (shorts crowded)
            out["long_short_ratio_extreme"] = (
                ((ls > 1.5) | (ls < 0.67)).astype(float)
            ).values

        # ---- Taker buy/sell ratio features ----
        if "taker_buy_sell_ratio" in d.columns:
            tbs = pd.to_numeric(d["taker_buy_sell_ratio"], errors="coerce").ffill()
            out["taker_buy_sell_ratio"] = tbs.values
            # Deviation from 1.0 (balanced) — positive = buy pressure
            out["taker_pressure"] = (tbs - 1.0).values
            roll_tbs = tbs.rolling(24, min_periods=6)
            out["taker_buy_sell_zscore_24"] = (
                (tbs - roll_tbs.mean()) / (roll_tbs.std() + 1e-10)
            ).values

        return out

    def compute_all_features(
        self,
        ohlcv: pd.DataFrame,
        derivatives: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """Compute price features and optionally merge derivatives series.

        Args:
            ohlcv: OHLCV DataFrame with a 'date' column (or datetime index).
            derivatives: Optional per-candle derivatives DataFrame (output of
                pull_derivatives_data.py). If provided, its time-series features
                are merged onto the price features by date (left join), so
                every downstream model gets the derivatives columns for free.

        Returns:
            Feature DataFrame. If derivatives given, contains 40+ price
            indicators plus ~18 derivatives features.
        """
        feats = self.compute_price_features(ohlcv)

        if derivatives is None or derivatives.empty:
            return feats

        # Ensure both sides have a datetime 'date' column for alignment
        if "date" not in feats.columns:
            feats = feats.copy()
            feats["date"] = _to_datetime(ohlcv["date"]) if "date" in ohlcv.columns else feats.index
        else:
            feats["date"] = _to_datetime(feats["date"])

        deriv_feats = self.compute_derivatives_series(derivatives)
        # Merge on date (left join — keep all OHLCV rows; missing deriv → NaN)
        merged = feats.merge(deriv_feats, on="date", how="left")
        # Forward-fill derivatives features within gaps (they update slower than 1h)
        deriv_cols = deriv_feats.columns.drop("date")
        merged[deriv_cols] = merged[deriv_cols].ffill()
        return merged

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
