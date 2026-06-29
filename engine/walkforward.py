"""Walk-forward backtest — Phase 4.

Rolling train/test validation to detect overfitting and measure
out-of-sample performance stability over time.

Architecture:
  Split data into N windows.
  For each window:
    1. Train model on past train_days
    2. Backtest on next test_days (out-of-sample)
    3. Record metrics
  4. Analyze trend: is performance stable or degrading?
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class WalkForwardResult:
    """Aggregate walk-forward validation results."""

    windows: list[dict] = field(default_factory=list)
    avg_sharpe: float = 0.0
    avg_win_rate: float = 0.0
    avg_profit_factor: float = 0.0
    avg_max_dd: float = 0.0
    avg_return: float = 0.0
    sharpe_std: float = 0.0  # Lower = more stable
    sharpe_trend: float = 0.0  # Positive = improving, negative = degrading
    overfitting_risk: str = "unknown"  # low/medium/high
    total_trades: int = 0
    oos_trades: int = 0  # Out-of-sample trades

    def to_dict(self) -> dict:
        return {
            "windows": len(self.windows),
            "avg_sharpe": round(self.avg_sharpe, 3),
            "avg_win_rate_pct": round(self.avg_win_rate * 100, 1),
            "avg_profit_factor": round(self.avg_profit_factor, 2),
            "avg_max_dd_pct": round(self.avg_max_dd * 100, 1),
            "avg_return_pct": round(self.avg_return * 100, 1),
            "sharpe_std": round(self.sharpe_std, 3),
            "sharpe_trend": round(self.sharpe_trend, 4),
            "overfitting_risk": self.overfitting_risk,
            "total_trades": self.total_trades,
            "oos_trades": self.oos_trades,
            "window_details": self.windows,
        }


def run_walkforward(
    ohlcv: pd.DataFrame,
    pair: str = "BTC/USDT:USDT",
    train_days: int = 30,
    test_days: int = 7,
    min_windows: int = 3,
    model_dir: str = "./models",
    initial_equity: float = 5000.0,
) -> WalkForwardResult:
    """Run walk-forward validation.

    Args:
        ohlcv: OHLCV DataFrame with datetime index or 'date' column.
        pair: Trading pair name.
        train_days: Days of data for training each window.
        test_days: Days of data for out-of-sample testing.
        min_windows: Minimum number of windows required.
        model_dir: Model storage directory.
        initial_equity: Starting equity for each backtest window.

    Returns:
        WalkForwardResult with aggregate metrics.
    """
    candles_per_day = 6  # 4h candles
    train_candles = train_days * candles_per_day
    test_candles = test_days * candles_per_day

    if len(ohlcv) < train_candles + test_candles:
        raise ValueError(
            f"Need at least {train_candles + test_candles} candles, got {len(ohlcv)}"
        )

    from engine.backtest_adapter import AIBacktestAdapter
    from engine.direction_predictor import DirectionPredictor
    from engine.features import FeatureEngineer
    from engine.regime_classifier import RegimeClassifier

    fe = FeatureEngineer()
    window_results = []

    # Rolling windows
    step = test_candles
    start = 0

    while start + train_candles + test_candles <= len(ohlcv):
        train_df = ohlcv.iloc[start : start + train_candles]
        test_df = ohlcv.iloc[start + train_candles : start + train_candles + test_candles]

        if len(test_df) < 50:
            break

        # Train on past window
        try:
            train_features = fe.compute_price_features(train_df)

            rc = RegimeClassifier(model_dir=model_dir)
            rc.train(train_features)

            dp = DirectionPredictor(model_dir=model_dir)
            dp.train(train_features)
        except Exception as e:
            logger.warning(f"Window {start}: training failed — {e}")
            start += step
            continue

        # Test on forward window (out-of-sample)
        adapter = AIBacktestAdapter(model_dir, initial_equity=initial_equity)
        result = adapter.run(test_df, pair, warmup=30) if hasattr(test_df, 'columns') else None

        if result is None:
            start += step
            continue

        window_results.append({
            "window": len(window_results) + 1,
            "train_start": str(train_df.index[0] if hasattr(train_df.index, '__getitem__') else train_df.iloc[0].get('date', '?')),
            "test_start": str(test_df.index[0] if hasattr(test_df.index, '__getitem__') else test_df.iloc[0].get('date', '?')),
            "trades": result.total_trades,
            "sharpe": round(result.sharpe_ratio, 3),
            "max_dd_pct": round(result.max_drawdown * 100, 1),
            "win_rate_pct": round(result.win_rate * 100, 1),
            "profit_factor": round(result.profit_factor, 2),
            "return_pct": round(result.total_return * 100, 1),
            "in_sample": False,  # Out-of-sample
        })

        start += step

        if len(window_results) >= 20:  # Safety cap
            break

    if len(window_results) < min_windows:
        raise ValueError(f"Only {len(window_results)} windows, need {min_windows}")

    # Aggregate
    sharpes = [w["sharpe"] for w in window_results]
    win_rates = [w["win_rate_pct"] / 100 for w in window_results]
    pfs = [w["profit_factor"] for w in window_results]
    dds = [w["max_dd_pct"] / 100 for w in window_results]
    returns = [w["return_pct"] / 100 for w in window_results]
    total_oos_trades = sum(w["trades"] for w in window_results)

    result = WalkForwardResult()
    result.windows = window_results
    result.avg_sharpe = float(np.mean(sharpes))
    result.avg_win_rate = float(np.mean(win_rates))
    result.avg_profit_factor = float(np.mean(pfs))
    result.avg_max_dd = float(np.mean(dds))
    result.avg_return = float(np.mean(returns))
    result.sharpe_std = float(np.std(sharpes))
    result.oos_trades = total_oos_trades

    # Sharpe trend (linear regression slope)
    if len(sharpes) >= 3:
        x = np.arange(len(sharpes))
        slope = np.polyfit(x, sharpes, 1)[0]
        result.sharpe_trend = float(slope)
    else:
        result.sharpe_trend = 0.0

    # Overfitting risk assessment
    if result.sharpe_std > result.avg_sharpe * 0.5:
        result.overfitting_risk = "high"  # High variance across windows
    elif result.sharpe_trend < -0.02:
        result.overfitting_risk = "high"  # Degrading performance
    elif result.sharpe_trend < -0.005:
        result.overfitting_risk = "medium"
    else:
        result.overfitting_risk = "low"

    logger.info(
        f"Walk-forward: {len(window_results)} windows, "
        f"avg Sharpe={result.avg_sharpe:.3f}±{result.sharpe_std:.3f}, "
        f"trend={result.sharpe_trend:.3f}, risk={result.overfitting_risk}"
    )

    return result
