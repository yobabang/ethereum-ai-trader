"""Tests for the Donchian breakout strategy."""
import numpy as np
import pandas as pd
import pytest

from engine.breakout_strategy import BreakoutStrategy, BreakoutParams


def make_ohlcv(n=300, drift=1.0, seed=42):
    np.random.seed(seed)
    close = 60000 + np.cumsum(np.random.randn(n) * 50 + drift)
    high = close + np.abs(np.random.randn(n) * 30)
    low = close - np.abs(np.random.randn(n) * 30)
    open_ = low + np.random.rand(n) * (high - low)
    volume = np.abs(np.random.randn(n) * 100 + 500)
    return pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=n, freq="4h"),
        "open": open_, "high": high, "low": low, "close": close, "volume": volume,
    })


class TestBreakoutStrategy:
    def test_uptrend_produces_long_signals(self):
        from engine.features import FeatureEngineer
        feats = FeatureEngineer().compute_price_features(make_ohlcv(300, drift=3.0))
        sigs = BreakoutStrategy(BreakoutParams(regime_filter=False, trend_filter=False)).compute_signals(feats)
        actions = [s.action for s in sigs if s.action != "hold"]
        assert "long" in actions, "Expected long breakout in uptrend"

    def test_trend_filter_blocks_counter_trend(self):
        """In strong uptrend, trend_filter should suppress shorts."""
        from engine.features import FeatureEngineer
        feats = FeatureEngineer().compute_price_features(make_ohlcv(400, drift=3.0))
        sigs_off = BreakoutStrategy(BreakoutParams(trend_filter=False, regime_filter=False)).compute_signals(feats)
        sigs_on = BreakoutStrategy(BreakoutParams(trend_filter=True, regime_filter=False)).compute_signals(feats)
        shorts_off = sum(1 for s in sigs_off if s.action == "short")
        shorts_on = sum(1 for s in sigs_on if s.action == "short")
        assert shorts_on <= shorts_off

    def test_sl_tp_long(self):
        strat = BreakoutStrategy(BreakoutParams(sl_atr_mult=2.0, tp_atr_mult=4.0))
        sl, tp = strat.compute_sl_tp(100.0, atr=1.0, side="long")
        assert sl == 98.0 and tp == 104.0

    def test_sl_tp_short(self):
        strat = BreakoutStrategy(BreakoutParams(sl_atr_mult=2.0, tp_atr_mult=4.0))
        sl, tp = strat.compute_sl_tp(100.0, atr=1.0, side="short")
        assert sl == 102.0 and tp == 96.0

    def test_donchian_excludes_current_bar(self):
        """Lookback must shift(1) — no lookahead."""
        strat = BreakoutStrategy(BreakoutParams(donchian_period=20))
        high = pd.Series(range(100, 200), dtype=float)
        low = pd.Series(range(90, 190), dtype=float)
        upper, lower = strat._donchian(high, low, 20)
        # At i=20, upper should be max of bars 0..19 (not including bar 20)
        assert upper.iloc[20] == 119.0  # max of high 100..119
        assert lower.iloc[20] == 90.0   # min of low 90..109

    def test_should_exit_on_opposite_breakout(self):
        from engine.breakout_strategy import BreakoutSignal
        strat = BreakoutStrategy()
        pos_long = type("P", (), {"side": "long", "entry_idx": 0})()
        exit_now, reason = strat.should_exit(pos_long, BreakoutSignal("short", "TRENDING_STRONG", 1.0), 10)
        assert exit_now and reason == "opposite_breakout"

    def test_backtest_runs_with_breakout(self):
        from engine.trend_backtest import TrendBacktest
        bt = TrendBacktest(initial_equity=5000, leverage=2, position_pct=0.20)
        res = bt.run(make_ohlcv(300, drift=2.0), strategy=BreakoutStrategy(BreakoutParams()))
        assert res.candles_processed == 300
        assert len(res.equity_curve) > 0
