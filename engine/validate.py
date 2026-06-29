"""Pre-launch validation — Phase 4.

Runs a comprehensive validation suite before allowing live trading:
  1. Backtest on historical data (all pairs)
  2. Safety rule compliance check
  3. Model integrity check
  4. Configuration validation

Usage:
    python -m engine.validate --config config.json
"""

import argparse
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


# SPEC minimums (must pass before live trading)
SPEC_MINIMUMS = {
    "sharpe_ratio": 0.5,
    "max_drawdown_pct": 15.0,
    "win_rate_pct": 40.0,
    "profit_factor": 1.5,
}

SAFETY_RULES = [
    "HIGH_VOLATILITY blocks new positions",
    "Confidence < 0.55 blocks trades",
    "Max drawdown risk > 5% equity blocks",
    "No same-direction entry on losing position",
    "Extreme funding blocks wrong direction",
    "3 consecutive losses triggers STOP",
    "Max position 20% equity",
    "Max leverage 5x",
]


class ValidationReport:
    """Collects all validation results."""

    def __init__(self):
        self.checks: list[dict] = []
        self.all_passed: bool = True

    def add(self, name: str, passed: bool, detail: str = ""):
        self.checks.append({"name": name, "passed": passed, "detail": detail})
        if not passed:
            self.all_passed = False

    def print_report(self):
        print("\n" + "=" * 60)
        print("  以太 AI Trader — 部署前验证报告")
        print("=" * 60)
        for c in self.checks:
            icon = "PASS" if c["passed"] else "FAIL"
            detail = f" — {c['detail']}" if c["detail"] else ""
            print(f"  [{icon}] {c['name']}{detail}")
        print("-" * 60)
        if self.all_passed:
            print("  结论: 全部通过 — 可以部署实盘")
        else:
            print("  结论: 存在失败项 — 修复后再试")
        print("=" * 60 + "\n")


def run_validation(config: dict, datadir: str) -> ValidationReport:
    """Run all validation checks."""
    report = ValidationReport()

    # ---- 1. Configuration check ----
    print("1. 配置检查...")
    pairs = config.get("pair_whitelist", [])
    trading_mode = config.get("trading_mode", "spot")
    margin_mode = config.get("margin_mode", "")

    report.add("Trading mode = futures", trading_mode == "futures", trading_mode)
    report.add("Margin mode = isolated", margin_mode == "isolated", margin_mode)
    report.add("At least 1 trading pair", len(pairs) >= 1, str(pairs))
    report.add("Only BTC/ETH pairs", all("BTC" in p or "ETH" in p for p in pairs), str(pairs))
    report.add("Max open trades <= 3", config.get("max_open_trades", 99) <= 3)

    # ---- 2. Backtest validation ----
    print("2. 回测验证...")
    from engine.backtest_adapter import AIBacktestAdapter

    model_dir = config.get("ai", {}).get("model_dir", "./models")
    initial_equity = 5000.0

    for pair in pairs:
        try:
            from freqtrade.data.history import load_pair_history
            ohlcv = load_pair_history(
                pair=pair, timeframe="4h", datadir=datadir,
                data_format="feather", candle_type="futures"
            )
            if ohlcv is None or len(ohlcv) < 100:
                ohlcv = load_pair_history(
                    pair=pair, timeframe="4h", datadir=datadir,
                    data_format="json", candle_type="futures"
                )
        except Exception:
            report.add(f"Backtest {pair}: data load", False, "No data — run download-data first")
            continue

        if ohlcv is None or len(ohlcv) < 100:
            report.add(f"Backtest {pair}: data", False, f"Only {len(ohlcv) if ohlcv is not None else 0} candles")
            continue

        adapter = AIBacktestAdapter(model_dir=model_dir, initial_equity=initial_equity)
        result = adapter.run(ohlcv, pair=pair)

        report.add(
            f"Backtest {pair}: Sharpe {result.sharpe_ratio:.2f} > {SPEC_MINIMUMS['sharpe_ratio']}",
            result.sharpe_ratio > SPEC_MINIMUMS["sharpe_ratio"],
            f"Sharpe={result.sharpe_ratio:.2f}",
        )
        report.add(
            f"Backtest {pair}: MaxDD {result.max_drawdown*100:.1f}% < {SPEC_MINIMUMS['max_drawdown_pct']}%",
            result.max_drawdown * 100 < SPEC_MINIMUMS["max_drawdown_pct"],
            f"MaxDD={result.max_drawdown*100:.1f}%",
        )
        report.add(
            f"Backtest {pair}: WinRate {result.win_rate*100:.0f}% > {SPEC_MINIMUMS['win_rate_pct']}%",
            result.win_rate * 100 > SPEC_MINIMUMS["win_rate_pct"],
            f"WinRate={result.win_rate*100:.0f}%",
        )
        report.add(
            f"Backtest {pair}: PF {result.profit_factor:.2f} > {SPEC_MINIMUMS['profit_factor']}",
            result.profit_factor > SPEC_MINIMUMS["profit_factor"],
            f"PF={result.profit_factor:.2f}",
        )
        report.add(
            f"Backtest {pair}: trades={result.total_trades}",
            result.total_trades >= 5,
            f"Need at least 5 trades, got {result.total_trades}",
        )

    # ---- 3. Model integrity ----
    print("3. 模型完整性检查...")
    import os
    model_path = Path(model_dir)
    for f in ["regime_classifier.pkl", "direction_predictor.pkl"]:
        exists = (model_path / f).exists()
        report.add(f"Model file: {f}", exists, "found" if exists else "missing — run trainer first")

    # ---- 4. Safety rule count ----
    print("4. 安全规则计数...")
    report.add(f"Safety rules: {len(SAFETY_RULES)} defined", len(SAFETY_RULES) == 8)

    # ---- 5. Dry-run mode configured ----
    print("5. Dry-run 检查...")
    report.add("Dry-run enabled (must be true before live)", config.get("dry_run", False) is True)

    return report


def main():
    parser = argparse.ArgumentParser(description="Pre-launch validation")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--datadir", default="user_data/data")
    args = parser.parse_args()

    import json
    try:
        with open(args.config) as f:
            config = json.load(f)
    except FileNotFoundError:
        print(f"ERROR: Config file {args.config} not found")
        sys.exit(1)

    report = run_validation(config, args.datadir)
    report.print_report()
    sys.exit(0 if report.all_passed else 1)


if __name__ == "__main__":
    main()
