"""Periodic review report generator.

Reads simulation trading data from SQLite and generates a structured
performance report (Markdown or JSON).

Usage:
  python scripts/periodic_review.py                    # latest 7 days
  python scripts/periodic_review.py --days 14          # last 14 days
  python scripts/periodic_review.py --format json      # JSON output
  python scripts/periodic_review.py --save review.md   # save to file
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "sim_trader.db"


def query_trades(conn, since: datetime) -> list[dict]:
    """Fetch closed trades since given timestamp."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT pair, side, entry_price, exit_price, realized_pnl,
               exit_reason, leverage, entry_time, exit_time, mode
        FROM positions
        WHERE status = 'closed' AND exit_time >= ?
    """, (since.isoformat(),))
    return [dict(zip([d[0] for d in cursor.description], row)) for row in cursor.fetchall()]


def query_equity(conn, since: datetime) -> list[dict]:
    """Fetch equity snapshots since given timestamp."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT timestamp, equity, balance, unrealized_pnl, open_positions
        FROM equity_snapshots
        WHERE timestamp >= ?
        ORDER BY timestamp ASC
    """, (since.isoformat(),))
    return [dict(zip([d[0] for d in cursor.description], row)) for row in cursor.fetchall()]


def compute_metrics(trades: list[dict], equity: list[dict]) -> dict:
    """Compute performance metrics from trades and equity history."""
    if not trades:
        return {"total_trades": 0, "message": "No trades in period"}

    wins = [t for t in trades if t["realized_pnl"] > 0]
    losses = [t for t in trades if t["realized_pnl"] <= 0]

    total_pnl = sum(t["realized_pnl"] for t in trades)
    win_rate = len(wins) / len(trades) if trades else 0

    avg_win = sum(t["realized_pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["realized_pnl"] for t in losses) / len(losses) if losses else 0

    # Profit factor
    gross_profit = sum(t["realized_pnl"] for t in wins)
    gross_loss = abs(sum(t["realized_pnl"] for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # By mode breakdown
    by_mode = {}
    for t in trades:
        mode = t.get("mode", "unknown")
        if mode not in by_mode:
            by_mode[mode] = {"trades": 0, "wins": 0, "pnl": 0}
        by_mode[mode]["trades"] += 1
        if t["realized_pnl"] > 0:
            by_mode[mode]["wins"] += 1
        by_mode[mode]["pnl"] += t["realized_pnl"]

    for mode in by_mode:
        by_mode[mode]["win_rate"] = by_mode[mode]["wins"] / by_mode[mode]["trades"]

    # By pair breakdown
    by_pair = {}
    for t in trades:
        pair = t["pair"]
        if pair not in by_pair:
            by_pair[pair] = {"trades": 0, "wins": 0, "pnl": 0}
        by_pair[pair]["trades"] += 1
        if t["realized_pnl"] > 0:
            by_pair[pair]["wins"] += 1
        by_pair[pair]["pnl"] += t["realized_pnl"]

    for pair in by_pair:
        by_pair[pair]["win_rate"] = by_pair[pair]["wins"] / by_pair[pair]["trades"]

    # By exit reason
    by_reason = {}
    for t in trades:
        reason = t.get("exit_reason", "unknown")
        by_reason[reason] = by_reason.get(reason, 0) + 1

    # Max drawdown from equity history
    max_dd = 0
    peak = 0
    for snap in equity:
        eq = snap["equity"]
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    return {
        "total_trades": len(trades),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": round(win_rate, 4),
        "total_pnl": round(total_pnl, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 3),
        "max_drawdown": round(max_dd, 4),
        "by_mode": by_mode,
        "by_pair": by_pair,
        "by_exit_reason": by_reason,
    }


def generate_report(days: int, fmt: str = "markdown") -> str:
    """Generate periodic review report."""
    since = datetime.utcnow() - timedelta(days=days)

    if not DB_PATH.exists():
        return f"Error: Database not found at {DB_PATH}"

    conn = sqlite3.connect(str(DB_PATH))
    try:
        trades = query_trades(conn, since)
        equity = query_equity(conn, since)
    finally:
        conn.close()

    metrics = compute_metrics(trades, equity)

    if fmt == "json":
        return json.dumps(metrics, indent=2)

    # Markdown format
    lines = [
        f"# Periodic Review Report",
        f"**Period**: Last {days} days (since {since.strftime('%Y-%m-%d %H:%M')} UTC)",
        f"**Generated**: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC",
        "",
    ]

    if metrics["total_trades"] == 0:
        lines.append("No trades in period.")
        return "\n".join(lines)

    lines.extend([
        "## Summary",
        f"- Total trades: {metrics['total_trades']}",
        f"- Winning trades: {metrics['winning_trades']}",
        f"- Losing trades: {metrics['losing_trades']}",
        f"- Win rate: {metrics['win_rate']*100:.1f}%",
        f"- Total PnL: ${metrics['total_pnl']:+.2f}",
        f"- Average win: ${metrics['avg_win']:.2f}",
        f"- Average loss: ${metrics['avg_loss']:.2f}",
        f"- Profit factor: {metrics['profit_factor']:.2f}",
        f"- Max drawdown: {metrics['max_drawdown']*100:.1f}%",
        "",
        "## By Mode",
    ])

    for mode, m in metrics["by_mode"].items():
        lines.append(f"- **{mode}**: {m['trades']} trades, "
                     f"{m['win_rate']*100:.1f}% win rate, "
                     f"PnL ${m['pnl']:+.2f}")

    lines.extend(["", "## By Pair"])
    for pair, p in metrics["by_pair"].items():
        lines.append(f"- **{pair}**: {p['trades']} trades, "
                     f"{p['win_rate']*100:.1f}% win rate, "
                     f"PnL ${p['pnl']:+.2f}")

    lines.extend(["", "## By Exit Reason"])
    for reason, count in metrics["by_exit_reason"].items():
        lines.append(f"- {reason}: {count}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate periodic review report")
    parser.add_argument("--days", type=int, default=7, help="Report period in days (default: 7)")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown",
                        help="Output format (default: markdown)")
    parser.add_argument("--save", help="Save report to file")
    args = parser.parse_args()

    report = generate_report(args.days, args.format)

    if args.save:
        Path(args.save).write_text(report, encoding="utf-8")
        print(f"Report saved to {args.save}")
    else:
        print(report)


if __name__ == "__main__":
    main()
