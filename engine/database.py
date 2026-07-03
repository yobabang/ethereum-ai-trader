"""SQLite persistence layer for the simulation broker.

Single-file, zero-config storage for orders/positions, equity snapshots,
and AI decisions. Designed to survive restarts — open positions are reloaded
into memory on startup.

Schema matches SPEC_SUPPLEMENT.md section 5 (with funding_paid and ai_reason
columns carried through from the original SPEC v0.2.0).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "sim_trader.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair TEXT NOT NULL,
    side TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    entry_price REAL NOT NULL,
    entry_time TEXT NOT NULL,
    exit_price REAL,
    exit_time TEXT,
    exit_reason TEXT,
    contracts REAL NOT NULL,
    margin REAL NOT NULL,
    leverage INTEGER NOT NULL,
    sl_price REAL NOT NULL,
    tp_price REAL NOT NULL,
    realized_pnl REAL DEFAULT 0,
    funding_paid REAL DEFAULT 0,
    ai_confidence REAL,
    ai_reason TEXT,
    mode TEXT DEFAULT 'ai',
    aggressive INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_positions_pair ON positions(pair);

CREATE TABLE IF NOT EXISTS equity_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    equity REAL NOT NULL,
    balance REAL NOT NULL,
    unrealized_pnl REAL DEFAULT 0,
    open_positions INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_equity_ts ON equity_snapshots(timestamp);

CREATE TABLE IF NOT EXISTS ai_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    pair TEXT NOT NULL,
    action TEXT NOT NULL,
    confidence REAL,
    expected_return REAL,
    position_size_pct REAL,
    stop_loss_pct REAL,
    take_profit_pct REAL,
    leverage INTEGER,
    reason TEXT,
    executed INTEGER DEFAULT 0,
    mode TEXT DEFAULT 'ai',
    aggressive INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_decisions_ts ON ai_decisions(timestamp);
"""


class Database:
    """Thin SQLite wrapper. Thread-safe via per-call connections."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = str(db_path)
        self._init_schema()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self):
        with self._conn() as c:
            c.executescript(SCHEMA)
        logger.info(f"Database initialized at {self.db_path}")

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    def open_position(self, pos: dict) -> int:
        """Insert a new open position. Returns the row id."""
        with self._conn() as c:
            cur = c.execute(
                """INSERT INTO positions
                   (pair, side, status, entry_price, entry_time, contracts, margin,
                    leverage, sl_price, tp_price, realized_pnl, funding_paid,
                    ai_confidence, ai_reason, mode, aggressive)
                   VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?)""",
                (pos["pair"], pos["side"], pos["entry_price"], pos["entry_time"],
                 pos["contracts"], pos["margin"], pos["leverage"],
                 pos["sl_price"], pos["tp_price"],
                 pos.get("ai_confidence"), pos.get("ai_reason"),
                 pos.get("mode", "ai"), int(pos.get("aggressive", False))),
            )
            return cur.lastrowid

    def close_position(self, pos_id: int, exit_price: float, exit_time: str,
                       exit_reason: str, realized_pnl: float, funding_paid: float):
        """Mark a position closed with final PnL and funding."""
        with self._conn() as c:
            c.execute(
                """UPDATE positions SET
                   status='closed', exit_price=?, exit_time=?, exit_reason=?,
                   realized_pnl=?, funding_paid=? WHERE id=?""",
                (exit_price, exit_time, exit_reason, realized_pnl, funding_paid, pos_id),
            )

    def liquidate_position(self, pos_id: int, exit_price: float, exit_time: str,
                           realized_pnl: float, funding_paid: float):
        """Mark a position liquidated (distinct exit_reason for reporting)."""
        with self._conn() as c:
            c.execute(
                """UPDATE positions SET
                   status='liquidated', exit_price=?, exit_time=?, exit_reason='liquidated',
                   realized_pnl=?, funding_paid=? WHERE id=?""",
                (exit_price, exit_time, realized_pnl, funding_paid, pos_id),
            )

    def update_funding(self, pos_id: int, funding_paid: float):
        """Accumulate funding charges on an open position."""
        with self._conn() as c:
            c.execute(
                "UPDATE positions SET funding_paid=? WHERE id=?",
                (funding_paid, pos_id),
            )

    def get_open_positions(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM positions WHERE status='open' ORDER BY entry_time"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_position(self, pos_id: int) -> Optional[dict]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM positions WHERE id=?", (pos_id,)).fetchone()
            return dict(row) if row else None

    def get_recent_positions(self, limit: int = 50) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM positions ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def count_open_by_pair(self, pair: str) -> int:
        with self._conn() as c:
            row = c.execute(
                "SELECT COUNT(*) AS n FROM positions WHERE pair=? AND status='open'",
                (pair,),
            ).fetchone()
            return row["n"]

    # ------------------------------------------------------------------
    # Equity snapshots
    # ------------------------------------------------------------------

    def save_equity_snapshot(self, equity: float, balance: float,
                             unrealized_pnl: float, open_count: int):
        with self._conn() as c:
            c.execute(
                """INSERT INTO equity_snapshots
                   (timestamp, equity, balance, unrealized_pnl, open_positions)
                   VALUES (?, ?, ?, ?, ?)""",
                (_now_iso(), equity, balance, unrealized_pnl, open_count),
            )

    def get_equity_history(self, days: int = 7) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                """SELECT * FROM equity_snapshots
                   WHERE timestamp >= datetime('now', ?)
                   ORDER BY timestamp ASC""",
                (f"-{days} days",),
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # AI decisions
    # ------------------------------------------------------------------

    def log_decision(self, decision: dict):
        with self._conn() as c:
            c.execute(
                """INSERT INTO ai_decisions
                   (timestamp, pair, action, confidence, expected_return,
                    position_size_pct, stop_loss_pct, take_profit_pct, leverage,
                    reason, executed, mode, aggressive)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (_now_iso(), decision["pair"], decision["action"],
                 decision.get("confidence"), decision.get("expected_return"),
                 decision.get("position_size_pct"), decision.get("stop_loss_pct"),
                 decision.get("take_profit_pct"), decision.get("leverage"),
                 decision.get("reason"), int(decision.get("executed", False)),
                 decision.get("mode", "ai"), int(decision.get("aggressive", False))),
            )

    def get_recent_decisions(self, limit: int = 50) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM ai_decisions ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Stats / cleanup
    # ------------------------------------------------------------------

    def get_account_stats(self, initial_equity: float) -> dict:
        """Aggregate stats for the account summary endpoint."""
        with self._conn() as c:
            total = c.execute("SELECT COUNT(*) AS n FROM positions").fetchone()["n"]
            wins = c.execute(
                "SELECT COUNT(*) AS n FROM positions WHERE realized_pnl > 0"
            ).fetchone()["n"]
            last_eq = c.execute(
                "SELECT equity FROM equity_snapshots ORDER BY id DESC LIMIT 1"
            ).fetchone()
            # Max drawdown from snapshots
            peak_row = c.execute(
                "SELECT MAX(equity) AS peak FROM equity_snapshots"
            ).fetchone()
            min_row = c.execute(
                """SELECT MIN(equity) AS trough FROM equity_snapshots
                   WHERE id >= (SELECT id FROM equity_snapshots
                                WHERE equity = (SELECT MAX(equity) FROM equity_snapshots)
                                LIMIT 1)"""
            ).fetchone() if peak_row and peak_row["peak"] else None

        equity = last_eq["equity"] if last_eq else initial_equity
        peak = peak_row["peak"] if peak_row and peak_row["peak"] else initial_equity
        # Approximate max drawdown: biggest peak-to-trough drop in snapshots
        max_dd = 0.0
        with self._conn() as c:
            rows = c.execute("SELECT equity FROM equity_snapshots ORDER BY id ASC").fetchall()
            running_peak = initial_equity
            for r in rows:
                e = r["equity"]
                if e > running_peak:
                    running_peak = e
                dd = (running_peak - e) / running_peak if running_peak > 0 else 0
                if dd > max_dd:
                    max_dd = dd

        return {
            "initial_equity": initial_equity,
            "equity": equity,
            "total_trades": total,
            "winning_trades": wins,
            "win_rate": wins / total if total > 0 else 0,
            "max_drawdown": max_dd,
        }

    def cleanup_old(self, snapshots_days: int = 30, decisions_days: int = 90):
        """Prune stale snapshots and decisions. Run on startup."""
        with self._conn() as c:
            c.execute(
                "DELETE FROM equity_snapshots WHERE timestamp < datetime('now', ?)",
                (f"-{snapshots_days} days",),
            )
            c.execute(
                "DELETE FROM ai_decisions WHERE timestamp < datetime('now', ?)",
                (f"-{decisions_days} days",),
            )
        logger.info(f"Cleaned up snapshots > {snapshots_days}d, decisions > {decisions_days}d")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
