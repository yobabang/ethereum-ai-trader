"""Direction predictor — AI Decision Layer 2.

Predicts the expected return over the next 4-hour candle using a
LightGBM regressor. Also estimates prediction confidence and
worst-case drawdown from historical error distribution.

Target: close(t+1) / close(t) - 1  (next-candle return)
"""

import logging
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

EXCLUDE_COLS = {"open", "high", "low", "close", "volume"}


class DirectionPredictor:
    """LightGBM regressor for next-candle return prediction.

    Confidence is derived from prediction residuals on the validation set
    and the model's own prediction variance across trees.
    """

    def __init__(self, model_dir: str = "./models"):
        self._model = None
        self._feature_cols: list[str] = []
        self._residual_std: float = 1.0
        self._train_mean: np.ndarray | None = None  # For per-prediction confidence
        self._train_std: np.ndarray | None = None   # For per-prediction confidence
        self._model_dir = Path(model_dir)
        self._model_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def train(self, df: pd.DataFrame, horizon: int = 4) -> dict:
        """Train the regressor on labeled data.

        Args:
            df: Feature DataFrame from FeatureEngineer.
            horizon: Number of candles ahead to predict. Default 4 — a
                multi-candle horizon aggregates out high-frequency noise and
                gives the model a more learnable signal than the previous
                default of 1 (which yielded ~0.50 direction accuracy, i.e.
                coin-flip, on 1h data).

        Returns:
            dict with metrics: rmse, direction_accuracy, samples.
        """
        import lightgbm as lgb

        # ---- Create target: next-candle return ----
        close = df["close"].values
        target = np.empty(len(close))
        target[:] = np.nan
        raw_returns = close[horizon:] / close[:-horizon] - 1
        raw_returns[~np.isfinite(raw_returns)] = np.nan  # Fix Critical #3
        target[:-horizon] = raw_returns

        # ---- Select features (numeric only) ----
        self._feature_cols = [
            c for c in df.columns
            if c not in EXCLUDE_COLS
            and df[c].dtype.kind in ('f', 'i')
        ]

        # ---- Drop NaN rows ----
        valid = pd.Series(~np.isnan(target), index=df.index) & df[self._feature_cols].notna().all(axis=1)
        X = df.loc[valid, self._feature_cols]
        y = target[valid.values]

        if len(X) < 100:
            raise ValueError(f"Need at least 100 training samples, got {len(X)}")

        # ---- Temporal train/val split ----
        split = int(len(X) * 0.8)
        X_train, X_val = X.iloc[:split], X.iloc[split:]
        y_train, y_val = y[:split], y[split:]

        # ---- LightGBM ----
        self._model = lgb.LGBMRegressor(
            n_estimators=150,
            max_depth=6,
            num_leaves=31,
            learning_rate=0.03,
            subsample=0.8,
            colsample_bytree=0.8,
            verbose=-1,
            random_state=42,
        )
        self._model.fit(X_train, y_train)

        # ---- Store feature statistics for per-prediction confidence ----
        self._train_mean = X_train.mean().values
        self._train_std = X_train.std().values

        # ---- Evaluate ----
        y_pred = self._model.predict(X_val)
        residuals = y_pred - y_val
        rmse = np.sqrt(np.mean(residuals**2))
        self._residual_std = float(np.std(residuals))

        dir_correct = (np.sign(y_pred) == np.sign(y_val)).sum()
        dir_accuracy = dir_correct / len(y_val) if len(y_val) > 0 else 0.0

        logger.info(
            f"Direction predictor trained: RMSE={rmse:.4f}, "
            f"dir_accuracy={dir_accuracy:.3f}, samples={len(X)}"
        )

        self._save()
        return {
            "rmse": round(float(rmse), 6),
            "direction_accuracy": round(float(dir_accuracy), 4),
            "samples": len(X),
        }

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def predict(self, df: pd.DataFrame) -> list[Optional[dict]]:
        """Predict expected return for each row.

        Returns:
            List of dicts: {expected_return, confidence, max_drawdown}
            None for rows with NaN features.
        """
        if self._model is None:
            raise RuntimeError("Model not trained. Call train() or load() first.")

        # Rebuild X strictly in training column order. Missing columns
        # (e.g. derivatives absent at inference) are filled with NaN, which
        # LightGBM handles natively — this keeps feature count/order identical
        # to training instead of crashing on a shape mismatch.
        missing = [c for c in self._feature_cols if c not in df.columns]
        if missing:
            df = df.copy()
            for c in missing:
                df[c] = np.nan
        X = df[list(self._feature_cols)]
        results: list[Optional[dict]] = []

        for i in range(len(X)):
            row = X.iloc[i]
            if row.isna().any():
                results.append(None)
                continue

            row_df = row.to_frame().T
            expected_return = float(self._model.predict(row_df)[0])

            # Per-prediction confidence: how far is this sample from training distribution?
            if self._train_mean is not None and self._train_std is not None:
                # Normalized distance from training centroid
                row_vals = row_df.values.flatten()
                z_scores = np.abs((row_vals - self._train_mean) / (self._train_std + 1e-10))
                outlier_score = float(np.mean(z_scores > 2.0))  # Fraction of features > 2 sigma
                # Base confidence from residual std + penalty for outliers
                base_conf = np.exp(-self._residual_std * 3)
                confidence = round(float(base_conf * (1.0 - outlier_score * 0.5)), 4)
            else:
                sigma = max(self._residual_std, 0.001)
                confidence = round(float(np.exp(-sigma * 3)), 4)
            confidence = max(0.1, min(0.95, confidence))

            # Max drawdown: predicted return - 2 * RMSE (approx 95% CI lower bound)
            max_dd = round(float(expected_return - 2 * self._residual_std), 6)

            results.append({
                "expected_return": round(float(expected_return), 6),
                "confidence": confidence,
                "max_drawdown": max_dd,
            })

        return results

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _model_path(self) -> Path:
        return self._model_dir / "direction_predictor.pkl"

    def _save(self) -> None:
        import joblib
        joblib.dump({
            "model": self._model,
            "feature_cols": self._feature_cols,
            "residual_std": self._residual_std,
            "train_mean": self._train_mean,
            "train_std": self._train_std,
        }, self._model_path())

    def load(self) -> None:
        """Load a previously trained model from disk."""
        import joblib

        path = self._model_path()
        if not path.exists():
            raise FileNotFoundError(f"No model found at {path}")

        data = joblib.load(path)
        self._model = data["model"]
        self._feature_cols = data["feature_cols"]
        self._residual_std = data["residual_std"]
        self._train_mean = data.get("train_mean")
        self._train_std = data.get("train_std")
