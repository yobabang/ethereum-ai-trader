"""Simulation broker — emulates a real futures exchange with virtual money.

"除了钱是假的，其他必须真" (SPEC_SUPPLEMENT.md §1.3):
  - Real OKX prices (public, no API key)
  - Real funding rates (settled at 00:00/08:00/16:00 UTC, pro-rated if <8h held)
  - Dynamic liquidation price (recomputed from margin + accrued funding + PnL)
  - Slippage (0.02%) + taker fees (0.05%) on market orders
  - Independent SL/TP check loop (every 5s, not tied to the 15min AI loop)

Never connects to live trading. The OKX client here only reads public market
data (ticker, funding rate). No API key, no order placement, no account writes.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import requests

from engine.database import Database

logger = logging.getLogger(__name__)

# --- Exchange constants (realistic OKX values) ---
TAKER_FEE = 0.0005          # 0.05%
MAKER_FEE = 0.0002          # 0.02% (informational; AI uses market orders = taker)
SLIPPAGE = 0.0002           # 0.02% market impact
MAINTENANCE_MARGIN = 0.005  # 0.5% maintenance margin ratio
MAX_CONCURRENT_PER_PAIR = 1 # one position per pair (no hedging)
FUNDING_HOURS = (0, 8, 16)  # UTC settlement times

OKX_TICKER_URL = "https://www.okx.com/api/v5/market/ticker?instId={}"
OKX_FUNDING_URL = "https://www.okx.com/api/v5/public/funding-rate?instId={}"
BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker/price?symbol={}"
BINANCE_FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate?symbol={}&limit=1"

# Map our pair format to exchange instrument ids
OKX_INST = {"BTC/USDT:USDT": "BTC-USDT-SWAP", "ETH/USDT:USDT": "ETH-USDT-SWAP"}
BINANCE_INST = {"BTC/USDT:USDT": "BTCUSDT", "ETH/USDT:USDT": "ETHUSDT"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _utcnow().isoformat()


@dataclass
class SimConfig:
    initial_equity: float = 1000.0
    max_leverage: int = 5            # default conservative cap
    max_position_pct: float = 0.20   # default conservative cap
    min_confidence: float = 0.55
    aggressive: bool = False


@dataclass
class OpenPosition:
    """In-memory mirror of an open DB position, for fast SL/TP checks."""
    id: int
    pair: str
    side: str            # 'long' | 'short'
    entry_price: float
    entry_time: datetime
    contracts: float
    margin: float
    leverage: int
    sl_price: float
    tp_price: float
    funding_paid: float = 0.0
    realized_pnl: float = 0.0
    ai_confidence: Optional[float] = None
    ai_reason: Optional[str] = None
    mode: str = "ai"
    aggressive: bool = False
    last_funding_settle: Optional[datetime] = None  # last settlement time passed


class MarketDataError(Exception):
    """Raised when both OKX and Binance fail to return market data."""


class SimBroker:
    """Virtual futures broker backed by SQLite + real market data."""

    def __init__(self, db_path: str = "sim_trader.db", config: SimConfig = None):
        self.db = Database(db_path)
        self.config = config or SimConfig()
        # Apply aggressive caps if requested
        if self.config.aggressive:
            self.config.max_leverage = 1000   # effectively unlimited
            self.config.max_position_pct = 1.0
            self.config.min_confidence = 0.45
        self.equity = self.config.initial_equity
        self.balance = self.config.initial_equity  # available balance
        self.open_positions: dict[int, OpenPosition] = {}
        self._last_ticker: dict[str, float] = {}   # fallback for disconnects
        self._ticker_fail_count: dict[str, int] = {}
        self._running = False
        self._recover_positions()

    # ------------------------------------------------------------------
    # Startup recovery (SPEC_SUPPLEMENT.md §8)
    # ------------------------------------------------------------------

    def _recover_positions(self):
        """Reload open positions from SQLite on restart."""
        rows = self.db.get_open_positions()
        for r in rows:
            pos = OpenPosition(
                id=r["id"], pair=r["pair"], side=r["side"],
                entry_price=r["entry_price"],
                entry_time=datetime.fromisoformat(r["entry_time"]),
                contracts=r["contracts"], margin=r["margin"],
                leverage=r["leverage"], sl_price=r["sl_price"],
                tp_price=r["tp_price"], funding_paid=r["funding_paid"],
                realized_pnl=r["realized_pnl"],
                ai_confidence=r.get("ai_confidence"), ai_reason=r.get("ai_reason"),
                mode=r.get("mode", "ai"), aggressive=bool(r.get("aggressive", False)),
            )
            # Resume funding settlement tracking from entry time
            pos.last_funding_settle = self._last_settle_before(pos.entry_time)
            self.open_positions[pos.id] = pos
            # Recompute balance: subtract margin used
            self.balance -= pos.margin
        if rows:
            logger.info(f"Recovered {len(rows)} open positions from DB")

    # ------------------------------------------------------------------
    # Market data (OKX primary, Binance fallback) — READ ONLY
    # ------------------------------------------------------------------

    def get_ticker(self, pair: str) -> float:
        """Fetch last price. OKX first, Binance fallback. Cache for disconnects."""
        # Try OKX
        price = self._fetch_okx_ticker(pair)
        if price is not None:
            self._last_ticker[pair] = price
            self._ticker_fail_count[pair] = 0
            return price
        # Fallback Binance
        price = self._fetch_binance_ticker(pair)
        if price is not None:
            self._last_ticker[pair] = price
            self._ticker_fail_count[pair] = 0
            return price
        # Both failed — use cached if available, with fail counter
        self._ticker_fail_count[pair] = self._ticker_fail_count.get(pair, 0) + 1
        if pair in self._last_ticker:
            logger.warning(f"{pair}: market data fail #{self._ticker_fail_count[pair]}, using cached")
            return self._last_ticker[pair]
        raise MarketDataError(f"Cannot fetch ticker for {pair} (both sources failed)")

    def _fetch_okx_ticker(self, pair: str) -> Optional[float]:
        inst = OKX_INST.get(pair)
        if not inst:
            return None
        try:
            r = requests.get(OKX_TICKER_URL.format(inst), timeout=8)
            r.raise_for_status()
            data = r.json().get("data", [])
            if data:
                return float(data[0]["last"])
        except Exception as e:
            logger.debug(f"OKX ticker fail for {pair}: {e}")
        return None

    def _fetch_binance_ticker(self, pair: str) -> Optional[float]:
        sym = BINANCE_INST.get(pair)
        if not sym:
            return None
        try:
            r = requests.get(BINANCE_TICKER_URL.format(sym), timeout=8)
            r.raise_for_status()
            return float(r.json()["price"])
        except Exception as e:
            logger.debug(f"Binance ticker fail for {pair}: {e}")
        return None

    def get_funding_rate(self, pair: str) -> float:
        """Current funding rate. OKX first, Binance fallback, default 0.0001."""
        fr = self._fetch_okx_funding(pair)
        if fr is not None:
            return fr
        fr = self._fetch_binance_funding(pair)
        if fr is not None:
            return fr
        logger.warning(f"{pair}: funding rate fetch failed, using default 0.0001")
        return 0.0001

    def _fetch_okx_funding(self, pair: str) -> Optional[float]:
        inst = OKX_INST.get(pair)
        if not inst:
            return None
        try:
            r = requests.get(OKX_FUNDING_URL.format(inst), timeout=8)
            r.raise_for_status()
            data = r.json().get("data", [])
            if data:
                return float(data[0]["fundingRate"])
        except Exception as e:
            logger.debug(f"OKX funding fail: {e}")
        return None

    def _fetch_binance_funding(self, pair: str) -> Optional[float]:
        sym = BINANCE_INST.get(pair)
        if not sym:
            return None
        try:
            r = requests.get(BINANCE_FUNDING_URL.format(sym), timeout=8)
            r.raise_for_status()
            data = r.json()
            if data:
                return float(data[-1]["fundingRate"])
        except Exception as e:
            logger.debug(f"Binance funding fail: {e}")
        return None

    # ------------------------------------------------------------------
    # Order placement (SPEC §4.1)
    # ------------------------------------------------------------------

    def open_order(self, decision: dict) -> Optional[int]:
        """Open a new position from an AI decision dict.

        decision keys: pair, side, position_size_pct, leverage,
                       stop_loss_pct, take_profit_pct, confidence, reason, mode
        Returns position id, or None if rejected.
        """
        pair = decision["pair"]
        side = decision["side"]
        pos_pct = decision.get("position_size_pct", 0.20)
        leverage = int(decision.get("leverage", 3))
        sl_pct = decision.get("stop_loss_pct", 0.02)
        tp_pct = decision.get("take_profit_pct", 0.04)
        confidence = decision.get("confidence", 0)
        reason = decision.get("reason", "")

        # --- Pre-trade checks ---
        if confidence < self.config.min_confidence:
            logger.info(f"Reject {pair} {side}: confidence {confidence:.2f} < {self.config.min_confidence}")
            return None
        if leverage > self.config.max_leverage:
            logger.info(f"Reject {pair} {side}: leverage {leverage} > cap {self.config.max_leverage}")
            return None
        if pos_pct > self.config.max_position_pct:
            logger.info(f"Reject {pair} {side}: pos_pct {pos_pct} > cap {self.config.max_position_pct}")
            return None
        if self.db.count_open_by_pair(pair) >= MAX_CONCURRENT_PER_PAIR:
            logger.info(f"Reject {pair} {side}: already have open position")
            return None

        # --- Get entry price (with slippage) ---
        try:
            raw_price = self.get_ticker(pair)
        except MarketDataError as e:
            logger.error(f"Reject {pair}: {e}")
            return None
        entry_price = raw_price * (1 + SLIPPAGE) if side == "long" else raw_price * (1 - SLIPPAGE)

        # --- Compute size and margin ---
        position_usdt = self.equity * pos_pct * leverage
        contracts = position_usdt / entry_price
        margin = position_usdt / leverage

        if margin > self.balance:
            logger.info(f"Reject {pair}: margin {margin:.2f} > balance {self.balance:.2f}")
            return None

        # --- Compute SL/TP prices (price-level, not % of margin) ---
        # SPEC §4.1: sl_price = price * (1 - sl_pct/leverage) for long
        # This gives the price at which loss = sl_pct of equity
        if side == "long":
            sl_price = entry_price * (1 - sl_pct / leverage)
            tp_price = entry_price * (1 + tp_pct / leverage)
        else:
            sl_price = entry_price * (1 + sl_pct / leverage)
            tp_price = entry_price * (1 - tp_pct / leverage)

        # --- Deduct entry fee from balance ---
        entry_fee = contracts * entry_price * TAKER_FEE
        self.balance -= margin + entry_fee

        # --- Persist ---
        pos_id = self.db.open_position({
            "pair": pair, "side": side, "entry_price": entry_price,
            "entry_time": _now_iso(), "contracts": contracts, "margin": margin,
            "leverage": leverage, "sl_price": sl_price, "tp_price": tp_price,
            "ai_confidence": confidence, "ai_reason": reason,
            "mode": decision.get("mode", "ai"),
            "aggressive": decision.get("aggressive", self.config.aggressive),
        })

        pos = OpenPosition(
            id=pos_id, pair=pair, side=side, entry_price=entry_price,
            entry_time=_utcnow(), contracts=contracts, margin=margin,
            leverage=leverage, sl_price=sl_price, tp_price=tp_price,
            ai_confidence=confidence, ai_reason=reason,
            mode=decision.get("mode", "ai"),
            aggressive=decision.get("aggressive", self.config.aggressive),
            last_funding_settle=self._last_settle_before(_utcnow()),
        )
        self.open_positions[pos_id] = pos
        logger.info(f"OPEN {pair} {side} lev={leverage}x entry=${entry_price:.2f} "
                    f"size=${position_usdt:.2f} SL=${sl_price:.2f} TP=${tp_price:.2f} id={pos_id}")
        return pos_id

    # ------------------------------------------------------------------
    # SL/TP/liquidation check loop (SPEC_SUPPLEMENT.md §4.1, §4.2)
    # ------------------------------------------------------------------

    def check_positions(self):
        """Check all open positions for SL/TP/liquidation. Call every ~5s."""
        for pos_id in list(self.open_positions.keys()):
            pos = self.open_positions[pos_id]
            try:
                price = self.get_ticker(pos.pair)
            except MarketDataError:
                logger.warning(f"Skip SL/TP check for {pos.pair}: no market data")
                continue

            # Funding settlement (may accrue before checking SL/TP)
            self._maybe_settle_funding(pos)

            # Liquidation check first (most urgent)
            liq_price = self._liquidation_price(pos)
            if self._is_liquidated(pos, price, liq_price):
                self._liquidate(pos, price)
                continue

            # SL/TP check (SL priority when both hit in same bar)
            if pos.side == "long":
                if price <= pos.sl_price:
                    self._close(pos, pos.sl_price, "stop_loss")
                elif price >= pos.tp_price:
                    self._close(pos, pos.tp_price, "take_profit")
            else:
                if price >= pos.sl_price:
                    self._close(pos, pos.sl_price, "stop_loss")
                elif price <= pos.tp_price:
                    self._close(pos, pos.tp_price, "take_profit")

    def _close(self, pos: OpenPosition, exit_price: float, reason: str):
        """Close a position at given price, realize PnL, update balance."""
        # Exit price with slippage against the closing direction
        if reason == "take_profit":
            # Closing a long TP = sell, slightly lower; short TP = buy, slightly higher
            exit_price = exit_price * (1 - SLIPPAGE) if pos.side == "long" else exit_price * (1 + SLIPPAGE)
        # For stop_loss, use the SL price as-is (already adverse)

        # Realized PnL
        if pos.side == "long":
            gross_pnl = pos.contracts * (exit_price - pos.entry_price)
        else:
            gross_pnl = pos.contracts * (pos.entry_price - exit_price)

        exit_fee = pos.contracts * exit_price * TAKER_FEE
        net_pnl = gross_pnl - exit_fee - pos.funding_paid

        # Update balance: return margin + net PnL
        self.balance += pos.margin + net_pnl
        self.equity = self.balance + sum(
            self._unrealized(p) for p in self.open_positions.values() if p.id != pos.id
        )

        self.db.close_position(pos.id, exit_price, _now_iso(), reason, net_pnl, pos.funding_paid)
        del self.open_positions[pos.id]
        logger.info(f"CLOSE {pos.pair} {pos.side} reason={reason} exit=${exit_price:.2f} "
                    f"pnl=${net_pnl:+.2f} id={pos.id}")

    def _liquidate(self, pos: OpenPosition, price: float):
        """Force-close at liquidation price."""
        liq_price = self._liquidation_price(pos)
        # Loss = full margin
        net_pnl = -pos.margin
        self.balance += 0  # margin fully consumed
        self.equity = self.balance + sum(
            self._unrealized(p) for p in self.open_positions.values() if p.id != pos.id
        )
        self.db.liquidate_position(pos.id, liq_price, _now_iso(), net_pnl, pos.funding_paid)
        del self.open_positions[pos.id]
        logger.warning(f"LIQUIDATED {pos.pair} {pos.side} @${liq_price:.2f} "
                       f"margin=${pos.margin:.2f} lost id={pos.id}")

    # ------------------------------------------------------------------
    # Funding rate settlement (SPEC_SUPPLEMENT.md §4.4)
    # ------------------------------------------------------------------

    def _last_settle_before(self, t: datetime) -> datetime:
        """Most recent funding settlement time before t (00/08/16 UTC)."""
        day = t.date()
        for h in reversed(FUNDING_HOURS):
            settle = datetime(day.year, day.month, day.day, h, 0, 0, tzinfo=timezone.utc)
            if settle <= t:
                return settle
        # Before 00:00 today → 16:00 yesterday
        from datetime import timedelta
        yesterday = day - timedelta(days=1)
        return datetime(yesterday.year, yesterday.month, yesterday.day, 16, 0, 0, tzinfo=timezone.utc)

    def _maybe_settle_funding(self, pos: OpenPosition):
        """Settle funding if a new settlement time has passed since last settle."""
        now = _utcnow()
        last_settle = self._last_settle_before(now)
        if pos.last_funding_settle is None or last_settle > pos.last_funding_settle:
            # Settle for the period [entry_or_last_settle, last_settle]
            start = pos.last_funding_settle or self._last_settle_before(pos.entry_time)
            if pos.entry_time > start:
                start = self._last_settle_before(pos.entry_time)
            hours_held = (last_settle - start).total_seconds() / 3600
            if hours_held <= 0:
                pos.last_funding_settle = last_settle
                return

            funding_rate = self.get_funding_rate(pos.pair)
            # Pro-rate if held less than 8h in this period
            ratio = min(hours_held / 8.0, 1.0)
            charge = pos.contracts * pos.entry_price * funding_rate * ratio

            if pos.side == "long":
                pos.funding_paid += charge   # long pays when rate > 0
            else:
                pos.funding_paid -= charge   # short receives when rate > 0

            # Funding affects margin: long paying reduces available margin
            self.db.update_funding(pos.id, pos.funding_paid)
            pos.last_funding_settle = last_settle
            logger.debug(f"FUNDING {pos.pair} {pos.side} rate={funding_rate:.6f} "
                         f"charge=${charge:.4f} accrued=${pos.funding_paid:.4f}")

    # ------------------------------------------------------------------
    # Liquidation price (dynamic, SPEC_SUPPLEMENT.md §4.3)
    # ------------------------------------------------------------------

    def _liquidation_price(self, pos: OpenPosition) -> float:
        """Dynamic liquidation price = entry adjusted for accrued funding + PnL."""
        # Effective margin remaining = initial margin - funding paid + realized PnL
        # (funding_paid > 0 means long has paid, reducing margin)
        effective_margin = pos.margin - max(pos.funding_paid, 0) + min(pos.funding_paid, 0) * 0
        # Simpler: margin consumed by positive funding (long) or augmented by negative (short)
        if pos.side == "long":
            effective_margin = pos.margin - max(pos.funding_paid, 0)
            # Liquidation when loss + maintenance >= effective_margin
            # loss = contracts * (entry - liq); maintenance = contracts * liq * MAINTENANCE
            # Solve: contracts*(entry-liq) + contracts*liq*MAINT = effective_margin
            # entry - liq + liq*MAINT = effective_margin / contracts
            # liq*(1 - MAINT) = entry - effective_margin/contracts
            if pos.contracts <= 0:
                return pos.entry_price
            liq = (pos.entry_price - effective_margin / pos.contracts) / (1 - MAINTENANCE_MARGIN)
            return liq
        else:
            effective_margin = pos.margin - max(-pos.funding_paid, 0)
            if pos.contracts <= 0:
                return pos.entry_price
            liq = (pos.entry_price + effective_margin / pos.contracts) / (1 + MAINTENANCE_MARGIN)
            return liq

    def _is_liquidated(self, pos: OpenPosition, price: float, liq_price: float) -> bool:
        if pos.side == "long":
            return price <= liq_price
        return price >= liq_price

    # ------------------------------------------------------------------
    # Valuation
    # ------------------------------------------------------------------

    def _unrealized(self, pos: OpenPosition) -> float:
        """Unrealized PnL for a position at current market price."""
        try:
            price = self.get_ticker(pos.pair)
        except MarketDataError:
            price = self._last_ticker.get(pos.pair, pos.entry_price)
        if pos.side == "long":
            return pos.contracts * (price - pos.entry_price)
        return pos.contracts * (pos.entry_price - price)

    def total_unrealized(self) -> float:
        return sum(self._unrealized(p) for p in self.open_positions.values())

    def total_equity(self) -> float:
        """Equity = balance + unrealized PnL of open positions."""
        return self.balance + self.total_unrealized()

    def used_margin(self) -> float:
        return sum(p.margin for p in self.open_positions.values())

    # ------------------------------------------------------------------
    # Equity snapshot + main loops
    # ------------------------------------------------------------------

    def snapshot_equity(self):
        """Persist an equity snapshot (call every ~30s)."""
        self.equity = self.total_equity()
        self.db.save_equity_snapshot(
            equity=self.equity, balance=self.balance,
            unrealized_pnl=self.total_unrealized(),
            open_count=len(self.open_positions),
        )

    async def sl_tp_loop(self, interval: float = 5.0):
        """Independent SL/TP/liquidation check loop (every 5s)."""
        self._running = True
        logger.info(f"SL/TP check loop started (interval={interval}s)")
        while self._running:
            try:
                self.check_positions()
            except Exception as e:
                logger.error(f"SL/TP loop error: {e}", exc_info=True)
            await asyncio.sleep(interval)

    async def snapshot_loop(self, interval: float = 30.0):
        """Equity snapshot loop (every 30s)."""
        self._running = True
        logger.info(f"Equity snapshot loop started (interval={interval}s)")
        while self._running:
            try:
                self.snapshot_equity()
            except Exception as e:
                logger.error(f"Snapshot loop error: {e}", exc_info=True)
            await asyncio.sleep(interval)

    def stop(self):
        self._running = False
