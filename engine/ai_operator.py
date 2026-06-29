"""AI Operator — control panel for Claude to run the trading system.

The AI operator can:
  1. Check system status
  2. Review recent trades
  3. Detect anomalies
  4. Adjust risk parameters
  5. Fix bugs and redeploy

Usage by AI (Claude):
  python -m engine.ai_operator status
  python -m engine.ai_operator trades --last 10
  python -m engine.ai_operator daily
  python -m engine.ai_operator check
  python -m engine.ai_operator adjust --confidence 0.60 --position 0.15
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from engine.trade_journal import TradeJournal, JOURNAL_DIR


def cmd_status():
    """Show system status for AI operator."""
    j = TradeJournal()
    stats = j.get_stats()

    print(json.dumps({
        "system": "ethereum-ai-trader",
        "status": "operational",
        "timestamp": datetime.now(UTC).isoformat(),
        "trading_stats": stats,
        "journal_dir": str(JOURNAL_DIR),
        "anomalies_pending": stats["anomalies_today"],
    }, indent=2, default=str))


def cmd_trades(last: int = 10):
    """Show recent trades."""
    j = TradeJournal()
    trades = j.get_recent_trades(last)
    for t in trades:
        print(json.dumps(t, default=str))


def cmd_daily():
    """Generate and show daily summary."""
    j = TradeJournal()
    summary = j.generate_daily_summary()
    print(json.dumps(summary, indent=2))


def cmd_check():
    """Run anomaly detection."""
    j = TradeJournal()
    issues = j.check_anomalies(max_drawdown_pct=0.15, max_consecutive_losses=5)

    if issues:
        print(json.dumps({
            "status": "issues_found",
            "count": len(issues),
            "issues": issues,
        }, indent=2))
        # Record anomalies
        for issue in issues:
            j.record_anomaly(issue["type"], issue["message"], issue["severity"])

        # Critical: suggest actions
        critical = [i for i in issues if i["severity"] == "critical"]
        if critical:
            print("\nRECOMMENDED ACTIONS:")
            print("  1. Stop trading immediately: freqtrade stop")
            print("  2. Review anomaly details above")
            print("  3. Fix root cause")
            print("  4. Resume with adjusted parameters")
    else:
        print(json.dumps({"status": "clean", "issues": []}, indent=2))


def cmd_adjust(confidence: float | None = None, position: float | None = None, leverage: int | None = None):
    """Adjust risk parameters live."""
    config_path = Path("../ethereum-ai-trader/config.json")
    if not config_path.exists():
        print("ERROR: config.json not found")
        sys.exit(1)

    with open(config_path) as f:
        config = json.load(f)

    changes = []
    ai = config.get("ai", {})
    if confidence is not None:
        old = ai.get("min_confidence", 0.55)
        ai["min_confidence"] = confidence
        changes.append(f"confidence: {old} -> {confidence}")
    if position is not None:
        old = ai.get("max_position_pct", 0.20)
        ai["max_position_pct"] = position
        changes.append(f"position: {old} -> {position}")
    if leverage is not None:
        old = ai.get("max_leverage", 5)
        ai["max_leverage"] = leverage
        changes.append(f"leverage: {old} -> {leverage}")

    config["ai"] = ai
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

    print(json.dumps({
        "action": "parameters_adjusted",
        "changes": changes,
        "timestamp": datetime.now(UTC).isoformat(),
    }, indent=2))


def cmd_decision(action: str, reason: str = ""):
    """Record an AI operator override decision."""
    j = TradeJournal()
    j.record_decision(
        action=action,
        reason=f"AI_OPERATOR: {reason}",
        confidence=1.0,
        expected_return=0.0,
        position_size_pct=0.0,
        stop_loss_pct=0.0,
        take_profit_pct=0.0,
        leverage=0,
    )
    print(json.dumps({"recorded": True, "action": action}, indent=2))


def main():
    parser = argparse.ArgumentParser(description="AI Operator Control Panel")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("status", help="Show system status")
    p = sub.add_parser("trades", help="Show recent trades")
    p.add_argument("--last", type=int, default=10)
    sub.add_parser("daily", help="Generate daily summary")
    sub.add_parser("check", help="Run anomaly detection")
    p = sub.add_parser("adjust", help="Adjust risk parameters")
    p.add_argument("--confidence", type=float)
    p.add_argument("--position", type=float)
    p.add_argument("--leverage", type=int)
    p = sub.add_parser("override", help="AI operator override decision")
    p.add_argument("--action", choices=["stop", "resume", "emergency_close"], required=True)
    p.add_argument("--reason", default="")

    args = parser.parse_args()

    if args.cmd == "status":
        cmd_status()
    elif args.cmd == "trades":
        cmd_trades(args.last)
    elif args.cmd == "daily":
        cmd_daily()
    elif args.cmd == "check":
        cmd_check()
    elif args.cmd == "adjust":
        cmd_adjust(args.confidence, args.position, args.leverage)
    elif args.cmd == "override":
        cmd_decision(args.action, args.reason)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
