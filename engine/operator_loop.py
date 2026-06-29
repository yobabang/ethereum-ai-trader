"""AI Operator Loop — runs every 5 minutes via cron.

The AI operator (Claude) checks the trading system, detects anomalies,
and takes corrective action. This is the bridge between the loop task
and the AI's ability to fix issues.

Actions performed:
  1. Check system status (trades, PnL, drawdown)
  2. Run anomaly detection
  3. If critical: stop trading, log issue, notify
  4. If warning: adjust parameters, log
  5. Generate daily summary at midnight
  6. Save checkpoint to reports/operator_log.json

Usage:
  python -m engine.operator_loop
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from engine.trade_journal import TradeJournal, JOURNAL_DIR

REPORTS_DIR = Path("../ethereum-ai-trader/reports")
OPERATOR_LOG = REPORTS_DIR / "operator_log.jsonl"


def run_operator_check() -> dict:
    """Run one cycle of the AI operator check. Returns action log."""
    j = TradeJournal()
    actions = []
    timestamp = datetime.now(UTC).isoformat()

    # 1. Get stats
    stats = j.get_stats()
    status = "normal"

    # 2. Check anomalies
    issues = j.check_anomalies(max_drawdown_pct=0.15, max_consecutive_losses=5)

    for issue in issues:
        if issue["severity"] == "critical":
            status = "critical"
            actions.append({
                "action": "STOP_TRADING",
                "reason": issue["message"],
                "severity": "critical",
            })
            j.record_anomaly(issue["type"], issue["message"], "critical")
        else:
            if status == "normal":
                status = "warning"
            actions.append({
                "action": "ADJUST_PARAMETERS",
                "reason": issue["message"],
                "severity": "warning",
            })
            j.record_anomaly(issue["type"], issue["message"], "warning")

    # 3. Check if midnight — generate daily summary
    now = datetime.now(UTC)
    if now.hour == 0 and now.minute < 10:
        summary = j.generate_daily_summary()
        actions.append({
            "action": "DAILY_SUMMARY",
            "summary": summary,
        })

    # 4. Check if no recent trades (system might be stuck)
    if stats["total_trades"] > 0 and stats.get("latest_trade_time"):
        last_trade = datetime.fromisoformat(stats["latest_trade_time"])
        hours_since = (now - last_trade).total_seconds() / 3600
        if hours_since > 24 and stats["total_trades"] > 10:
            actions.append({
                "action": "CHECK_SYSTEM",
                "reason": f"No trades in {hours_since:.0f}h — system may be stuck",
                "severity": "warning",
            })

    # 5. Build operator report
    report = {
        "timestamp": timestamp,
        "status": status,
        "stats": stats,
        "issues_found": len(issues),
        "actions_taken": len(actions),
        "actions": actions,
    }

    # 6. Save to operator log
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(OPERATOR_LOG, "a") as f:
        f.write(json.dumps(report, default=str) + "\n")

    return report


def main():
    report = run_operator_check()
    print(json.dumps(report, indent=2, default=str))

    if report["status"] == "critical":
        print("\n*** CRITICAL: AI OPERATOR MUST INTERVENE ***")
        print("Actions required:", json.dumps(report["actions"], indent=2))
        sys.exit(1)
    elif report["status"] == "warning":
        print("\nWarnings detected — AI operator should review")
        sys.exit(0)
    else:
        print("\nSystem normal")


if __name__ == "__main__":
    main()
