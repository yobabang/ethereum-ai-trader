"""Unit tests for the hybrid pipeline (AI direction + trend timing).

Validates the fusion logic without network or real models — AI predict and
trend strategy are monkeypatched to return controlled directions, and we
assert the pipeline opens only when both agree.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

_PROJECT = Path(__file__).resolve().parent.parent
if str(_PROJECT) not in sys.path:
    sys.path.insert(0, str(_PROJECT))


def _make_df(n: int = 120) -> pd.DataFrame:
    """Synthetic 5m OHLCV with enough rows for ema50/atr."""
    idx = pd.date_range("2026-07-15 00:00", periods=n, freq="5min")
    base = 50000
    # gentle uptrend so trend strategy can produce a signal
    close = [base + i * 5 for i in range(n)]
    df = pd.DataFrame({
        "date": idx,
        "open": close, "high": [c + 20 for c in close],
        "low": [c - 20 for c in close], "close": close,
        "volume": [100.0] * n,
    })
    return df


@pytest.fixture
def trader(tmp_path):
    """A LiveTrader in hybrid mode with stubbed broker + lazy AI."""
    from engine.live_trader import LiveTrader, HYBRID_PRESET
    t = LiveTrader(
        mode="hybrid", aggressive=False,
        db_path=str(tmp_path / "hybrid.db"), initial_equity=1000.0,
        timeframe=HYBRID_PRESET["timeframe"], interval=HYBRID_PRESET["interval"],
    )
    t.params = {**t.params, **HYBRID_PRESET}
    # Stub broker so open_order never touches the network
    t.broker.get_ticker = lambda pair: 50000.0
    return t


def _stub_ai(dp, er: float, conf: float = 0.7):
    """Make AI predict return a given expected_return (sign = direction)."""
    dp.predict = MagicMock(return_value=[{
        "expected_return": er, "confidence": conf, "max_drawdown": 0.01,
    }])


def _stub_trend(strat, action: str, reason: str = "uptrend"):
    """Make trend strategy return a given action."""
    sig = MagicMock()
    sig.action = action
    sig.reason = reason
    sig.regime = "TRENDING_STRONG"
    strat.compute_signals = MagicMock(return_value=[sig])


def test_hybrid_opens_when_ai_and_trend_agree_long(trader):
    df = _make_df()
    trader._init_ai_pipeline()
    _stub_ai(trader._dp, er=0.005)          # AI says long
    from engine.trend_strategy import TrendStrategy, TrendParams
    trader._trend_strat = TrendStrategy(TrendParams(ema_fast=9, ema_slow=50))
    _stub_trend(trader._trend_strat, "long")  # trend says long

    decision = trader._run_hybrid_pipeline(df)
    assert decision is not None
    assert decision["action"] == "LONG"
    assert decision["leverage"] == trader.params["leverage"]
    assert "hybrid long" in decision["reason"]


def test_hybrid_opens_when_ai_and_trend_agree_short(trader):
    df = _make_df()
    trader._init_ai_pipeline()
    _stub_ai(trader._dp, er=-0.005)
    from engine.trend_strategy import TrendStrategy, TrendParams
    trader._trend_strat = TrendStrategy(TrendParams(ema_fast=9, ema_slow=50))
    _stub_trend(trader._trend_strat, "short")

    decision = trader._run_hybrid_pipeline(df)
    assert decision["action"] == "SHORT"


def test_hybrid_holds_on_disagreement(trader):
    df = _make_df()
    trader._init_ai_pipeline()
    _stub_ai(trader._dp, er=0.005)  # AI long
    from engine.trend_strategy import TrendStrategy, TrendParams
    trader._trend_strat = TrendStrategy(TrendParams(ema_fast=9, ema_slow=50))
    _stub_trend(trader._trend_strat, "short")  # trend short — disagree

    decision = trader._run_hybrid_pipeline(df)
    assert decision["action"] == "HOLD"
    assert "分歧" in decision["reason"]


def test_hybrid_holds_when_ai_no_clear_direction(trader):
    df = _make_df()
    trader._init_ai_pipeline()
    _stub_ai(trader._dp, er=0.00001)  # below min_signal
    from engine.trend_strategy import TrendStrategy, TrendParams
    trader._trend_strat = TrendStrategy(TrendParams(ema_fast=9, ema_slow=50))
    _stub_trend(trader._trend_strat, "long")

    decision = trader._run_hybrid_pipeline(df)
    assert decision["action"] == "HOLD"
    assert "AI" in decision["reason"]


def test_hybrid_holds_when_trend_no_signal(trader):
    df = _make_df()
    trader._init_ai_pipeline()
    _stub_ai(trader._dp, er=0.005)
    from engine.trend_strategy import TrendStrategy, TrendParams
    trader._trend_strat = TrendStrategy(TrendParams(ema_fast=9, ema_slow=50))
    _stub_trend(trader._trend_strat, "hold")

    decision = trader._run_hybrid_pipeline(df)
    assert decision["action"] == "HOLD"
    assert "trend" in decision["reason"]


def test_hybrid_uses_tight_sl_tp(trader):
    """SL/TP must come from the high-freq hybrid preset (tight), not the old 2%/4%."""
    df = _make_df()
    trader._init_ai_pipeline()
    _stub_ai(trader._dp, er=0.005)
    from engine.trend_strategy import TrendStrategy, TrendParams
    trader._trend_strat = TrendStrategy(TrendParams(ema_fast=9, ema_slow=50))
    _stub_trend(trader._trend_strat, "long")

    decision = trader._run_hybrid_pipeline(df)
    assert decision["action"] == "LONG"
    # SL ~0.8% (allow ATR floor to raise it), TP ~1.5%
    assert decision["stop_loss_pct"] <= 0.015  # well below old 2%
    assert decision["take_profit_pct"] <= 0.02  # well below old 4%
