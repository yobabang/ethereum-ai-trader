"""Self-optimizing training loop — AI decision Layer 5.

Handles periodic model retraining, version management, backtest
validation, and adaptive parameter adjustment based on live trading
feedback (the "skin in the game" loop).

Architecture:
  Every 4 hours:
    1. Download latest data
    2. Retrain RegimeClassifier + DirectionPredictor
    3. Backtest new models on recent 7 days
    4. If new > old (higher Sharpe): swap. Else: keep old, log reason.
  After every closed trade:
    1. Record trade metrics
    2. Update adaptive thresholds
    3. If consecutive losses: tighten confidence, reduce size
    4. If winning streak: gradually restore defaults
"""

import json
import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class ModelVersion:
    """A versioned model checkpoint."""

    def __init__(self, model_dir: Path, version: str):
        self.dir = model_dir
        self.version = version
        self.sharpe: float = 0.0
        self.max_drawdown: float = 1.0
        self.win_rate: float = 0.0
        self.profit_factor: float = 0.0
        self.trained_at: str = ""
        self.replaced_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "sharpe": self.sharpe,
            "max_drawdown": self.max_drawdown,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "trained_at": self.trained_at,
            "replaced_reason": self.replaced_reason,
        }


class TradeFeedback:
    """Per-trade record for the feedback loop."""

    def __init__(
        self,
        pair: str,
        side: str,
        entry_price: float,
        exit_price: float,
        pnl: float,
        pnl_pct: float,
        entry_reason: str,
        exit_reason: str,
        duration_hours: float,
    ):
        self.pair = pair
        self.side = side
        self.entry_price = entry_price
        self.exit_price = exit_price
        self.pnl = pnl
        self.pnl_pct = pnl_pct
        self.entry_reason = entry_reason
        self.exit_reason = exit_reason
        self.duration_hours = duration_hours
        self.closed_at = datetime.now(UTC).isoformat()

    def to_dict(self) -> dict:
        return self.__dict__


class SelfOptimizer:
    """Orchestrates periodic retraining and adaptive parameter adjustment.

    Not an AI model itself — it's the meta-controller that decides when
    to retrain, whether to swap models, and how to adapt parameters.
    """

    def __init__(
        self,
        model_dir: str = "./models",
        train_interval_hours: int = 4,
        backtest_days: int = 7,
        min_sharpe: float = 0.5,
        max_drawdown: float = 0.15,
        min_win_rate: float = 0.40,
    ):
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.train_interval_hours = train_interval_hours
        self.backtest_days = backtest_days
        self.min_sharpe = min_sharpe
        self.max_drawdown = max_drawdown
        self.min_win_rate = min_win_rate

        # State
        self._last_train_time: float = 0.0
        self._current_version: str = ""
        self._version_history: list[ModelVersion] = []
        self._trade_history: list[TradeFeedback] = []
        self._consecutive_losses: int = 0
        self._consecutive_wins: int = 0
        self._adaptive_confidence_threshold: float = 0.55
        self._adaptive_position_scalar: float = 1.0

        self._load_state()

    # ------------------------------------------------------------------
    # Training scheduling
    # ------------------------------------------------------------------

    def should_retrain(self) -> bool:
        """Check if enough time has passed since last training."""
        elapsed = time.time() - self._last_train_time
        return elapsed >= self.train_interval_hours * 3600

    def hours_until_next_train(self) -> float:
        """Hours remaining until next scheduled training."""
        elapsed = time.time() - self._last_train_time
        remaining = self.train_interval_hours * 3600 - elapsed
        return max(0, remaining / 3600)

    # ------------------------------------------------------------------
    # Model versioning
    # ------------------------------------------------------------------

    def record_training(
        self,
        sharpe: float,
        max_dd: float,
        win_rate: float,
        profit_factor: float,
        reason: str = "",
    ) -> str:
        """Record a new model version after training. Returns version ID."""
        self._last_train_time = time.time()
        version = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")

        mv = ModelVersion(self.model_dir, version)
        mv.sharpe = sharpe
        mv.max_drawdown = max_dd
        mv.win_rate = win_rate
        mv.profit_factor = profit_factor
        mv.trained_at = datetime.now(UTC).isoformat()
        mv.replaced_reason = reason

        self._version_history.append(mv)
        self._current_version = version
        self._save_versions()

        # Keep only last 30 versions
        if len(self._version_history) > 30:
            self._version_history = self._version_history[-30:]

        logger.info(
            f"Model version {version}: Sharpe={sharpe:.3f}, "
            f"MaxDD={max_dd:.2%}, WinRate={win_rate:.1%}, PF={profit_factor:.2f}"
        )
        return version

    def should_replace_model(self, new_sharpe: float, new_max_dd: float) -> tuple[bool, str]:
        """Decide whether new model should replace the current one.

        Returns (replace: bool, reason: str).
        """
        # First model: always accept if it meets minimums
        if not self._version_history:
            if new_sharpe >= self.min_sharpe and new_max_dd <= self.max_drawdown:
                return True, "Initial model meets minimum thresholds"
            return False, f"Initial model fails: Sharpe={new_sharpe:.3f} < {self.min_sharpe}"

        # Compare with best recent version
        best = max(self._version_history, key=lambda v: v.sharpe)

        if new_sharpe < self.min_sharpe:
            return False, f"Sharpe {new_sharpe:.3f} below minimum {self.min_sharpe}"

        if new_max_dd > self.max_drawdown:
            return False, f"MaxDD {new_max_dd:.2%} above limit {self.max_drawdown:.2%}"

        if new_sharpe > best.sharpe * 1.05:  # 5% improvement required
            return True, f"Sharpe improved: {best.sharpe:.3f} → {new_sharpe:.3f}"

        return False, f"Sharpe {new_sharpe:.3f} not better than best {best.sharpe:.3f}"

    # ------------------------------------------------------------------
    # Trade feedback loop
    # ------------------------------------------------------------------

    def record_trade(
        self,
        pair: str,
        side: str,
        entry_price: float,
        exit_price: float,
        pnl: float,
        pnl_pct: float,
        entry_reason: str,
        exit_reason: str,
        duration_hours: float,
    ) -> None:
        """Record a closed trade and update adaptive parameters."""
        fb = TradeFeedback(
            pair=pair,
            side=side,
            entry_price=entry_price,
            exit_price=exit_price,
            pnl=pnl,
            pnl_pct=pnl_pct,
            entry_reason=entry_reason,
            exit_reason=exit_reason,
            duration_hours=duration_hours,
        )
        self._trade_history.append(fb)

        # Keep last 1000 trades
        if len(self._trade_history) > 1000:
            self._trade_history = self._trade_history[-1000:]

        # Adaptive parameter adjustment
        if pnl < 0:
            self._consecutive_losses += 1
            self._consecutive_wins = 0
        else:
            self._consecutive_wins += 1
            self._consecutive_losses = 0

        self._adapt_parameters()
        self._save_state()

    def _adapt_parameters(self) -> None:
        """Adjust trading parameters based on recent performance."""
        # After 2 consecutive losses: tighten up
        if self._consecutive_losses >= 2:
            self._adaptive_confidence_threshold = min(0.75, 0.55 + self._consecutive_losses * 0.05)
            self._adaptive_position_scalar = max(0.3, 1.0 - self._consecutive_losses * 0.15)
            logger.warning(
                f"Adaptive: {self._consecutive_losses} consecutive losses. "
                f"Confidence threshold → {self._adaptive_confidence_threshold:.2f}, "
                f"Position scalar → {self._adaptive_position_scalar:.2f}"
            )
        # After 3 consecutive wins: gradually restore
        elif self._consecutive_wins >= 3:
            self._adaptive_confidence_threshold = max(0.55, self._adaptive_confidence_threshold - 0.05)
            self._adaptive_position_scalar = min(1.0, self._adaptive_position_scalar + 0.1)
            logger.info(
                f"Adaptive: {self._consecutive_wins} consecutive wins. Restoring parameters."
            )

    # ------------------------------------------------------------------
    # Public adaptive getters (called by DecisionArbitrator)
    # ------------------------------------------------------------------

    @property
    def confidence_threshold(self) -> float:
        return self._adaptive_confidence_threshold

    @property
    def position_scalar(self) -> float:
        return self._adaptive_position_scalar

    @property
    def consecutive_losses(self) -> int:
        return self._consecutive_losses

    @property
    def consecutive_wins(self) -> int:
        return self._consecutive_wins

    def recent_sharpe(self, n_trades: int = 20) -> float:
        """Sharpe ratio of last N trades."""
        if len(self._trade_history) < n_trades:
            n_trades = len(self._trade_history)
        if n_trades < 2:
            return 0.0

        returns = [t.pnl_pct / 100 for t in self._trade_history[-n_trades:]]
        if np.std(returns) == 0:
            return 0.0
        return float(np.mean(returns) / np.std(returns) * np.sqrt(365 * 6))  # Annualized (6 trades/day)

    def recent_win_rate(self, n_trades: int = 20) -> float:
        """Win rate of last N trades."""
        if not self._trade_history:
            return 0.0
        recent = self._trade_history[-n_trades:]
        wins = sum(1 for t in recent if t.pnl > 0)
        return wins / len(recent)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _state_path(self) -> Path:
        return self.model_dir / "trade_feedback.json"  # Trade feedback state (strategy-owned)

    def _version_path(self) -> Path:
        return self.model_dir / "model_versions.json"  # Model version state (scheduler-owned)

    def _save_state(self) -> None:
        """Save trade feedback state (consecutive losses, adaptive params)."""
        data = {
            "consecutive_losses": self._consecutive_losses,
            "consecutive_wins": self._consecutive_wins,
            "adaptive_confidence_threshold": self._adaptive_confidence_threshold,
            "adaptive_position_scalar": self._adaptive_position_scalar,
            "trade_count": len(self._trade_history),
        }
        with open(self._state_path(), "w") as f:
            json.dump(data, f, indent=2)

    def _save_versions(self) -> None:
        """Save model version state (training history, version tracking)."""
        data = {
            "last_train_time": self._last_train_time,
            "current_version": self._current_version,
            "version_history": [v.to_dict() for v in self._version_history],
            "consecutive_losses": self._consecutive_losses,
            "consecutive_wins": self._consecutive_wins,
            "adaptive_confidence_threshold": self._adaptive_confidence_threshold,
            "adaptive_position_scalar": self._adaptive_position_scalar,
            "trade_count": len(self._trade_history),
        }
        with open(self._version_path(), "w") as f:
            json.dump(data, f, indent=2)

    def _load_state(self) -> None:
        """Load trade feedback state. Also try loading version state if available."""
        # Load trade feedback (separate file, strategy-owned)
        path = self._state_path()
        if path.exists():
            try:
                with open(path) as f:
                    data = json.load(f)
                self._consecutive_losses = data.get("consecutive_losses", 0)
                self._consecutive_wins = data.get("consecutive_wins", 0)
                self._adaptive_confidence_threshold = data.get("adaptive_confidence_threshold", 0.55)
                self._adaptive_position_scalar = data.get("adaptive_position_scalar", 1.0)
                logger.info(f"Trade feedback loaded: {data.get('trade_count', 0)} trades")
            except Exception as e:
                logger.warning(f"Could not load trade feedback: {e}")

        # Load model versions (separate file, scheduler-owned)
        vpath = self._version_path()
        if vpath.exists():
            try:
                with open(vpath) as f:
                    data = json.load(f)
                self._last_train_time = data.get("last_train_time", 0.0)
                self._current_version = data.get("current_version", "")
                self._version_history = [
                    ModelVersion(self.model_dir, v["version"]) for v in data.get("version_history", [])
                ]
                for mv, vd in zip(self._version_history, data.get("version_history", [])):
                    mv.sharpe = vd.get("sharpe", 0)
                    mv.max_drawdown = vd.get("max_drawdown", 1)
                    mv.win_rate = vd.get("win_rate", 0)
                    mv.trained_at = vd.get("trained_at", "")
                logger.info(f"Model versions loaded: version={self._current_version}")
            except Exception as e:
                logger.warning(f"Could not load model versions: {e}")

    def get_trade_stats(self) -> dict:
        """Summary statistics for the web dashboard."""
        if not self._trade_history:
            return {"total_trades": 0, "win_rate": 0, "sharpe": 0, "total_pnl": 0}
        trades = self._trade_history
        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl <= 0]
        return {
            "total_trades": len(trades),
            "win_rate": round(len(wins) / len(trades), 3) if trades else 0,
            "sharpe": round(self.recent_sharpe(100), 3),
            "total_pnl": round(sum(t.pnl for t in trades), 2),
            "avg_win": round(np.mean([t.pnl for t in wins]), 2) if wins else 0,
            "avg_loss": round(np.mean([t.pnl for t in losses]), 2) if losses else 0,
            "consecutive_losses": self._consecutive_losses,
            "consecutive_wins": self._consecutive_wins,
            "current_confidence_threshold": self._adaptive_confidence_threshold,
            "current_position_scalar": self._adaptive_position_scalar,
        }
