"""Tests for SelfOptimizer (Phase 3)."""

import shutil
import time
from pathlib import Path

from freqtrade.ai.self_optimizer import SelfOptimizer


class TestSelfOptimizer:
    """Phase 3: Self-optimizing training loop."""

    MODEL_DIR = "./tests/ai/test_opt_models"

    def setup_method(self):
        shutil.rmtree(self.MODEL_DIR, ignore_errors=True)

    def teardown_method(self):
        shutil.rmtree(self.MODEL_DIR, ignore_errors=True)

    def test_initial_state(self):
        """Fresh optimizer starts with defaults."""
        opt = SelfOptimizer(model_dir=self.MODEL_DIR)
        assert opt.confidence_threshold == 0.55
        assert opt.position_scalar == 1.0
        assert opt.consecutive_losses == 0
        assert opt.should_retrain() is True  # Never trained

    def test_should_retrain_after_interval(self):
        """After recording training, should_retrain is False until interval passes."""
        opt = SelfOptimizer(model_dir=self.MODEL_DIR, train_interval_hours=4)
        opt.record_training(1.0, 0.1, 0.5, 2.0)
        assert opt.should_retrain() is False

    def test_hours_until_next_train(self):
        """Returns correct remaining hours."""
        opt = SelfOptimizer(model_dir=self.MODEL_DIR, train_interval_hours=4)
        opt.record_training(1.0, 0.1, 0.5, 2.0)
        remaining = opt.hours_until_next_train()
        assert 0 <= remaining <= 4

    def test_first_model_always_accepted_if_valid(self):
        """First ever trained model is accepted if it meets minimums."""
        opt = SelfOptimizer(model_dir=self.MODEL_DIR, min_sharpe=0.5)
        accept, reason = opt.should_replace_model(0.8, 0.1)
        assert accept is True
        assert "meets minimum" in reason

    def test_first_model_rejected_if_below_minimum(self):
        """Below-minimum Sharpe is rejected."""
        opt = SelfOptimizer(model_dir=self.MODEL_DIR, min_sharpe=0.5)
        accept, reason = opt.should_replace_model(0.3, 0.1)
        assert accept is False

    def test_new_model_rejected_if_worse(self):
        """A worse model is not accepted."""
        opt = SelfOptimizer(model_dir=self.MODEL_DIR)
        opt.record_training(1.0, 0.1, 0.6, 2.0)
        accept, _ = opt.should_replace_model(0.6, 0.2)
        assert accept is False

    def test_new_model_accepted_if_better(self):
        """5%+ improvement triggers replacement."""
        opt = SelfOptimizer(model_dir=self.MODEL_DIR)
        opt.record_training(0.8, 0.1, 0.5, 2.0)
        accept, reason = opt.should_replace_model(0.9, 0.08)
        # 0.9 > 0.8 * 1.05 = 0.84
        assert accept is True

    def test_trade_feedback_adaptive_parameters(self):
        """Consecutive losses tighten parameters, wins restore them."""
        opt = SelfOptimizer(model_dir=self.MODEL_DIR)

        # Two consecutive losses
        opt.record_trade("BTC/USDT:USDT", "long", 60000, 59500, -500, -0.8, "ai", "ai_reversal", 4)
        opt.record_trade("BTC/USDT:USDT", "short", 60000, 60500, -500, -0.8, "ai", "ai_reversal", 4)
        assert opt.consecutive_losses == 2
        assert opt.confidence_threshold > 0.55
        assert opt.position_scalar < 1.0

        # Three wins should restore
        opt.record_trade("ETH/USDT:USDT", "long", 3000, 3100, 100, 3.3, "ai", "ai_tp", 8)
        opt.record_trade("ETH/USDT:USDT", "short", 3100, 3050, 50, 1.6, "ai", "ai_tp", 6)
        opt.record_trade("BTC/USDT:USDT", "long", 60000, 61200, 1200, 2.0, "ai", "ai_tp", 12)
        assert opt.consecutive_wins == 3
        # Should have started restoring
        assert opt.confidence_threshold < 0.75  # Not at max anymore

    def test_recent_sharpe(self):
        """Sharpe ratio of recent trades is calculated."""
        opt = SelfOptimizer(model_dir=self.MODEL_DIR)
        # Add some positive and negative trades
        for i in range(10):
            pnl = 100 if i % 2 == 0 else -50
            opt.record_trade("BTC", "long", 60000, 60100, pnl, pnl / 600, "ai", "ai", 4)
        sharpe = opt.recent_sharpe(10)
        assert sharpe > 0  # More wins than losses

    def test_get_trade_stats(self):
        """Returns complete statistics."""
        opt = SelfOptimizer(model_dir=self.MODEL_DIR)
        opt.record_trade("BTC", "long", 60000, 61200, 1200, 2.0, "ai", "tp", 12)
        opt.record_trade("ETH", "short", 3000, 2950, 50, 1.6, "ai", "tp", 6)

        stats = opt.get_trade_stats()
        assert stats["total_trades"] == 2
        assert stats["win_rate"] == 1.0
        assert stats["total_pnl"] == 1250.0
        assert "avg_win" in stats
        assert "avg_loss" in stats
        assert "current_confidence_threshold" in stats
        assert "current_position_scalar" in stats

    def test_state_persistence(self):
        """ModelDir is a Path, not str."""
        opt = SelfOptimizer(model_dir=Path(self.MODEL_DIR))
        assert isinstance(opt.model_dir, Path)
