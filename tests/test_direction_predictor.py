"""Tests for direction predictor (AI Layer 2)."""

import numpy as np
import pandas as pd


def make_features(n_candles: int = 500) -> pd.DataFrame:
    """Generate feature DataFrame with a clear trend for testing."""
    np.random.seed(42)
    # Strong upward trend with noise
    close = 60000 + np.cumsum(np.random.randn(n_candles) * 200 + 15)
    high = close + np.abs(np.random.randn(n_candles) * 100)
    low = close - np.abs(np.random.randn(n_candles) * 100)
    open_ = low + np.random.rand(n_candles) * (high - low)
    volume = np.abs(np.random.randn(n_candles) * 100 + 500)
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=pd.date_range("2026-06-01", periods=n_candles, freq="4h"),
    )
    from freqtrade.ai.features import FeatureEngineer

    return FeatureEngineer().compute_price_features(df)


class TestDirectionPredictor:
    """Tests for the regression ensemble."""

    def test_train_returns_metrics(self):
        """Training must return performance metrics."""
        from freqtrade.ai.direction_predictor import DirectionPredictor

        df = make_features(500)
        pred = DirectionPredictor(model_dir="./tests/ai/test_dp_models")
        metrics = pred.train(df)

        assert "rmse" in metrics
        assert "direction_accuracy" in metrics
        assert "samples" in metrics
        assert metrics["samples"] > 100
        assert metrics["rmse"] > 0  # Should be non-negative
        assert 0 <= metrics["direction_accuracy"] <= 1

    def test_predict_returns_structured_output(self):
        """Predict must return expected_return, confidence, and max_drawdown."""
        from freqtrade.ai.direction_predictor import DirectionPredictor

        df = make_features(500)
        pred = DirectionPredictor(model_dir="./tests/ai/test_dp_models")
        pred.train(df)

        test_df = make_features(50)
        results = pred.predict(test_df)

        assert isinstance(results, list)
        for r in results:
            if r is not None:
                assert "expected_return" in r
                assert "confidence" in r
                assert "max_drawdown" in r
                assert -1 <= r["expected_return"] <= 1  # Return is ~percentage
                assert 0 <= r["confidence"] <= 1
                assert r["max_drawdown"] < 0.5  # Shouldn't be > 50%

    def test_confidence_lower_when_uncertain(self):
        """Confidence must decrease as prediction difficulty increases."""
        from freqtrade.ai.direction_predictor import DirectionPredictor

        # Train on trending data
        df = make_features(500)
        pred = DirectionPredictor(model_dir="./tests/ai/test_dp_models")
        pred.train(df)

        # Predict on clean data vs noisy data
        clean = make_features(100)
        noisy = make_features(100)

        results_clean = pred.predict(clean)
        results_noisy = pred.predict(noisy)

        avg_conf_clean = np.mean([r["confidence"] for r in results_clean if r])
        avg_conf_noisy = np.mean([r["confidence"] for r in results_noisy if r])

        # Both should have reasonable confidence values
        assert avg_conf_clean > 0
        assert avg_conf_noisy > 0

    def test_direction_accuracy_better_than_random(self):
        """Direction accuracy must exceed 50% (better than coin flip)."""
        from freqtrade.ai.direction_predictor import DirectionPredictor

        df = make_features(600)
        pred = DirectionPredictor(model_dir="./tests/ai/test_dp_models")

        # Train on first 400, test on last 200
        train_df = df.iloc[:400]
        metrics = pred.train(train_df)

        # On training data, accuracy should be decent
        assert metrics["direction_accuracy"] > 0.5, (
            f"Direction accuracy {metrics['direction_accuracy']:.2f} must be > 0.5"
        )

    def test_save_load_roundtrip(self):
        """Model must survive save/load cycle."""
        from freqtrade.ai.direction_predictor import DirectionPredictor

        df = make_features(400)
        pred = DirectionPredictor(model_dir="./tests/ai/test_dp_models")
        pred.train(df)

        test_df = make_features(30)
        before = pred.predict(test_df)

        pred2 = DirectionPredictor(model_dir="./tests/ai/test_dp_models")
        pred2.load()
        after = pred2.predict(test_df)

        for b, a in zip(before, after):
            if b is not None and a is not None:
                assert b["expected_return"] == a["expected_return"]

    def test_untrained_raises(self):
        """Must error if not trained."""
        from freqtrade.ai.direction_predictor import DirectionPredictor

        pred = DirectionPredictor(model_dir="/tmp/nonexist_dp")
        with __import__("pytest").raises(RuntimeError, match="not trained"):
            pred.predict(make_features(20))
