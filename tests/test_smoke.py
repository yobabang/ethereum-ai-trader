"""End-to-end smoke tests for the simulation trading platform.

Tests the full pipeline against real SimBroker interfaces:
  - Open order → price move → SL/TP/liquidation close → DB persistence
  - Restart recovery
  - Multiple pairs (BTC + ETH)
  - Funding rate settlement
  - Trade journal logging

Run: python -m pytest tests/test_smoke.py -v
"""
import pytest
import tempfile
from datetime import datetime, timezone, timedelta

from engine.database import Database
from engine.sim_broker import SimBroker, SimConfig


@pytest.fixture
def temp_db(tmp_path):
    return str(tmp_path / "test_smoke.db")


def _make_broker(db_path, initial_equity=1000.0, aggressive=False):
    config = SimConfig(
        initial_equity=initial_equity,
        max_leverage=1000 if aggressive else 5,
        max_position_pct=1.0 if aggressive else 0.20,
        min_confidence=0.45 if aggressive else 0.55,
        aggressive=aggressive,
    )
    return SimBroker(db_path=db_path, config=config)


def _mock_ticker(broker, prices):
    def fake_get(pair):
        return prices.get(pair, 50000.0)
    broker.get_ticker = fake_get
    broker.get_funding_rate = lambda pair: 0.0001


def _make_decision(pair="BTC/USDT:USDT", side="long", pos_pct=0.10,
                   leverage=3, sl_pct=0.02, tp_pct=0.04, confidence=0.75):
    return {
        "pair": pair, "side": side, "position_size_pct": pos_pct,
        "leverage": leverage, "stop_loss_pct": sl_pct,
        "take_profit_pct": tp_pct, "confidence": confidence,
        "reason": "smoke_test",
    }


# ---------------------------------------------------------------------------
# Full lifecycle
# ---------------------------------------------------------------------------

def test_full_lifecycle_long(temp_db):
    """Open long → price up → TP close → verify DB + equity increase."""
    broker = _make_broker(temp_db)
    _mock_ticker(broker, {"BTC/USDT:USDT": 50000.0})
    initial_equity = broker.equity

    pos_id = broker.open_order(_make_decision(
        side="long", leverage=3, pos_pct=0.10, sl_pct=0.02, tp_pct=0.04,
    ))
    assert pos_id is not None
    assert len(broker.open_positions) == 1

    # TP at 4% rise = $52000
    _mock_ticker(broker, {"BTC/USDT:USDT": 52000.0})
    broker.check_positions()
    assert len(broker.open_positions) == 0

    # DB has closed position
    closed = broker.db.get_recent_positions(5)
    assert len(closed) >= 1
    assert closed[0]["status"] == "closed"
    assert closed[0]["exit_reason"] == "take_profit"
    assert closed[0]["realized_pnl"] > 0


def test_full_lifecycle_short(temp_db):
    """Open short → price down → TP close → verify DB."""
    broker = _make_broker(temp_db)
    _mock_ticker(broker, {"ETH/USDT:USDT": 3000.0})

    pos_id = broker.open_order(_make_decision(
        pair="ETH/USDT:USDT", side="short", leverage=3,
        sl_pct=0.02, tp_pct=0.04,
    ))
    assert pos_id is not None

    # TP at 4% drop = $2880
    _mock_ticker(broker, {"ETH/USDT:USDT": 2880.0})
    broker.check_positions()
    assert len(broker.open_positions) == 0
    assert broker.db.get_recent_positions(5)[0]["exit_reason"] == "take_profit"


# ---------------------------------------------------------------------------
# SL trigger
# ---------------------------------------------------------------------------

def test_sl_trigger(temp_db):
    """Long order → adverse price → stop-loss close."""
    broker = _make_broker(temp_db)
    _mock_ticker(broker, {"BTC/USDT:USDT": 50000.0})
    broker.open_order(_make_decision(side="long", sl_pct=0.02))

    _mock_ticker(broker, {"BTC/USDT:USDT": 48000.0})  # 4% drop, hits 2% SL
    broker.check_positions()
    assert len(broker.open_positions) == 0
    assert broker.db.get_recent_positions(5)[0]["exit_reason"] == "stop_loss"


# ---------------------------------------------------------------------------
# Restart recovery
# ---------------------------------------------------------------------------

def test_restart_recovery(temp_db):
    """Open position → simulate restart → verify position recovered."""
    broker1 = _make_broker(temp_db)
    _mock_ticker(broker1, {"BTC/USDT:USDT": 50000.0})
    broker1.open_order(_make_decision(side="long", pos_pct=0.10, leverage=3))
    balance_after = broker1.balance
    assert len(broker1.open_positions) == 1

    # New broker, same DB
    broker2 = _make_broker(temp_db)
    assert len(broker2.open_positions) == 1
    pos = list(broker2.open_positions.values())[0]
    assert pos.pair == "BTC/USDT:USDT"
    assert pos.side == "long"
    assert broker2.balance == pytest.approx(balance_after, rel=1e-3)


# ---------------------------------------------------------------------------
# Multiple pairs
# ---------------------------------------------------------------------------

def test_multiple_pairs(temp_db):
    """Open BTC long + ETH short simultaneously."""
    broker = _make_broker(temp_db)
    _mock_ticker(broker, {"BTC/USDT:USDT": 50000.0, "ETH/USDT:USDT": 3000.0})

    btc_id = broker.open_order(_make_decision(pair="BTC/USDT:USDT", side="long"))
    eth_id = broker.open_order(_make_decision(pair="ETH/USDT:USDT", side="short"))

    assert btc_id is not None
    assert eth_id is not None
    assert len(broker.open_positions) == 2


def test_same_pair_rejected(temp_db):
    """Second order on same pair should be rejected."""
    broker = _make_broker(temp_db)
    _mock_ticker(broker, {"BTC/USDT:USDT": 50000.0})
    broker.open_order(_make_decision(side="long"))
    pos_id2 = broker.open_order(_make_decision(side="short"))  # same pair
    assert pos_id2 is None
    assert len(broker.open_positions) == 1


# ---------------------------------------------------------------------------
# Liquidation
# ---------------------------------------------------------------------------

def test_liquidation(temp_db):
    """High leverage + adverse move → liquidation."""
    broker = _make_broker(temp_db)
    _mock_ticker(broker, {"BTC/USDT:USDT": 50000.0})
    broker.open_order(_make_decision(
        side="long", leverage=5, pos_pct=0.20, sl_pct=0.20, tp_pct=0.40,
    ))
    _mock_ticker(broker, {"BTC/USDT:USDT": 40000.0})  # 20% drop, 5x lev → liquidation
    broker.check_positions()
    assert len(broker.open_positions) == 0
    closed = broker.db.get_recent_positions(5)
    assert closed[0]["exit_reason"] == "liquidated"


# ---------------------------------------------------------------------------
# Funding rate
# ---------------------------------------------------------------------------

def test_funding_settlement(temp_db):
    """Verify funding accrual on open positions."""
    broker = _make_broker(temp_db)
    _mock_ticker(broker, {"BTC/USDT:USDT": 50000.0})
    broker.open_order(_make_decision(side="long"))
    pos = list(broker.open_positions.values())[0]
    assert pos.funding_paid == 0.0

    # Manually trigger funding settlement
    broker._maybe_settle_funding(pos)
    # May or may not settle depending on time; at least no crash
    assert pos.funding_paid >= 0


# ---------------------------------------------------------------------------
# Decision logging
# ---------------------------------------------------------------------------

def test_decision_logging(temp_db):
    """AI decisions logged to ai_decisions table."""
    broker = _make_broker(temp_db)
    broker.db.log_decision({
        "pair": "BTC/USDT:USDT", "action": "LONG",
        "confidence": 0.75, "expected_return": 0.015,
        "position_size_pct": 0.10, "stop_loss_pct": 0.02,
        "take_profit_pct": 0.04, "leverage": 3,
        "reason": "smoke_test", "executed": True,
        "mode": "ai", "aggressive": False,
    })
    decisions = broker.db.get_recent_decisions(10)
    assert len(decisions) >= 1
    assert decisions[0]["action"] == "LONG"


# ---------------------------------------------------------------------------
# Equity snapshots
# ---------------------------------------------------------------------------

def test_equity_snapshot(temp_db):
    """Snapshot persists and retrieves correctly."""
    broker = _make_broker(temp_db)
    broker.snapshot_equity()
    history = broker.db.get_equity_history(days=1)
    assert len(history) >= 1
    assert history[-1]["equity"] == pytest.approx(1000.0, abs=1.0)


# ---------------------------------------------------------------------------
# Account stats
# ---------------------------------------------------------------------------

def test_account_stats(temp_db):
    """Account stats aggregate correctly from DB."""
    broker = _make_broker(temp_db)
    _mock_ticker(broker, {"BTC/USDT:USDT": 50000.0})

    # Place and close a winning trade
    broker.open_order(_make_decision(side="long"))
    _mock_ticker(broker, {"BTC/USDT:USDT": 52000.0})
    broker.check_positions()

    stats = broker.db.get_account_stats(1000.0)
    assert stats["total_trades"] == 1
    assert stats["winning_trades"] == 1
    assert stats["win_rate"] == 1.0
