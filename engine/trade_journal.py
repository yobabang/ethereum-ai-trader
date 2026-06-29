"""Trade Journal — local archive of every trade for AI operator review.

Every entry, exit, and decision is saved to disk in structured format.
The AI operator (Claude) can read this journal to monitor performance
and detect anomalies.

Journal structure:
  ethereum-ai-trader/journal/
    trades_2026-06.jsonl    — all trades for June 2026
    decisions_2026-06.jsonl — all AI decisions
    daily_2026-06-28.json   — daily summary
    anomalies.jsonl         — detected anomalies
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

JOURNAL_DIR = Path("../ethereum-ai-trader/journal")


class TradeJournal:
    """Persistent trade journal for AI operator monitoring."""

    def __init__(self, journal_dir: str | None = None):
        self.dir = Path(journal_dir) if journal_dir else JOURNAL_DIR
        self.dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Trade recording
    # ------------------------------------------------------------------

    def record_entry(
        self,
        pair: str,
        side: str,
        entry_price: float,
        amount: float,
        leverage: int,
        stop_loss: float,
        take_profit: float,
        confidence: float,
        expected_return: float,
        regime: str,
    ) -> str:
        """Record a trade entry. Returns trade_id."""
        trade = {
            "type": "entry",
            "trade_id": datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f"),
            "timestamp": datetime.now(UTC).isoformat(),
            "pair": pair,
            "side": side,
            "entry_price": entry_price,
            "amount": amount,
            "leverage": leverage,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "confidence": confidence,
            "expected_return": expected_return,
            "regime": regime,
            "status": "open",
        }
        self._append_trade(trade)
        return trade["trade_id"]

    def record_exit(
        self,
        trade_id: str,
        exit_price: float,
        pnl: float,
        pnl_pct: float,
        exit_reason: str,
        duration_hours: float,
    ) -> dict:
        """Record a trade exit."""
        trade = {
            "type": "exit",
            "trade_id": trade_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "exit_price": exit_price,
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 4),
            "exit_reason": exit_reason,
            "duration_hours": round(duration_hours, 2),
            "status": "closed",
        }
        self._append_trade(trade)
        return trade

    def record_decision(
        self,
        action: str,
        reason: str,
        confidence: float,
        expected_return: float,
        position_size_pct: float,
        stop_loss_pct: float,
        take_profit_pct: float,
        leverage: int,
    ) -> None:
        """Record an AI decision (even if it resulted in HOLD)."""
        decision = {
            "timestamp": datetime.now(UTC).isoformat(),
            "action": action,
            "reason": reason,
            "confidence": confidence,
            "expected_return": expected_return,
            "position_size_pct": position_size_pct,
            "stop_loss_pct": stop_loss_pct,
            "take_profit_pct": take_profit_pct,
            "leverage": leverage,
        }
        self._append_jsonl("decisions", decision)

    # ------------------------------------------------------------------
    # Anomaly detection for AI operator
    # ------------------------------------------------------------------

    def record_anomaly(self, anomaly_type: str, detail: str, severity: str = "warning") -> None:
        """Record an anomaly for AI operator review."""
        anomaly = {
            "timestamp": datetime.now(UTC).isoformat(),
            "type": anomaly_type,
            "detail": detail,
            "severity": severity,
        }
        self._append_jsonl("anomalies", anomaly)
        if severity == "critical":
            logger.error(f"CRITICAL ANOMALY: {anomaly_type} — {detail}")
        else:
            logger.warning(f"Anomaly: {anomaly_type} — {detail}")

    def check_anomalies(self, max_drawdown_pct: float = 0.15, max_consecutive_losses: int = 5) -> list[dict]:
        """Check for trading anomalies. Returns list of issues found."""
        issues = []

        # Check recent trades for consecutive losses
        recent = self.get_recent_trades(20)
        closed = [t for t in recent if t.get("status") == "closed"]

        consecutive = 0
        for t in reversed(closed):
            if t.get("pnl", 0) <= 0:
                consecutive += 1
            else:
                break
        if consecutive >= max_consecutive_losses:
            issues.append({
                "type": "consecutive_losses",
                "count": consecutive,
                "severity": "critical" if consecutive >= 5 else "warning",
                "message": f"{consecutive} consecutive losing trades detected",
            })

        # Check drawdown from daily summaries
        daily = self.get_daily_summaries(7)
        if daily:
            total_pnl = sum(d.get("total_pnl", 0) for d in daily)
            if total_pnl < 0 and abs(total_pnl) > 500:
                issues.append({
                    "type": "weekly_drawdown",
                    "pnl": total_pnl,
                    "severity": "warning",
                    "message": f"Weekly drawdown: ${total_pnl:.0f}",
                })

        return issues

    # ------------------------------------------------------------------
    # Daily summary
    # ------------------------------------------------------------------

    def generate_daily_summary(self) -> dict:
        """Generate today's trading summary."""
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        trades = self.get_trades_for_date(today)

        closed = [t for t in trades if t.get("status") == "closed"]
        total_pnl = sum(t.get("pnl", 0) for t in closed)
        wins = [t for t in closed if t.get("pnl", 0) > 0]
        losses = [t for t in closed if t.get("pnl", 0) <= 0]

        summary = {
            "date": today,
            "total_trades": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(closed), 3) if closed else 0,
            "total_pnl": round(total_pnl, 2),
            "avg_win": round(sum(t["pnl"] for t in wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(sum(t["pnl"] for t in losses) / len(losses), 2) if losses else 0,
            "best_trade": round(max(t["pnl"] for t in closed), 2) if closed else 0,
            "worst_trade": round(min(t["pnl"] for t in closed), 2) if closed else 0,
            "generated_at": datetime.now(UTC).isoformat(),
        }

        # Save daily summary
        daily_path = self.dir / f"daily_{today}.json"
        with open(daily_path, "w") as f:
            json.dump(summary, f, indent=2)

        return summary

    # ------------------------------------------------------------------
    # Query methods for AI operator
    # ------------------------------------------------------------------

    def get_recent_trades(self, n: int = 50) -> list[dict]:
        """Get the N most recent trades."""
        trades = self._read_jsonl("trades")
        return trades[-n:]

    def get_trades_for_date(self, date_str: str) -> list[dict]:
        """Get all trades for a specific date."""
        trades = self._read_jsonl("trades")
        return [t for t in trades if date_str in t.get("timestamp", "")]

    def get_daily_summaries(self, n_days: int = 7) -> list[dict]:
        """Get daily summaries for the last N days."""
        summaries = []
        for f in sorted(self.dir.glob("daily_*.json"), reverse=True)[:n_days]:
            with open(f) as fp:
                summaries.append(json.load(fp))
        return summaries

    def get_stats(self) -> dict:
        """Get overall statistics for AI operator dashboard."""
        trades = self._read_jsonl("trades")
        closed = [t for t in trades if t.get("status") == "closed"]
        decisions = self._read_jsonl("decisions")
        anomalies = self._read_jsonl("anomalies")

        total_pnl = sum(t.get("pnl", 0) for t in closed)
        wins = [t for t in closed if t.get("pnl", 0) > 0]
        losses = [t for t in closed if t.get("pnl", 0) <= 0]

        return {
            "total_trades": len(closed),
            "total_decisions": len(decisions),
            "total_pnl": round(total_pnl, 2),
            "win_rate": round(len(wins) / len(closed), 3) if closed else 0,
            "avg_win": round(sum(t["pnl"] for t in wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(sum(t["pnl"] for t in losses) / len(losses), 2) if losses else 0,
            "consecutive_losses": self._count_consecutive_losses(closed),
            "anomalies_today": len([a for a in anomalies if datetime.now(UTC).strftime("%Y-%m-%d") in a.get("timestamp", "")]),
            "latest_trade_time": closed[-1]["timestamp"] if closed else None,
            "journal_size": len(trades),
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _append_trade(self, trade: dict) -> None:
        self._append_jsonl("trades", trade)

    def _append_jsonl(self, entity: str, data: dict) -> None:
        month = datetime.now(UTC).strftime("%Y-%m")
        path = self.dir / f"{entity}_{month}.jsonl"
        with open(path, "a") as f:
            f.write(json.dumps(data, default=str) + "\n")

    def _read_jsonl(self, entity: str) -> list[dict]:
        month = datetime.now(UTC).strftime("%Y-%m")
        path = self.dir / f"{entity}_{month}.jsonl"
        if not path.exists():
            return []
        records = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return records

    @staticmethod
    def _count_consecutive_losses(trades: list[dict]) -> int:
        count = 0
        for t in reversed(trades):
            if t.get("pnl", 0) <= 0:
                count += 1
            else:
                break
        return count


# Global instance
journal = TradeJournal()
