"""Auto-training scheduler — Phase 3.

Runs the training pipeline on a timer (default every 4 hours)
within the AI strategy's event loop. Uses a non-blocking check
on each strategy iteration to decide whether to retrain.

Architecture:
  AIStrategy.bot_loop_start() → Scheduler.check() → TrainingPipeline.run()
  → SelfOptimizer.record_training() → model swap decision
"""

import logging
import threading
import time
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


class TrainingScheduler:
    """Background training scheduler integrated with strategy lifecycle.

    Does NOT block the main trading loop. Training runs in a
    separate thread. The strategy calls check() on each iteration
    to see if training should be triggered.
    """

    def __init__(
        self,
        model_dir: str = "./models",
        datadir: str = "./user_data/data",
        pairs: list[str] | None = None,
        interval_hours: int = 4,
        backtest_days: int = 7,
    ):
        self.model_dir = model_dir
        self.datadir = datadir
        self.pairs = pairs or ["BTC/USDT:USDT", "ETH/USDT:USDT"]
        self.interval_hours = interval_hours
        self.backtest_days = backtest_days

        # State
        self._last_train_time: float = 0.0
        self._training_in_progress: bool = False
        self._training_thread: threading.Thread | None = None
        self._last_metrics: dict = {}
        self._last_error: str = ""
        self._training_count: int = 0

    # ------------------------------------------------------------------
    # Public API (called by AIStrategy)
    # ------------------------------------------------------------------

    def check(self) -> None:
        """Check if training should be triggered.

        Called on every strategy iteration (bot_loop_start).
        Non-blocking — if enough time has passed, starts training
        in background thread.
        """
        if self._training_in_progress:
            return  # Already training

        elapsed = time.time() - self._last_train_time
        if self._last_train_time > 0 and elapsed < self.interval_hours * 3600:
            return  # Not time yet

        # Start training in background
        logger.info(
            f"Training scheduler: starting training cycle #{self._training_count + 1} "
            f"(last trained {elapsed/3600:.1f}h ago)"
        )
        self._training_in_progress = True
        self._training_thread = threading.Thread(target=self._train, daemon=True)
        self._training_thread.start()

    def should_retrain(self) -> bool:
        """Check if enough time has passed (used when check() pattern doesn't fit)."""
        if self._training_in_progress:
            return False
        elapsed = time.time() - self._last_train_time
        return self._last_train_time == 0 or elapsed >= self.interval_hours * 3600

    @property
    def is_training(self) -> bool:
        return self._training_in_progress

    @property
    def last_metrics(self) -> dict:
        return self._last_metrics

    @property
    def last_error(self) -> str:
        return self._last_error

    @property
    def training_count(self) -> int:
        return self._training_count

    @property
    def hours_until_next(self) -> float:
        if self._last_train_time == 0:
            return 0.0
        elapsed = time.time() - self._last_train_time
        return max(0.0, (self.interval_hours * 3600 - elapsed) / 3600)

    @property
    def last_train_iso(self) -> str:
        if self._last_train_time == 0:
            return "never"
        return datetime.fromtimestamp(self._last_train_time, tz=UTC).isoformat()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _train(self) -> None:
        """Run the full training pipeline (runs in background thread)."""
        try:
            from engine.self_optimizer import SelfOptimizer
            from engine.training_pipeline import TrainingPipeline

            # Run pipeline
            pipeline = TrainingPipeline(
                datadir=self.datadir,
                model_dir=self.model_dir,
                pairs=self.pairs,
                backtest_days=self.backtest_days,
            )
            metrics = pipeline.run()

            # Feed metrics to optimizer for model swap decision
            optimizer = SelfOptimizer(model_dir=self.model_dir)
            sharpe = metrics.get("sharpe", 0)
            max_dd = metrics.get("max_drawdown", 1)
            win_rate = metrics.get("win_rate", 0)
            profit_factor = metrics.get("profit_factor", 0)

            accept, reason = optimizer.should_replace_model(sharpe, max_dd)
            if accept:
                optimizer.record_training(sharpe, max_dd, win_rate, profit_factor, reason)
                logger.info(f"Model SWAPPED: {reason}")
            else:
                logger.info(f"Model KEPT: {reason}")

            self._last_metrics = metrics
            self._last_error = ""
            self._training_count += 1
            self._last_train_time = time.time()

            # Persist state for API bridge
            self._save_state()

            logger.info(
                f"Training cycle #{self._training_count} complete: "
                f"Sharpe={sharpe:.3f}, MaxDD={max_dd:.2%}, "
                f"WinRate={win_rate:.1%}, Decision={'SWAP' if accept else 'KEEP'}"
            )

        except Exception as e:
            self._last_error = str(e)
            logger.error(f"Training failed: {e}", exc_info=True)
        finally:
            self._training_in_progress = False

    def _save_state(self) -> None:
        """Persist scheduler state to disk for API bridge."""
        import json
        from pathlib import Path
        path = Path(self.model_dir) / "scheduler_state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.get_status(), f, indent=2)

    def get_status(self) -> dict:
        """Return status for web dashboard and persistence."""
        return {
            "training_in_progress": self._training_in_progress,
            "training_count": self._training_count,
            "last_train_time": self.last_train_iso,
            "hours_until_next": round(self.hours_until_next, 1),
            "last_metrics": self._last_metrics,
            "last_error": self._last_error,
        }
