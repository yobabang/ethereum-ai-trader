"""FastAPI TestClient tests for engine/api_bridge.py.

Covers the manual-override endpoints added for the simulation trading UI:
  - GET  /trade/account        (initial_equity derivation, max_drawdown real)
  - GET  /trade/positions      (liq_price field present)
  - POST /trade/manual         (Pydantic Field validation rejects out-of-range)
  - DELETE /trade/positions/{id}  (close path, HTTPException instead of NameError)

Network is stubbed: SimBroker.get_ticker and api_bridge._get_price return a
fixed price, so no OKX/Binance calls are made. Each test uses an isolated
tmp DB so state never leaks between tests.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Ensure project root importable
_PROJECT = Path(__file__).resolve().parent.parent
if str(_PROJECT) not in sys.path:
    sys.path.insert(0, str(_PROJECT))

from engine import api_bridge
from engine.sim_broker import SimBroker, SimConfig

FIXED_PRICE = 50000.0


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient backed by an isolated tmp DB and stubbed market prices."""
    db_path = str(tmp_path / "test_trader.db")

    # Stub network price fetches everywhere they're read
    monkeypatch.setattr(api_bridge, "_get_price", lambda pair, fallback: FIXED_PRICE)
    monkeypatch.setattr(SimBroker, "get_ticker", lambda self, pair: FIXED_PRICE)

    # Initialize the global broker with a known initial equity
    api_bridge.initialize_broker(db_path=db_path, initial_equity=5000.0)

    with TestClient(api_bridge.app) as c:
        yield c


# ---------------------------------------------------------------------------
# GET /trade/account
# ---------------------------------------------------------------------------

def test_account_returns_real_initial_equity(client):
    """initial_equity must reflect the configured 5000, not a hardcoded 1000."""
    r = client.get("/api/v1/trade/account")
    assert r.status_code == 200
    body = r.json()
    assert body["initial_equity"] == 5000.0
    # With no positions, equity == initial
    assert body["equity"] == 5000.0
    assert body["balance"] == 5000.0
    assert body["open_positions"] == 0
    # max_drawdown must be a number (0.0 with no history), not missing/None
    assert "max_drawdown" in body
    assert isinstance(body["max_drawdown"], (int, float))


# ---------------------------------------------------------------------------
# POST /trade/manual — Pydantic Field validation
# ---------------------------------------------------------------------------

def test_manual_order_rejects_excessive_leverage(client):
    """leverage > 5 must be rejected at the schema layer (422)."""
    r = client.post("/api/v1/trade/manual", json={
        "pair": "BTC/USDT:USDT", "side": "long",
        "leverage": 100, "position_size_pct": 0.10,
        "stop_loss_pct": 0.02, "take_profit_pct": 0.04,
    })
    assert r.status_code == 422


def test_manual_order_rejects_excessive_position(client):
    """position_size_pct > 0.20 must be rejected (422)."""
    r = client.post("/api/v1/trade/manual", json={
        "pair": "BTC/USDT:USDT", "side": "long",
        "leverage": 3, "position_size_pct": 0.50,
        "stop_loss_pct": 0.02, "take_profit_pct": 0.04,
    })
    assert r.status_code == 422


def test_manual_order_rejects_zero_stop_loss(client):
    """stop_loss_pct must be > 0 (no instant stop-out via override)."""
    r = client.post("/api/v1/trade/manual", json={
        "pair": "BTC/USDT:USDT", "side": "long",
        "leverage": 3, "position_size_pct": 0.10,
        "stop_loss_pct": 0.0, "take_profit_pct": 0.04,
    })
    assert r.status_code == 422


def test_manual_order_opens_position(client):
    """A valid within-range manual order opens a position and returns its id."""
    r = client.post("/api/v1/trade/manual", json={
        "pair": "BTC/USDT:USDT", "side": "long",
        "leverage": 3, "position_size_pct": 0.10,
        "stop_loss_pct": 0.02, "take_profit_pct": 0.04,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert "position_id" in body


# ---------------------------------------------------------------------------
# GET /trade/positions — liq_price field
# ---------------------------------------------------------------------------

def test_positions_include_liquidation_price(client):
    """After opening a position, /trade/positions must return liq_price."""
    # Open a position first
    client.post("/api/v1/trade/manual", json={
        "pair": "BTC/USDT:USDT", "side": "long",
        "leverage": 3, "position_size_pct": 0.10,
        "stop_loss_pct": 0.02, "take_profit_pct": 0.04,
    })
    r = client.get("/api/v1/trade/positions")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 1
    pos = body["positions"][0]
    assert "liq_price" in pos
    # Long liq price should be below entry
    assert pos["liq_price"] is not None
    assert pos["liq_price"] < pos["entry_price"]


# ---------------------------------------------------------------------------
# DELETE /trade/positions/{id} — close path
# ---------------------------------------------------------------------------

def test_close_position_returns_success(client):
    """Closing an open position returns success with exit_price."""
    open_resp = client.post("/api/v1/trade/manual", json={
        "pair": "BTC/USDT:USDT", "side": "long",
        "leverage": 3, "position_size_pct": 0.10,
        "stop_loss_pct": 0.02, "take_profit_pct": 0.04,
    })
    pid = open_resp.json()["position_id"]

    r = client.delete(f"/api/v1/trade/positions/{pid}")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["position_id"] == pid
    assert body["exit_price"] == FIXED_PRICE


def test_close_nonexistent_returns_404_not_500(client):
    """Closing an unknown id must return 404, NOT a 500 from a NameError.

    Regression for the missing HTTPException import: previously the 404 path
    raised NameError and crashed to 500.
    """
    r = client.delete("/api/v1/trade/positions/99999")
    assert r.status_code == 404
    # And the body must be a proper FastAPI error detail, not a stack trace
    assert "detail" in r.json()


# ---------------------------------------------------------------------------
# CORS — DELETE method allowed
# ---------------------------------------------------------------------------

def test_cors_allows_delete(client):
    """Preflight for DELETE must be allowed (regression for CORS allow_methods)."""
    r = client.options(
        "/api/v1/trade/positions/1",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "DELETE",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    assert r.status_code == 200
    assert "DELETE" in r.headers.get("access-control-allow-methods", "")


# ---------------------------------------------------------------------------
# GET/POST /trade/control — runtime mode/leverage/paused switching
# ---------------------------------------------------------------------------

@pytest.fixture
def control_client(tmp_path, monkeypatch):
    """TestClient with trading_state.json isolated to tmp_path."""
    state_path = str(tmp_path / "trading_state.json")
    monkeypatch.setattr(api_bridge, "_TRADING_STATE_PATH", state_path)
    monkeypatch.setattr(api_bridge, "_get_price", lambda pair, fallback: FIXED_PRICE)
    monkeypatch.setattr(SimBroker, "get_ticker", lambda self, pair: FIXED_PRICE)
    api_bridge.initialize_broker(db_path=str(tmp_path / "ctrl.db"), initial_equity=1000.0)
    with TestClient(api_bridge.app) as c:
        yield c


def test_control_get_returns_defaults_when_unset(control_client):
    r = control_client.get("/api/v1/trade/control")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] is None
    assert body["paused"] is False
    assert body["leverage"] is None


def test_control_post_sets_mode_and_leverage(control_client):
    r = control_client.post("/api/v1/trade/control", json={"mode": "hybrid", "leverage": 20})
    assert r.status_code == 200
    assert r.json()["success"] is True

    # GET reflects the write
    g = control_client.get("/api/v1/trade/control").json()
    assert g["mode"] == "hybrid"
    assert g["leverage"] == 20
    assert g["updated_at"] is not None


def test_control_post_rejects_invalid_mode(control_client):
    r = control_client.post("/api/v1/trade/control", json={"mode": "invalid"})
    assert r.status_code == 422


def test_control_post_rejects_excessive_leverage(control_client):
    r = control_client.post("/api/v1/trade/control", json={"leverage": 60})
    assert r.status_code == 422


def test_control_post_partial_update_merges(control_client):
    """Setting leverage alone must not wipe a previously-set mode."""
    control_client.post("/api/v1/trade/control", json={"mode": "ai"})
    control_client.post("/api/v1/trade/control", json={"leverage": 10})
    g = control_client.get("/api/v1/trade/control").json()
    assert g["mode"] == "ai"   # preserved
    assert g["leverage"] == 10  # updated


def test_control_pause_toggle(control_client):
    control_client.post("/api/v1/trade/control", json={"paused": True})
    assert control_client.get("/api/v1/trade/control").json()["paused"] is True
    control_client.post("/api/v1/trade/control", json={"paused": False})
    assert control_client.get("/api/v1/trade/control").json()["paused"] is False

