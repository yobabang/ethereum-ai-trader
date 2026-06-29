"""Training pipeline — connects data download, feature engineering,
model training, and backtest validation into one automated loop.

Called by the SelfOptimizer's retraining scheduler.
"""

import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class TrainingPipeline:
    """End-to-end retraining pipeline.

    Flow:
      1. Load/reload OHLCV data from freqtrade data directory
      2. Run FeatureEngineer
      3. Train RegimeClassifier + DirectionPredictor
      4. Quick backtest on recent N days
      5. Report metrics to SelfOptimizer for acceptance decision
    """

    def __init__(
        self,
        datadir: str = "./user_data/data",
        model_dir: str = "./models",
        pairs: list[str] | None = None,
        backtest_days: int = 7,
    ):
        self.datadir = Path(datadir)
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.pairs = pairs or ["BTC/USDT:USDT", "ETH/USDT:USDT"]
        self.backtest_days = backtest_days

    def run(self) -> dict:
        """Execute full training pipeline. Returns metrics dict."""
        start_time = time.time()

        # ---- 1. Load data ----
        logger.info("Loading historical data...")
        dfs = []
        for pair in self.pairs:
            try:
                df = self._load_data(pair)
                if df is not None and len(df) > 100:
                    dfs.append((pair, df))
                    logger.info(f"  {pair}: {len(df)} candles loaded")
            except Exception as e:
                logger.warning(f"  {pair}: failed — {e}")

        if not dfs:
            raise RuntimeError("No data available for training")

        # ---- 2. Feature engineering ----
        logger.info("Computing features...")
        from engine.features import FeatureEngineer

        fe = FeatureEngineer()
        feature_dfs = []
        for pair, df in dfs:
            try:
                fdf = fe.compute_price_features(df)
                feature_dfs.append(fdf)
                logger.info(f"  {pair}: {len(fdf.columns)} features, {len(fdf)} rows")
            except Exception as e:
                logger.warning(f"  {pair} features failed: {e}")

        if not feature_dfs:
            raise RuntimeError("Feature computation failed for all pairs")

        # Drop non-numeric columns (date, index) before ML training
        for i, fdf in enumerate(feature_dfs):
            feature_dfs[i] = fdf.select_dtypes(include=["number"])

        combined = pd.concat(feature_dfs)
        logger.info(f"Combined training data: {len(combined)} rows x {len(combined.columns)} numeric cols")

        # ---- 3. Train models ----
        metrics = {"pairs": len(dfs), "samples": len(combined)}

        # Regime classifier
        from engine.regime_classifier import RegimeClassifier

        logger.info("Training regime classifier...")
        rc = RegimeClassifier(model_dir=str(self.model_dir))
        regime_metrics = rc.train(combined)
        metrics["regime_accuracy"] = regime_metrics["accuracy"]
        logger.info(f"  Regime accuracy: {regime_metrics['accuracy']:.3f}")

        # Direction predictor
        from engine.direction_predictor import DirectionPredictor

        logger.info("Training direction predictor...")
        dp = DirectionPredictor(model_dir=str(self.model_dir))
        dp_metrics = dp.train(combined)
        metrics["rmse"] = dp_metrics["rmse"]
        metrics["direction_accuracy"] = dp_metrics["direction_accuracy"]
        logger.info(f"  Direction RMSE: {dp_metrics['rmse']:.5f}, dir_acc: {dp_metrics['direction_accuracy']:.3f}")

        # ---- 4. Quick backtest on recent data ----
        logger.info(f"Backtesting on last {self.backtest_days} days...")
        bt_metrics = self._quick_backtest(combined)
        metrics.update(bt_metrics)
        logger.info(
            f"  Backtest: Sharpe={bt_metrics.get('sharpe', 0):.3f}, "
            f"MaxDD={bt_metrics.get('max_drawdown', 0):.3f}"
        )

        elapsed = time.time() - start_time
        metrics["train_time_seconds"] = round(elapsed, 1)
        logger.info(f"Training pipeline complete in {elapsed:.1f}s")

        return metrics

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_data(self, pair: str, timeframe: str = "4h") -> Optional[pd.DataFrame]:
        """Load OHLCV data for a pair.

        Uses the same fallback logic as trainer.py — tries freqtrade's
        native loader first, then direct feather file read.
        """
        from pathlib import Path
        from freqtrade.data.history import load_pair_history

        data_path = Path(self.datadir)
        if not data_path.is_absolute():
            data_path = Path.cwd() / data_path

        # Try freqtrade's native loader
        for fmt in ["feather", "json"]:
            try:
                df = load_pair_history(
                    pair=pair,
                    timeframe=timeframe,
                    datadir=data_path,
                    data_format=fmt,
                    timerange=None,
                    candle_type="futures",
                )
                if df is not None and len(df) > 100:
                    return df
            except Exception:
                continue

        # Fallback: direct feather file read
        safe_pair = pair.replace("/", "_").replace(":", "_")
        for data_dir in [data_path, data_path / "okx", data_path / "binance"]:
            feather_path = data_dir / f"{safe_pair}-{timeframe}-futures.feather"
            if feather_path.exists():
                df = pd.read_feather(feather_path)
                if len(df) > 100:
                    return df

        return None

    # ------------------------------------------------------------------
    # Quick backtest (simulated)
    # ------------------------------------------------------------------

    def _quick_backtest(self, features: pd.DataFrame) -> dict:
        """Run a quick forward-walk validation on recent data.

        This is a simplified backtest — it uses the trained model
        on the most recent data without full exchange simulation.
        The full backtest runs in Phase 4.
        """
        from engine.direction_predictor import DirectionPredictor

        # Use last N days for validation
        n_recent = min(len(features), self.backtest_days * 6)  # 6 candles/day at 4h
        if n_recent < 20:
            return {"sharpe": 0.0, "max_drawdown": 0.0, "win_rate": 0.0, "profit_factor": 0.0}

        recent = features.iloc[-n_recent:]

        # Load the freshly trained predictor
        dp = DirectionPredictor(model_dir=str(self.model_dir))
        try:
            dp.load()
        except FileNotFoundError:
            return {"sharpe": 0.0, "max_drawdown": 0.0, "win_rate": 0.0, "profit_factor": 0.0}

        predictions = dp.predict(recent)

        # Simulate: if prediction direction matches actual next-period return, it's a "win"
        closes = recent["close"].values
        actual_returns = np.diff(closes) / closes[:-1]
        pred_returns = [p["expected_return"] if p else 0.0 for p in predictions[:-1]]

        # Align lengths
        n = min(len(actual_returns), len(pred_returns))
        actual_returns = actual_returns[:n]
        pred_returns = pred_returns[:n]

        if n < 5:
            return {"sharpe": 0.0, "max_drawdown": 0.0, "win_rate": 0.0, "profit_factor": 0.0}

        # Direction matches
        correct = sum(1 for a, p in zip(actual_returns, pred_returns) if np.sign(a) == np.sign(p) and p != 0)
        win_rate = correct / n

        # Sharpe from simulated returns
        returns = np.array(pred_returns) * np.sign(actual_returns)  # Return if we followed the prediction
        ret_mean = np.mean(returns)
        ret_std = np.std(returns) or 1e-10
        sharpe = float(ret_mean / ret_std * np.sqrt(365 * 6))  # Annualized

        # Max drawdown
        cumulative = np.cumprod(1 + np.array(returns))
        running_max = np.maximum.accumulate(cumulative)
        drawdowns = (cumulative - running_max) / running_max
        max_dd = float(abs(min(drawdowns)))

        # Profit factor
        wins_sum = sum(r for r in returns if r > 0)
        losses_sum = abs(sum(r for r in returns if r < 0)) or 1e-10
        profit_factor = float(wins_sum / losses_sum)

        return {
            "sharpe": round(sharpe, 4),
            "max_drawdown": round(max_dd, 4),
            "win_rate": round(win_rate, 4),
            "profit_factor": round(profit_factor, 4),
        }
