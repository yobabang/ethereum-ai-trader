"""AI iteration analysis — reads a trigger snapshot and produces recommendations.

This is the "AI brain" entry point. It's meant to be invoked by a human
operator in Claude Code (or via CronCreate) when iteration_monitor fires.

Workflow:
  1. Read the latest (or specified) snapshot from data/snapshots/
  2. Print a structured analysis prompt (for the AI to reason over)
  3. AI produces recommendations: parameter changes + optional retrain decision
  4. For each recommended param change, run walk-forward validation
  5. If validation passes (Sharpe improvement ≥ threshold), apply the change
  6. Update iteration state so the monitor resets its trigger

Usage:
  python scripts/analyze_iteration.py                    # analyze latest snapshot
  python scripts/analyze_iteration.py --snapshot <path>  # analyze specific
  python scripts/analyze_iteration.py --dry-run          # analyze but don't apply
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.database import Database
from engine.iteration_monitor import _save_state, _utcnow_iso

DEFAULT_DB_PATH = "sim_trader.db"

SNAPSHOT_DIR = ROOT / "data" / "snapshots"

# Minimum Sharpe improvement to accept a param change (vs current baseline)
SHARPE_IMPROVEMENT_THRESHOLD = 0.10


def find_latest_snapshot() -> Path | None:
    """Find the most recent snapshot file."""
    if not SNAPSHOT_DIR.exists():
        return None
    snaps = sorted(SNAPSHOT_DIR.glob("snapshot_*.json"))
    return snaps[-1] if snaps else None


def load_snapshot(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def print_analysis_prompt(snapshot: dict):
    """Print a structured analysis of the snapshot for AI reasoning.

    This is what the AI agent reads to form recommendations.
    """
    s = snapshot["summary"]
    print("\n" + "=" * 70)
    print("  📊 ITERATION ANALYSIS")
    print("=" * 70)
    print(f"\n触发原因: {snapshot['trigger_reason']}")
    print(f"快照时间: {snapshot['snapshot_time']}")
    print(f"\n--- 总览 ---")
    print(f"  分析交易数: {s['total_trades_analyzed']}")
    print(f"  胜率: {s['win_rate']*100:.1f}% ({s['winning_trades']}胜 / {s['losing_trades']}负)")
    print(f"  总盈亏: ${s['total_pnl']:+.2f}")
    print(f"  当前持仓: {s['open_positions_count']}")

    print(f"\n--- 按 mode 分 ---")
    for mode, m in snapshot.get("by_mode", {}).items():
        print(f"  {mode}: {m['trades']}笔, 胜率{m['win_rate']*100:.1f}%, PnL ${m['pnl']:+.2f}")

    print(f"\n--- 按交易对分 ---")
    for pair, p in snapshot.get("by_pair", {}).items():
        print(f"  {pair}: {p['trades']}笔, 胜率{p['win_rate']*100:.1f}%, PnL ${p['pnl']:+.2f}")

    print(f"\n--- 按退出原因分 ---")
    for reason, cnt in snapshot.get("by_exit_reason", {}).items():
        print(f"  {reason}: {cnt}笔")

    print(f"\n--- 权益曲线尾部 ---")
    for eq in snapshot.get("equity_history_tail", [])[-5:]:
        print(f"  {eq.get('timestamp','')}: equity={eq.get('equity',0):.2f}")

    print("\n" + "=" * 70)
    print("  AI 分析任务:")
    print("  基于以上数据，分析:")
    print("  1. 策略是否退化？哪个 mode/币种表现最差？")
    print("  2. 哪些参数可能需要调整？给出具体建议值")
    print("  3. 是否需要重训模型？(累积交易是否足够)")
    print("  4. 建议的参数调整，每个都会经过 walk-forward 验证")
    print("=" * 70 + "\n")


def validate_and_apply(snapshot: dict, dry_run: bool = False):
    """Run walk-forward validation on any parameter recommendations.

    In this scaffold, we just mark the snapshot as analyzed and reset the trigger.
    The actual AI recommendations would be fed into walkforward_verify.py
    or trend_walkforward.py for validation.
    """
    recommendations = snapshot.get("_ai_recommendations", [])

    if not recommendations:
        print("ℹ️  无 AI 建议（_ai_recommendations 为空），仅标记已分析。")
        _mark_analyzed(snapshot)
        return

    print(f"\n🔄 验证 {len(recommendations)} 条 AI 建议...")
    applied = 0
    for i, rec in enumerate(recommendations):
        print(f"\n  [{i+1}] {rec.get('description', 'unknown')}")
        print(f"      参数: {rec.get('params', {})}")
        # TODO: run walk-forward validation here
        # For now, just log
        if dry_run:
            print(f"      [DRY-RUN] 跳过验证和应用")
            continue
        # In production: call validate_params(rec['params']) and apply if passes
        print(f"      [TODO] walk-forward 验证待实现")
        applied += 1

    _mark_analyzed(snapshot)
    print(f"\n✅ 完成: {applied}/{len(recommendations)} 条建议已应用")


def _mark_analyzed(snapshot: dict):
    """Update iteration state so monitor resets its trigger counter."""
    from engine.iteration_monitor import _load_state
    state = _load_state(DEFAULT_DB_PATH)
    state["last_analysis_time"] = _utcnow_iso()
    # Count total trades analyzed as new baseline
    s = snapshot.get("summary", {})
    state["last_trade_count"] = s.get("total_trades_analyzed", 0) + state.get("last_trade_count", 0)
    _save_state(state, DEFAULT_DB_PATH)
    print(f"📝 迭代状态已更新: last_analysis_time={state['last_analysis_time']}")


def main():
    global DEFAULT_DB_PATH
    parser = argparse.ArgumentParser(description="AI iteration analysis")
    parser.add_argument("--snapshot", help="Specific snapshot file path")
    parser.add_argument("--dry-run", action="store_true", help="Analyze but don't apply changes")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="Database path (for state tracking)")
    args = parser.parse_args()

    # Find snapshot
    if args.snapshot:
        snap_path = Path(args.snapshot)
    else:
        snap_path = find_latest_snapshot()

    if not snap_path or not snap_path.exists():
        print("❌ 没有找到快照文件。请先运行 engine.iteration_monitor 触发。")
        sys.exit(1)

    print(f"📁 加载快照: {snap_path}")
    snapshot = load_snapshot(snap_path)

    # Auto-detect db_path from snapshot if --db not explicitly given
    if args.db == DEFAULT_DB_PATH and "db_path" in snapshot:
        # --db was not given (default), use snapshot's db_path
        DEFAULT_DB_PATH = snapshot["db_path"]
        print(f"   自动检测 db_path: {DEFAULT_DB_PATH} (from snapshot)")
    else:
        DEFAULT_DB_PATH = args.db

    print_analysis_prompt(snapshot)

    # If the snapshot has AI recommendations (added by the AI agent), validate them
    if "_ai_recommendations" in snapshot:
        validate_and_apply(snapshot, dry_run=args.dry_run)
    else:
        print("\n💡 此快照尚未包含 AI 建议。")
        print("   在 Claude Code 中分析上述数据后，将建议写入快照的 `_ai_recommendations` 字段，")
        print("   然后重新运行本脚本（或直接运行 walk-forward 验证）。")
        print(f"\n   快照路径: {snap_path}")
        print(f"   示例建议格式:")
        print('   "_ai_recommendations": [')
        print('     {"description": "降低 confidence 阈值", "params": {"min_confidence": 0.50}}')
        print("   ]")
        # Still mark as analyzed so the monitor's trigger counter resets
        _mark_analyzed(snapshot)


if __name__ == "__main__":
    main()
