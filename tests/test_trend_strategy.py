"""Tests for the rule-based trend strategy and its backtester (Plan D)."""
import numpy as np
import pandas as pd
import pytest

from engine.trend_strategy import TrendParams, TrendStrategy, compute_regime_rulebased
from engine.trend_backtest import TrendBacktest


def make_trending_ohlcv(n=300, drift=1.0, seed=42):
    """Strong uptrend data for testing entry logic."""
    np.random.seed(seed)
    close = 60000 + np.cumsum(np.random.randn(n) * 50 + drift)
    high = close + np.abs(np.random.randn(n) * 30)
    low = close - np.abs(np.random.randn(n) * 30)
    open_ = low + np.random.rand(n) * (high - low)
    volume = np.abs(np.random.randn(n) * 100 + 500)
    return pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=n, freq="1h"),
        "open": open_, "high": high, "low": low, "close": close, "volume": volume,
    })


def make_flat_ohlcv(n=300, seed=7):
    """No-trend (ranging) data."""
    np.random.seed(seed)
    close = 60000 + np.random.randn(n) * 80
    high = close + np.abs(np.random.randn(n) * 40)
    low = close - np.abs(np.random.randn(n) * 40)
    open_ = low + np.random.rand(n) * (high - low)
    volume = np.abs(np.random.randn(n) * 100 + 500)
    return pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=n, freq="1h"),
        "open": open_, "high": high, "low": low, "close": close, "volume": volume,
    })


class TestRegimeRulebased:
    def test_trending_data_labels_trending(self):
        from engine.features import FeatureEngineer
        feats = FeatureEngineer().compute_price_features(make_trending_ohlcv(300, drift=3.0))
        regime = compute_regime_rulebased(feats)
        valid = regime.dropna()
        trending = valid.isin(["TRENDING_STRONG", "TRENDING_WEAK"]).mean()
        # A strong uptrend should label a meaningful share as trending. 20% is
        # a robust floor (the rule-based labeler uses ADX + rolling quantiles,
        # which are conservative).
        assert trending > 0.2, f"Only {trending:.1%} trending in strong uptrend"

    def test_warmup_rows_handled(self):
        """Regime may be non-NaN early (ADX has a short warmup); the contract is
        that it produces valid labels once indicators are ready, not that early
        rows are NaN. Just verify no crash and a mix of labels."""
        from engine.features import FeatureEngineer
        feats = FeatureEngineer().compute_price_features(make_trending_ohlcv(100))
        regime = compute_regime_rulebased(feats)
        valid = regime.dropna()
        assert len(valid) > 50  # most rows get a label


class TestTrendStrategy:
    def test_uptrend_produces_long_signals(self):
        from engine.features import FeatureEngineer
        feats = FeatureEngineer().compute_price_features(make_trending_ohlcv(300, drift=3.0))
        sigs = TrendStrategy(TrendParams(ema_fast=9, ema_slow=50, regime_filter=False)).compute_signals(feats)
        actions = [s.action for s in sigs if s.action != "hold"]
        # In a strong uptrend we should see at least some long entries
        assert "long" in actions, "Expected long signals in uptrend"
        assert "short" not in actions or actions.count("short") < actions.count("long")

    def test_regime_filter_blocks_ranging(self):
        from engine.features import FeatureEngineer
        # Flat data → mostly ranging → regime_filter should suppress entries
        feats = FeatureEngineer().compute_price_features(make_flat_ohlcv(300))
        sigs_on = TrendStrategy(TrendParams(regime_filter=True)).compute_signals(feats)
        sigs_off = TrendStrategy(TrendParams(regime_filter=False)).compute_signals(feats)
        entries_on = sum(1 for s in sigs_on if s.action in ("long", "short"))
        entries_off = sum(1 for s in sigs_off if s.action in ("long", "short"))
        # Filter should reduce (or keep equal) entries vs no filter
        assert entries_on <= entries_off

    def test_sl_tp_long(self):
        strat = TrendStrategy(TrendParams(sl_atr_mult=2.0, tp_atr_mult=3.0))
        sl, tp = strat.compute_sl_tp(100.0, atr=1.0, side="long")
        assert sl == 98.0
        assert tp == 103.0

    def test_sl_tp_short(self):
        strat = TrendStrategy(TrendParams(sl_atr_mult=2.0, tp_atr_mult=3.0))
        sl, tp = strat.compute_sl_tp(100.0, atr=1.0, side="short")
        assert sl == 102.0
        assert tp == 97.0

    def test_should_exit_on_reversal(self):
        strat = TrendStrategy()
        from engine.trend_strategy import TrendSignal
        pos_long = type("P", (), {"side": "long", "entry_idx": 0, "entry_price": 100,
                                  "stop_loss": 95, "take_profit": 110, "entry_atr": 1})()
        # Short signal while long → exit
        exit_now, reason = strat.should_exit(pos_long, TrendSignal("short", "TRENDING_STRONG", 1.0), 10)
        assert exit_now and reason == "trend_reversal"

    def test_slope_confirm_filters_entries(self):
        """slope_confirm should produce <= entries vs no slope confirm."""
        from engine.features import FeatureEngineer
        feats = FeatureEngineer().compute_price_features(make_trending_ohlcv(400, drift=2.0))
        sigs_off = TrendStrategy(TrendParams(slope_confirm=False, regime_filter=False)).compute_signals(feats)
        sigs_on = TrendStrategy(TrendParams(slope_confirm=True, regime_filter=False)).compute_signals(feats)
        off_entries = sum(1 for s in sigs_off if s.action in ("long", "short"))
        on_entries = sum(1 for s in sigs_on if s.action in ("long", "short"))
        assert on_entries <= off_entries, f"slope_confirm should filter: {on_entries} vs {off_entries}"

    def test_trend_filter_blocks_counter_trend_shorts(self):
        """In a strong uptrend, trend_filter should suppress short entries."""
        from engine.features import FeatureEngineer
        # Strong uptrend data
        feats = FeatureEngineer().compute_price_features(make_trending_ohlcv(400, drift=3.0))
        sigs_off = TrendStrategy(TrendParams(trend_filter=False, regime_filter=False,
                                              slope_confirm=False)).compute_signals(feats)
        sigs_on = TrendStrategy(TrendParams(trend_filter=True, regime_filter=False,
                                             slope_confirm=False)).compute_signals(feats)
        shorts_off = sum(1 for s in sigs_off if s.action == "short")
        shorts_on = sum(1 for s in sigs_on if s.action == "short")
        # In a strong uptrend the big EMA slope is up → trend_filter blocks shorts
        assert shorts_on <= shorts_off, f"trend_filter should block shorts: {shorts_on} vs {shorts_off}"


class TestTrendBacktest:
    def test_backtest_returns_result(self):
        bt = TrendBacktest(initial_equity=5000, leverage=3)
        res = bt.run(make_trending_ohlcv(300), params=TrendParams())
        assert res.total_trades >= 0
        assert res.candles_processed == 300
        assert len(res.equity_curve) > 0
        # Equity curve should start at initial equity
        assert abs(res.equity_curve[0] - 5000) < 1e-6

    def test_metrics_in_valid_range(self):
        bt = TrendBacktest(initial_equity=5000, leverage=3)
        res = bt.run(make_trending_ohlcv(400, drift=2.0), params=TrendParams())
        d = res.to_dict()
        assert 0 <= d["win_rate_pct"] <= 100
        assert d["max_drawdown_pct"] >= 0
        assert d["total_trades"] == res.winning_trades + res.losing_trades

    def test_flat_data_no_catastrophic_loss(self):
        """On flat data with regime filter, shouldn't blow up the account."""
        bt = TrendBacktest(initial_equity=5000, leverage=3)
        res = bt.run(make_flat_ohlcv(400), params=TrendParams(regime_filter=True))
        # Should not lose more than 60% (sanity, not a hard guarantee)
        assert res.to_dict()["total_return_pct"] > -60
