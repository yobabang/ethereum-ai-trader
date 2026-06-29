"""Integration test: AIStrategy as a freqtrade strategy."""

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def mock_config():
    return {
        "trading_mode": "futures",
        "margin_mode": "isolated",
        "stake_currency": "USDT",
        "stake_amount": "unlimited",
        "max_open_trades": 3,
        "dry_run": True,
        "ai": {
            "max_leverage": 5,
            "max_position_pct": 0.20,
            "model_dir": "./tests/ai/test_strategy_models",
        },
        "exchange": {"name": "okx", "pair_whitelist": ["BTC/USDT:USDT"]},
        "datadir": "./tests/testdata",
        "strategy": "AIStrategy",
        "timeframe": "4h",
    }


def make_ohlcv(n=200):
    np.random.seed(42)
    close = 60000 + np.cumsum(np.random.randn(n) * 200 + 10)
    high = close + np.abs(np.random.randn(n) * 100)
    low = close - np.abs(np.random.randn(n) * 100)
    open_ = low + np.random.rand(n) * (high - low)
    volume = np.abs(np.random.randn(n) * 100 + 500)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=pd.date_range("2026-06-01", periods=n, freq="4h"),
    )


class TestAIStrategy:
    """Integration test for AI strategy."""

    def test_populate_indicators_adds_features(self, mock_config):
        """Strategy must add AI features to the dataframe."""
        from freqtrade.ai.ai_strategy import AIStrategy

        strategy = AIStrategy(mock_config)
        df = make_ohlcv(200)
        metadata = {"pair": "BTC/USDT:USDT"}

        result = strategy.populate_indicators(df, metadata)

        assert "rsi_14" in result.columns
        assert "adx_14" in result.columns
        assert "ema_9" in result.columns
        assert len(result) == len(df)

    def test_populate_entry_trend_sets_signals(self, mock_config):
        """AI must set entry signals on the last candle."""
        from freqtrade.ai.ai_strategy import AIStrategy

        strategy = AIStrategy(mock_config)
        df = make_ohlcv(200)
        metadata = {"pair": "BTC/USDT:USDT"}

        # First populate features
        df = strategy.populate_indicators(df, metadata)
        # Then populate entry signals
        result = strategy.populate_entry_trend(df, metadata)

        assert "enter_long" in result.columns
        assert "enter_short" in result.columns

    def test_strategy_can_short(self, mock_config):
        """AI strategy must support shorting."""
        from freqtrade.ai.ai_strategy import AIStrategy

        strategy = AIStrategy(mock_config)
        assert strategy.can_short is True

    def test_strategy_timeframe_4h(self, mock_config):
        """AI strategy operates on 4-hour candles."""
        from freqtrade.ai.ai_strategy import AIStrategy

        strategy = AIStrategy(mock_config)
        assert strategy.timeframe == "4h"

    def test_trailing_stop_is_set(self, mock_config):
        """Trailing stop must be enabled as safety fallback."""
        from freqtrade.ai.ai_strategy import AIStrategy

        strategy = AIStrategy(mock_config)
        assert strategy.trailing_stop is True

    def test_confirm_trade_entry_rejects_without_decision(self, mock_config):
        """Without an AI decision, all trades must be rejected."""
        from freqtrade.ai.ai_strategy import AIStrategy

        strategy = AIStrategy(mock_config)
        strategy._last_decision = None

        result = strategy.confirm_trade_entry(
            pair="BTC/USDT:USDT",
            order_type="limit",
            amount=100,
            rate=60000,
            time_in_force="gtc",
            current_time=pd.Timestamp.now(),
            entry_tag=None,
            side="long",
        )
        assert result is False

    def test_confirm_trade_exit_always_allows(self, mock_config):
        """Exit must always be allowed for safety."""
        from freqtrade.ai.ai_strategy import AIStrategy

        strategy = AIStrategy(mock_config)
        result = strategy.confirm_trade_exit(
            pair="BTC/USDT:USDT",
            trade=None,
            order_type="limit",
            amount=100,
            rate=60000,
            time_in_force="gtc",
            exit_reason="test",
            current_time=pd.Timestamp.now(),
        )
        assert result is True
