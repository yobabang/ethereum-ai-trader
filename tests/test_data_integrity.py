"""P0 Data Integrity Tests — Real OKX Data Validation.

Based on test-engineer's plan (test_plan_real_data.md).
Tests D1.1 through D1.8 — all P0 priority.
"""

import pandas as pd
import pytest
from pathlib import Path

DATA_DIR = Path("user_data/data/okx")
PRICE_COLS = ["open", "high", "low", "close"]
TIMEFRAME_4H = pd.Timedelta("4h")


def _load(pair_safe: str, tf: str = "4h") -> pd.DataFrame:
    path = DATA_DIR / f"{pair_safe}-{tf}-futures.feather"
    if not path.exists():
        pytest.skip(f"File not found: {path}")
    df = pd.read_feather(path)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date")


class TestDataIntegrity:
    """P0: Data integrity for real OKX data."""

    # D1.1: Column structure
    @pytest.mark.parametrize("pair", ["BTC_USDT_USDT", "ETH_USDT_USDT"])
    def test_column_structure(self, pair):
        df = _load(pair)
        expected = {"date", "open", "high", "low", "close", "volume"}
        missing = expected - set(df.columns)
        assert not missing, f"Missing columns: {missing}"
        assert len(df.columns) == 6, f"Expected 6 cols, got {len(df.columns)}"

    # D1.2: OHLC logic
    @pytest.mark.parametrize("pair", ["BTC_USDT_USDT", "ETH_USDT_USDT"])
    def test_ohlc_logic(self, pair):
        df = _load(pair)
        assert (df["high"] >= df["low"]).all(), "high < low detected"
        assert (df["high"] >= df["close"]).all(), "high < close"
        assert (df["high"] >= df["open"]).all(), "high < open"
        assert (df["low"] <= df["close"]).all(), "low > close"
        assert (df["low"] <= df["open"]).all(), "low > open"

    # D1.3: Price positivity
    @pytest.mark.parametrize("pair", ["BTC_USDT_USDT", "ETH_USDT_USDT"])
    def test_price_positive(self, pair):
        df = _load(pair)
        for col in PRICE_COLS:
            assert (df[col] > 0).all(), f"{col} has non-positive values"

    # D1.4: Volume positivity
    @pytest.mark.parametrize("pair", ["BTC_USDT_USDT", "ETH_USDT_USDT"])
    def test_volume_positive(self, pair):
        df = _load(pair)
        assert (df["volume"] >= 0).all(), "Negative volume"
        vol_positive_pct = (df["volume"] > 0).mean()
        assert vol_positive_pct > 0.99, f"Only {vol_positive_pct:.2%} candles have volume > 0"

    # D1.5: Time continuity
    @pytest.mark.parametrize("pair", ["BTC_USDT_USDT", "ETH_USDT_USDT"])
    def test_time_continuity(self, pair):
        df = _load(pair)
        diffs = df["date"].diff().dropna()
        mode_diff = diffs.mode()[0]
        assert mode_diff == TIMEFRAME_4H, f"Expected 4h interval, got {mode_diff}"
        gaps = (diffs != TIMEFRAME_4H).sum()
        assert gaps == 0, f"Found {gaps} gaps in time series"

    # D1.6: No duplicate timestamps
    @pytest.mark.parametrize("pair", ["BTC_USDT_USDT", "ETH_USDT_USDT"])
    def test_no_duplicates(self, pair):
        df = _load(pair)
        assert df["date"].is_unique, "Duplicate timestamps found"

    # D1.7: Monotonic time
    @pytest.mark.parametrize("pair", ["BTC_USDT_USDT", "ETH_USDT_USDT"])
    def test_monotonic(self, pair):
        df = _load(pair)
        assert df["date"].is_monotonic_increasing

    # D1.8: Sufficient data
    @pytest.mark.parametrize("pair", ["BTC_USDT_USDT", "ETH_USDT_USDT"])
    def test_sufficient_data(self, pair):
        df = _load(pair)
        assert len(df) >= 200, f"Only {len(df)} candles (need >= 200)"
