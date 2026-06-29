"""Market regime classifier — AI Decision Layer 1.

Classifies the current market state into one of 6 regimes:
  TRENDING_STRONG, TRENDING_WEAK, RANGING_TIGHT,
  RANGING_WIDE, HIGH_VOLATILITY, LOW_VOLATILITY

Uses heuristic labeling to generate training data, then trains
a LightGBM classifier for fast inference.
"""

import logging
import os
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Feature columns to exclude from training (raw data, not indicators)
EXCLUDE_COLS = {"open", "high", "low", "close", "volume"}


class RegimeLabeler:
    """Generates training labels by heuristic analysis of feature data.

    These labels are used to train the LightGBM classifier, which then
    generalizes beyond the heuristics.
    """

    # Priority order: first matching rule wins
    def label(self, df: pd.DataFrame) -> pd.Series:
        """Label each row with a regime.

        Args:
            df: Feature DataFrame from FeatureEngineer.compute_price_features().

        Returns:
            pd.Series of string regime labels.
        """
        labels = pd.Series("UNKNOWN", index=df.index, dtype=str)

        # Extract feature columns
        adx = df.get("adx_14", pd.Series(20, index=df.index))
        atr_ratio = df.get("atr_ratio", pd.Series(0.01, index=df.index))
        vol_ratio = df.get("volatility_ratio", pd.Series(1, index=df.index))
        bb_width = df.get("bb_width", pd.Series(0.02, index=df.index))
        ema_cross = df.get("ema_cross_9_21", pd.Series(0, index=df.index))
        returns_4 = df.get("returns_4", pd.Series(0, index=df.index))
        rsi = df.get("rsi_14", pd.Series(50, index=df.index))

        # ---- Pass 1: Volatility extremes (expanding window to avoid lookahead) ----
        vol_cut_high = vol_ratio.expanding(min_periods=50).quantile(0.85)
        vol_cut_low = vol_ratio.expanding(min_periods=50).quantile(0.15)
        atr_cut_high = atr_ratio.expanding(min_periods=50).quantile(0.85)
        atr_cut_low = atr_ratio.expanding(min_periods=50).quantile(0.5)

        labels[vol_ratio > vol_cut_high] = "HIGH_VOLATILITY"
        labels[(vol_ratio < vol_cut_low) & (atr_ratio < atr_cut_low)] = "LOW_VOLATILITY"

        # ---- Pass 2: Strong trend (high ADX + fast returns) ----
        ret_cut = returns_4.abs().quantile(0.5)
        labels[
            (adx > 25)
            & (labels == "UNKNOWN")
            & (returns_4.abs() > ret_cut)
        ] = "TRENDING_STRONG"

        # ---- Pass 3: Weak trend (moderate ADX) ----
        labels[
            (adx > 18)
            & (labels == "UNKNOWN")
        ] = "TRENDING_WEAK"

        # ---- Pass 4: Ranging (low ADX) ----
        bb_cut = bb_width.quantile(0.5)
        labels[
            (labels == "UNKNOWN") & (bb_width < bb_cut)
        ] = "RANGING_TIGHT"
        labels[labels == "UNKNOWN"] = "RANGING_WIDE"

        return labels


class RegimeClassifier:
    """LightGBM classifier for market regime detection.

    Wraps training, inference, and persistence. Feature columns are
    auto-selected by excluding raw OHLCV columns.
    """

    REGIMES = [
        "TRENDING_STRONG",
        "TRENDING_WEAK",
        "RANGING_TIGHT",
        "RANGING_WIDE",
        "HIGH_VOLATILITY",
        "LOW_VOLATILITY",
    ]

    def __init__(self, model_dir: str = "./models"):
        self._model = None
        self._feature_cols: list[str] = []
        self._model_dir = Path(model_dir)
        self._model_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def train(self, df: pd.DataFrame) -> dict:
        """Train the classifier on labeled feature data.

        Args:
            df: Feature DataFrame (from FeatureEngineer). Labels are
                generated internally via RegimeLabeler.

        Returns:
            dict with training metrics.
        """
        import lightgbm as lgb

        # Generate labels
        labeler = RegimeLabeler()
        y = labeler.label(df)

        # Select feature columns (numeric only — exclude date/index columns)
        self._feature_cols = [
            c for c in df.columns
            if c not in EXCLUDE_COLS
            and df[c].dtype.kind in ('f', 'i')  # float or int only
        ]

        # Drop rows with NaN (warmup period)
        valid = y.notna() & df[self._feature_cols].notna().all(axis=1)
        X = df.loc[valid, self._feature_cols]
        y = y[valid]

        if len(X) < 100:
            raise ValueError(f"Need at least 100 labeled samples, got {len(X)}")

        # Train LightGBM
        self._model = lgb.LGBMClassifier(
            objective="multiclass",
            num_class=len(self.REGIMES),
            class_weight="balanced",
            n_estimators=100,
            max_depth=5,
            num_leaves=31,
            learning_rate=0.05,
            verbose=-1,
            random_state=42,
        )
        self._model.fit(X, y)

        # Evaluate
        train_acc = self._model.score(X, y)
        logger.info(f"Regime classifier trained: accuracy={train_acc:.3f}, samples={len(X)}")

        # Save
        self._save()

        return {"accuracy": round(train_acc, 4), "samples": len(X)}

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def predict(self, df: pd.DataFrame) -> list[Optional[str]]:
        """Predict regime labels for each row.

        Args:
            df: Feature DataFrame.

        Returns:
            List of regime strings (None for rows with NaN features).
        """
        if self._model is None:
            raise RuntimeError("Model not trained. Call train() or load() first.")

        cols = [c for c in self._feature_cols if c in df.columns]
        if not cols:
            raise ValueError("No matching feature columns found in input DataFrame")

        X = df[cols]
        results: list[Optional[str]] = []

        for i in range(len(X)):
            row = X.iloc[i]
            if row.isna().any():
                results.append(None)
            else:
                # Pass a DataFrame to preserve feature names
                pred = self._model.predict(row.to_frame().T)[0]
                results.append(pred)

        return results

    def predict_proba(self, df: pd.DataFrame) -> Optional[pd.DataFrame]:
        """Return class probabilities for each row.

        Returns:
            DataFrame with columns = regime names, or None if not trained.
        """
        if self._model is None:
            raise RuntimeError("Model not trained.")

        cols = [c for c in self._feature_cols if c in df.columns]
        X = df[cols].dropna()

        if len(X) == 0:
            return None

        proba = self._model.predict_proba(X)
        return pd.DataFrame(proba, index=X.index, columns=self._model.classes_)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _model_path(self) -> Path:
        return self._model_dir / "regime_classifier.pkl"

    def _save(self) -> None:
        import joblib

        joblib.dump(
            {"model": self._model, "feature_cols": self._feature_cols},
            self._model_path(),
        )

    def load(self) -> None:
        """Load a previously trained model from disk."""
        import joblib

        path = self._model_path()
        if not path.exists():
            raise FileNotFoundError(f"No model found at {path}")

        data = joblib.load(path)
        self._model = data["model"]
        self._feature_cols = data["feature_cols"]
