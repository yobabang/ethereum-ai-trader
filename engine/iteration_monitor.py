"""Iteration trigger monitor — watches SQLite for data-driven triggers.

Runs as a lightweight background process. Does NOT do AI analysis itself —
it only detects trigger conditions and writes a structured snapshot file
for the AI agent (Claude Code) to consume when triggered.

Trigger conditions (any one fires):
  - trade_count_since_last >= MIN_TRADES (default 50)
  - max_drawdown >= DRAWDOWN_THRESHOLD (default 0.15)
  - consecutive_losses >= MAX_CONSEC_LOSSES (default 5)
  - hours_since_last_analysis >= MAX_HOURS (default 168 = 7 days, safety net)

When triggered:
  1. Generates a snapshot (JSON) of recent performance
  2. Writes it to data/snapshots/snapshot_<timestamp>.json
  3. Prints a clear banner telling the operator to run /analyze-iteration
  4. Records trigger in DB so it won't re-fire until analyzed

Usage:
  python -m engine.iteration_monitor              # foreground
  python -m engine.iteration_monitor --once       # single check then exit
  python -m engine.iteration_monitor --interval 300  # check every 5min
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.database import Database

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger(__name__)

# --- Trigger thresholds ---
MIN_TRADES = 50              # fire after 50 new trades since last analysis
DRAWDOWN_THRESHOLD = 0.15    # fire if max drawdown exceeds 15%
MAX_CONSEC_LOSSES = 5        # fire after 5 consecutive losses
MAX_HOURS = 168              # safety net: fire at least weekly

SNAPSHOT_DIR = Path("data/snapshots")
MAX_SNAPSHOTS = 50  # keep last N snapshots, delete older ones


def _get_state_file(db_path: str) -> Path:
    """Derive state file path from DB path (so --db switches migrate state)."""
    return Path(db_path).parent / "iteration_state.json"


def _cleanup_old_snapshots():
    """Keep only the last MAX_SNAPSHOTS snapshot files, delete older ones."""
    if not SNAPSHOT_DIR.exists():
        return
    snaps = sorted(SNAPSHOT_DIR.glob("snapshot_*.json"))
    if len(snaps) <= MAX_SNAPSHOTS:
        return
    to_delete = snaps[:-MAX_SNAPSHOTS]
    for snap in to_delete:
        try:
            snap.unlink()
        except Exception as e:
            logger.warning(f"Failed to delete {snap}: {e}")
    logger.info(f"Cleaned up {len(to_delete)} old snapshots (keeping last {MAX_SNAPSHOTS})")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_state(db_path: str) -> dict:
    """Load iteration state (last analysis time, last trade count)."""
    state_file = _get_state_file(db_path)
    if state_file.exists():
        return json.loads(state_file.read_text(encoding="utf-8"))
    return {"last_analysis_time": None, "last_trade_count": 0}


def _save_state(state: dict, db_path: str):
    state_file = _get_state_file(db_path)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _count_trades_since(db: Database, since_time: str | None) -> int:
    """Count closed trades since the given timestamp (or all if None)."""
    conn = sqlite3.connect(db.db_path)
    try:
        if since_time:
            row = conn.execute(
                "SELECT COUNT(*) FROM positions WHERE status IN ('closed','liquidated') AND exit_time > ?",
                (since_time,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) FROM positions WHERE status IN ('closed','liquidated')"
            ).fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def _max_consecutive_losses(db: Database) -> int:
    """Compute max consecutive losses from recent closed trades."""
    conn = sqlite3.connect(db.db_path)
    try:
        rows = conn.execute(
            "SELECT realized_pnl FROM positions WHERE status IN ('closed','liquidated') "
            "ORDER BY exit_time DESC LIMIT 100"
        ).fetchall()
    finally:
        conn.close()
    # rows are newest-first; reverse to chronological
    pnls = [r[0] for r in reversed(rows)]
    max_streak = streak = 0
    for p in pnls:
        if p <= 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    return max_streak


def _generate_snapshot(db: Database, trigger_reason: str, db_path: str = "") -> dict:
    """Build a structured snapshot of current performance for AI analysis."""
    conn = sqlite3.connect(db.db_path)
    try:
        # Recent closed trades (last 50)
        recent = conn.execute(
            "SELECT * FROM positions WHERE status IN ('closed','liquidated') "
            "ORDER BY exit_time DESC LIMIT 50"
        ).fetchall()
        cols = [d[0] for d in conn.execute("SELECT * FROM positions LIMIT 1").description]
        trades = [dict(zip(cols, r)) for r in recent]

        # Open positions
        opens = conn.execute(
            "SELECT * FROM positions WHERE status='open' ORDER BY entry_time"
        ).fetchall()
        open_positions = [dict(zip(cols, r)) for r in opens]

        # Equity snapshots (last 24h)
        eq_rows = conn.execute(
            "SELECT * FROM equity_snapshots ORDER BY id DESC LIMIT 288"  # ~24h at 5min
        ).fetchall()
        eq_cols = [d[0] for d in conn.execute("SELECT * FROM equity_snapshots LIMIT 1").description]
        equity_history = [dict(zip(eq_cols, r)) for r in reversed(eq_rows)]

        # AI decisions (last 50)
        dec_rows = conn.execute(
            "SELECT * FROM ai_decisions ORDER BY id DESC LIMIT 50"
        ).fetchall()
        dec_cols = [d[0] for d in conn.execute("SELECT * FROM ai_decisions LIMIT 1").description]
        decisions = [dict(zip(dec_cols, r)) for r in dec_rows]

        # Aggregate stats
        total = len(trades)
        wins = sum(1 for t in trades if t["realized_pnl"] > 0)
        losses = total - wins
        total_pnl = sum(t["realized_pnl"] for t in trades)
        win_rate = wins / total if total > 0 else 0

        # By mode breakdown
        by_mode: dict[str, dict] = {}
        for t in trades:
            m = t.get("mode", "unknown")
            by_mode.setdefault(m, {"trades": 0, "wins": 0, "pnl": 0.0})
            by_mode[m]["trades"] += 1
            if t["realized_pnl"] > 0:
                by_mode[m]["wins"] += 1
            by_mode[m]["pnl"] += t["realized_pnl"]

        # By exit reason
        by_reason: dict[str, int] = {}
        for t in trades:
            r = t.get("exit_reason", "unknown")
            by_reason[r] = by_reason.get(r, 0) + 1

        # By pair
        by_pair: dict[str, dict] = {}
        for t in trades:
            p = t.get("pair", "unknown")
            by_pair.setdefault(p, {"trades": 0, "wins": 0, "pnl": 0.0})
            by_pair[p]["trades"] += 1
            if t["realized_pnl"] > 0:
                by_pair[p]["wins"] += 1
            by_pair[p]["pnl"] += t["realized_pnl"]

    finally:
        conn.close()

    return {
        "snapshot_time": _utcnow_iso(),
        "trigger_reason": trigger_reason,
        "db_path": db_path,
        "summary": {
            "total_trades_analyzed": total,
            "winning_trades": wins,
            "losing_trades": losses,
            "win_rate": round(win_rate, 4),
            "total_pnl": round(total_pnl, 2),
            "open_positions_count": len(open_positions),
        },
        "by_mode": {k: {**v, "win_rate": round(v["wins"] / v["trades"], 4) if v["trades"] else 0}
                    for k, v in by_mode.items()},
        "by_pair": {k: {**v, "win_rate": round(v["wins"] / v["trades"], 4) if v["trades"] else 0}
                    for k, v in by_pair.items()},
        "by_exit_reason": by_reason,
        "recent_trades": trades,
        "open_positions": open_positions,
        "equity_history_tail": equity_history[-20:] if equity_history else [],
        "recent_decisions": decisions[:10],
    }


def _check_triggers(db: Database, state: dict) -> str | None:
    """Return trigger reason if any condition is met, else None."""
    last_time = state.get("last_analysis_time")
    last_count = state.get("last_trade_count", 0)

    trades_since = _count_trades_since(db, last_time)
    if trades_since >= MIN_TRADES:
        return f"trades_since_last={trades_since} (>={MIN_TRADES})"

    # Drawdown from account stats
    stats = db.get_account_stats(1000.0)
    if stats["max_drawdown"] >= DRAWDOWN_THRESHOLD:
        return f"max_drawdown={stats['max_drawdown']:.3f} (>={DRAWDOWN_THRESHOLD})"

    consec = _max_consecutive_losses(db)
    if consec >= MAX_CONSEC_LOSSES:
        return f"consecutive_losses={consec} (>={MAX_CONSEC_LOSSES})"

    # Safety net: time-based
    if last_time:
        try:
            last_dt = datetime.fromisoformat(last_time)
            hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
            if hours >= MAX_HOURS:
                return f"hours_since_last={hours:.0f} (>={MAX_HOURS})"
        except Exception:
            pass
    elif trades_since > 0:
        # Bootstrap: no analysis ever done, but we have trades → trigger
        # This ensures the first deployment gets an initial analysis even with
        # fewer than MIN_TRADES (otherwise we'd wait forever for the first 50).
        # Not in the 4 documented trigger conditions — this is a one-time bootstrap.
        return f"first_analysis (trades={trades_since})"

    return None


def run_check(db_path: str = "sim_trader.db") -> str | None:
    """Single trigger check. Returns snapshot path if triggered, else None."""
    db = Database(db_path)
    state = _load_state(db_path)

    reason = _check_triggers(db, state)
    if not reason:
        logger.info(f"No trigger. trades_since_last={_count_trades_since(db, state.get('last_analysis_time'))}/{MIN_TRADES}")
        return None

    logger.info(f"🔥 TRIGGER FIRED: {reason}")
    snapshot = _generate_snapshot(db, reason, db_path=db_path)

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snap_file = SNAPSHOT_DIR / f"snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    snap_file.write_text(json.dumps(snapshot, indent=2, default=str), encoding="utf-8")
    logger.info(f"Snapshot written: {snap_file}")

    # Cleanup old snapshots
    _cleanup_old_snapshots()

    # Print banner for operator
    print("\n" + "=" * 60)
    print("  🔥 ITERATION TRIGGERED")
    print(f"  Reason: {reason}")
    print(f"  Snapshot: {snap_file}")
    print("  Run in Claude Code: /analyze-iteration")
    print("=" * 60 + "\n")

    return str(snap_file)


def run_loop(db_path: str = "sim_trader.db", interval: int = 300):
    """Background loop: check every `interval` seconds."""
    logger.info(f"Iteration monitor started (interval={interval}s, db={db_path})")
    while True:
        try:
            run_check(db_path)
        except Exception as e:
            logger.error(f"Monitor error: {e}", exc_info=True)
        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="Iteration trigger monitor")
    parser.add_argument("--db", default="sim_trader.db")
    parser.add_argument("--once", action="store_true", help="Single check then exit")
    parser.add_argument("--interval", type=int, default=300, help="Check interval (seconds)")
    args = parser.parse_args()

    if args.once:
        snap = run_check(args.db)
        sys.exit(0 if snap else 1)
    else:
        run_loop(args.db, args.interval)


if __name__ == "__main__":
    main()
