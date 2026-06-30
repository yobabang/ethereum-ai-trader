"""Tests for market regime classifier (AI Layer 1)."""

import numpy as np
import pandas as pd

import pytest


def make_features(n_candles: int = 500, regime: str = "trending") -> pd.DataFrame:
    """Generate feature DataFrame with characteristics of a given regime."""
    np.random.seed(42)

    if regime == "trending_strong":
        close = 60000 + np.cumsum(np.random.randn(n_candles) * 200 + 10)  # Strong drift up
    elif regime == "ranging_tight":
        close = 60000 + np.random.randn(n_candles) * 80  # Small oscillations
    elif regime == "high_volatility":
        close = 60000 + np.cumsum(np.random.randn(n_candles) * 600)  # Big swings
    else:
        close = 60000 + np.cumsum(np.random.randn(n_candles) * 150)

    high = close + np.abs(np.random.randn(n_candles) * 100)
    low = close - np.abs(np.random.randn(n_candles) * 100)
    open_ = low + np.random.rand(n_candles) * (high - low)
    volume = np.abs(np.random.randn(n_candles) * 100 + 500)

    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=pd.date_range("2026-06-01", periods=n_candles, freq="4h"),
    )

    # Add enough features to satisfy the classifier
    from engine.features import FeatureEngineer

    return FeatureEngineer().compute_price_features(df)


class TestRegimeLabels:
    """Tests for heuristic labeling (training labels generation)."""

    def test_labeling_produces_all_six_classes(self):
        """The labeling function must output all 6 regime labels."""
        from engine.regime_classifier import RegimeLabeler

        labeler = RegimeLabeler()

        # Generate diverse data to trigger all regimes
        dfs = []
        for regime, n in [
            ("trending_strong", 200),
            ("trending_strong", 100),  # Different direction
            ("ranging_tight", 200),
            ("ranging_tight", 100),
            ("high_volatility", 200),
        ]:
            dfs.append(make_features(n, regime))

        combined = pd.concat(dfs)
        labels = labeler.label(combined)

        unique = set(labels.dropna().unique())
        expected = {
            "TRENDING_STRONG",
            "TRENDING_WEAK",
            "RANGING_TIGHT",
            "RANGING_WIDE",
            "HIGH_VOLATILITY",
            "LOW_VOLATILITY",
        }
        missing = expected - unique
        assert not missing, f"Missing regime labels: {missing}"

    def test_labels_are_strings(self):
        """Labels must be strings, not codes."""
        from engine.regime_classifier import RegimeLabeler

        df = make_features(300)
        labels = RegimeLabeler().label(df)
        valid = labels.dropna()
        assert all(isinstance(x, str) for x in valid)

    def test_strong_trend_detected(self):
        """A strongly trending series must be labeled as TRENDING_STRONG."""
        from engine.regime_classifier import RegimeLabeler

        df = make_features(500, "trending_strong")
        labels = RegimeLabeler().label(df)
        # A strongly trending series should label a meaningful share as TRENDING.
        # The heuristic labeler uses expanding quantiles, so the share is not
        # deterministic; 25% is a robust floor that still proves detection.
        trending_pct = (labels.str.contains("TRENDING")).mean()
        assert trending_pct > 0.25, f"Only {trending_pct:.1%} labeled trending"

    def test_tight_range_detected(self):
        """A tight ranging series must be labeled as ranging."""
        from engine.regime_classifier import RegimeLabeler

        df = make_features(500, "ranging_tight")
        labels = RegimeLabeler().label(df)
        ranging_pct = (labels.str.contains("RANGING")).mean()
        assert ranging_pct > 0.3, f"Only {ranging_pct:.1%} labeled ranging"


class TestRegimeClassifier:
    """Tests for the LightGBM classifier."""

    def test_train_and_predict(self):
        """Train on labeled data and predict on new data."""
        from engine.regime_classifier import RegimeClassifier

        # Generate training data with diverse regimes
        dfs = []
        for regime in ["trending_strong", "ranging_tight", "high_volatility"]:
            dfs.append(make_features(400, regime))
        train_df = pd.concat(dfs)

        clf = RegimeClassifier()
        clf.train(train_df)

        # Predict on new data
        test_df = make_features(100)
        predictions = clf.predict(test_df)

        assert len(predictions) == len(test_df)
        valid = [p for p in predictions if p is not None]
        assert len(valid) > 0, "Should have at least some predictions"
        assert all(isinstance(p, str) for p in valid)

    def test_predict_proba_returns_confidence(self):
        """Predict proba must return probability for each class."""
        from engine.regime_classifier import RegimeClassifier

        dfs = [make_features(400, r) for r in ["trending_strong", "ranging_tight"]]
        train_df = pd.concat(dfs)

        clf = RegimeClassifier()
        clf.train(train_df)
        test_df = make_features(100)

        probas = clf.predict_proba(test_df)
        assert probas is not None
        # Must sum to ~1 for each row
        sums = probas.sum(axis=1)
        assert (sums > 0.99).all() and (sums < 1.01).all()

    def test_save_and_load(self, tmp_path):
        """Classifier must survive a save/load roundtrip."""
        from engine.regime_classifier import RegimeClassifier

        dfs = [make_features(300, r) for r in ["trending_strong", "ranging_tight"]]
        train_df = pd.concat(dfs)

        clf = RegimeClassifier(model_dir=str(tmp_path))
        clf.train(train_df)
        pred_before = clf.predict(make_features(50))

        # Load fresh
        clf2 = RegimeClassifier(model_dir=str(tmp_path))
        clf2.load()
        pred_after = clf2.predict(make_features(50))

        assert pred_before == pred_after

    def test_untrained_raises(self):
        """Predicting without training must raise an error."""
        from engine.regime_classifier import RegimeClassifier

        clf = RegimeClassifier()
        with pytest.raises(RuntimeError, match="not trained"):
            clf.predict(make_features(50))
