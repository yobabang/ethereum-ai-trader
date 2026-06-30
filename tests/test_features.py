"""Tests for AI feature engineering pipeline."""

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal


# Generate realistic OHLCV data for testing
def make_ohlcv(n_candles: int = 200) -> pd.DataFrame:
    """Create realistic OHLCV data with a random walk."""
    np.random.seed(42)
    close = 60000 + np.cumsum(np.random.randn(n_candles) * 200)
    high = close + np.abs(np.random.randn(n_candles) * 100)
    low = close - np.abs(np.random.randn(n_candles) * 100)
    open_ = low + np.random.rand(n_candles) * (high - low)
    volume = np.abs(np.random.randn(n_candles) * 100 + 500)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=pd.date_range("2026-06-01", periods=n_candles, freq="4h"),
    )


class TestPriceFeatures:
    """RED: Tests for price-based technical indicators."""

    def test_compute_all_returns_dataframe_with_no_nan(self):
        """After warmup, every row must have zero NaN values.

        Technical indicators need a lookback window (e.g. SMA50 needs 50
        candles), so the first ~50 rows legitimately contain NaN. Downstream
        code (direction_predictor, regime_classifier) drops these. The contract
        is: no NaN AFTER the warmup period.
        """
        from engine.features import FeatureEngineer

        ohlcv = make_ohlcv(200)
        fe = FeatureEngineer()
        result = fe.compute_price_features(ohlcv)

        assert isinstance(result, pd.DataFrame)
        warmup = 50
        after_warmup = result.iloc[warmup:]
        assert not after_warmup.isna().any().any(), (
            f"NaN in columns after warmup: {after_warmup.columns[after_warmup.isna().any()].tolist()}"
        )

    def test_rsi_column_present_and_in_range(self):
        """RSI must be between 0 and 100."""
        from engine.features import FeatureEngineer

        ohlcv = make_ohlcv(200)
        fe = FeatureEngineer()
        result = fe.compute_price_features(ohlcv)

        assert "rsi_14" in result.columns
        rsi = result["rsi_14"].dropna()
        assert (rsi >= 0).all(), f"RSI min: {rsi.min()}"
        assert (rsi <= 100).all(), f"RSI max: {rsi.max()}"

    def test_macd_columns_present(self):
        """MACD, MACD signal, and MACD histogram must be present."""
        from engine.features import FeatureEngineer

        ohlcv = make_ohlcv(200)
        fe = FeatureEngineer()
        result = fe.compute_price_features(ohlcv)

        for col in ["macd", "macd_signal", "macd_hist"]:
            assert col in result.columns, f"Missing column: {col}"

    def test_bb_columns_present(self):
        """Bollinger Bands must include upper, middle, lower, and width."""
        from engine.features import FeatureEngineer

        ohlcv = make_ohlcv(200)
        fe = FeatureEngineer()
        result = fe.compute_price_features(ohlcv)

        for col in ["bb_upper", "bb_middle", "bb_lower", "bb_width"]:
            assert col in result.columns, f"Missing column: {col}"

    def test_atr_column_present(self):
        """ATR must be present and positive."""
        from engine.features import FeatureEngineer

        ohlcv = make_ohlcv(200)
        fe = FeatureEngineer()
        result = fe.compute_price_features(ohlcv)

        assert "atr_14" in result.columns
        atr = result["atr_14"].dropna()
        assert (atr > 0).all()

    def test_adx_column_present(self):
        """ADX must be present."""
        from engine.features import FeatureEngineer

        ohlcv = make_ohlcv(200)
        fe = FeatureEngineer()
        result = fe.compute_price_features(ohlcv)

        assert "adx_14" in result.columns

    def test_ema_columns_present(self):
        """Key EMAs must be present."""
        from engine.features import FeatureEngineer

        ohlcv = make_ohlcv(200)
        fe = FeatureEngineer()
        result = fe.compute_price_features(ohlcv)

        for col in ["ema_9", "ema_21", "ema_50"]:
            assert col in result.columns, f"Missing column: {col}"

    def test_volume_features_present(self):
        """Volume indicators must be present."""
        from engine.features import FeatureEngineer

        ohlcv = make_ohlcv(200)
        fe = FeatureEngineer()
        result = fe.compute_price_features(ohlcv)

        for col in ["obv", "volume_ratio"]:
            assert col in result.columns, f"Missing column: {col}"

    def test_minimum_candles_required(self):
        """Require at least 50 candles for stable indicator calculation."""
        from engine.features import FeatureEngineer

        ohlcv = make_ohlcv(30)
        fe = FeatureEngineer()

        with pytest.raises(ValueError, match="at least 50"):
            fe.compute_price_features(ohlcv)

    def test_return_series_features(self):
        """Return-based features must be present."""
        from engine.features import FeatureEngineer

        ohlcv = make_ohlcv(200)
        fe = FeatureEngineer()
        result = fe.compute_price_features(ohlcv)

        for col in ["returns_1", "returns_4", "returns_24"]:
            assert col in result.columns, f"Missing column: {col}"

    def test_momentum_features_present(self):
        """Momentum/ROC features must be present."""
        from engine.features import FeatureEngineer

        ohlcv = make_ohlcv(200)
        fe = FeatureEngineer()
        result = fe.compute_price_features(ohlcv)

        for col in ["roc_6", "roc_12"]:
            assert col in result.columns, f"Missing column: {col}"


class TestOrderbookFeatures:
    """RED: Tests for orderbook-derived features."""

    def make_orderbook(self) -> dict:
        """Create a mock orderbook."""
        mid = 60000.0
        bids = [[mid - i * 10, np.random.uniform(0.5, 5)] for i in range(1, 21)]
        asks = [[mid + i * 10, np.random.uniform(0.5, 5)] for i in range(1, 21)]
        return {"bids": bids, "asks": asks}

    def test_spread_feature(self):
        """Spread percentage must be computed."""
        from engine.features import FeatureEngineer

        fe = FeatureEngineer()
        ob = self.make_orderbook()
        result = fe.compute_orderbook_features(ob)

        assert "spread_pct" in result
        assert result["spread_pct"] > 0

    def test_imbalance_feature(self):
        """Bid/ask volume imbalance must be between -1 and 1."""
        from engine.features import FeatureEngineer

        fe = FeatureEngineer()
        ob = self.make_orderbook()
        result = fe.compute_orderbook_features(ob)

        assert "imbalance" in result
        assert -1 <= result["imbalance"] <= 1

    def test_depth_features(self):
        """Depth features must include multiple levels."""
        from engine.features import FeatureEngineer

        fe = FeatureEngineer()
        ob = self.make_orderbook()
        result = fe.compute_orderbook_features(ob)

        for key in ["bid_depth_5", "ask_depth_5", "bid_depth_10", "ask_depth_10"]:
            assert key in result, f"Missing key: {key}"
            assert result[key] > 0, f"{key} should be positive"


class TestDerivativesFeatures:
    """RED: Tests for derivatives market features."""

    def test_funding_rate_columns(self):
        """Funding rate features from derivatives data."""
        from engine.features import FeatureEngineer

        fe = FeatureEngineer()
        deriv = {
            "funding_rate": 0.0001,
            "open_interest": 50000000.0,
            "long_short_ratio": 1.2,
        }
        result = fe.compute_derivatives_features(deriv)

        assert "funding_rate" in result
        assert "open_interest" in result
        assert "long_short_ratio" in result

    def test_funding_rate_signal(self):
        """Extreme funding rates should produce a directional signal."""
        from engine.features import FeatureEngineer

        fe = FeatureEngineer()
        # Very positive funding = shorts get paid = bearish signal
        result_pos = fe.compute_derivatives_features(
            {"funding_rate": 0.005, "open_interest": 1000, "long_short_ratio": 1.0}
        )
        # Very negative funding = longs get paid = bullish signal
        result_neg = fe.compute_derivatives_features(
            {"funding_rate": -0.005, "open_interest": 1000, "long_short_ratio": 1.0}
        )

        assert "funding_signal" in result_pos
        # Positive extreme funding = bearish (shorts get paid, too many longs)
        assert result_pos["funding_signal"] < 0
        # Negative extreme funding = bullish (longs get paid, too many shorts)
        assert result_neg["funding_signal"] > 0


class TestFeaturePipeline:
    """RED: End-to-end feature pipeline test."""

    def test_full_pipeline_returns_no_nan(self):
        """After full pipeline, every row after warmup must have no NaN."""
        from engine.features import FeatureEngineer

        ohlcv = make_ohlcv(200)
        fe = FeatureEngineer()
        price_features = fe.compute_price_features(ohlcv)

        assert isinstance(price_features, pd.DataFrame)
        # After warmup period, all values should be non-NaN
        warmup = 50
        after_warmup = price_features.iloc[warmup:]
        assert not after_warmup.isna().any().any(), (
            f"NaN in columns after warmup: {after_warmup.columns[after_warmup.isna().any()].tolist()}"
        )

    def test_output_row_count_matches_input(self):
        """Feature output must have same number of rows as input."""
        from engine.features import FeatureEngineer

        ohlcv = make_ohlcv(200)
        fe = FeatureEngineer()
        result = fe.compute_price_features(ohlcv)

        assert len(result) == len(ohlcv)
