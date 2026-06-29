"""RL Signal Source — FinRL-style reinforcement learning agent as a second
trading signal, running parallel to the LightGBM DirectionPredictor.

Architecture:
  RlTradingEnv    — Gymnasium environment wrapping OHLCV features
  RlSignalAgent   — PPO agent wrapper with predict() interface compatible
                     with DirectionPredictor

Uses Stable-Baselines3 PPO (production-quality, pip-installable) instead of
ElegantRL to keep the dependency footprint small on Windows.

Integration point: ai_strategy.py _run_ai_pipeline() Layer 2.
"""

import json
import logging
from pathlib import Path
from typing import Optional

import gymnasium as gym
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOOKBACK = 50  # Number of historical steps in the RL observation
EXCLUDE_COLS = {"open", "high", "low", "close", "volume", "date", "index"}
TRANSACTION_COST = 0.0005  # 0.05% taker fee on OKX


# ---------------------------------------------------------------------------
# RlTradingEnv — Gymnasium-compatible trading environment
# ---------------------------------------------------------------------------

class RlTradingEnv(gym.Env):
    """A Gymnasium-compatible environment for training RL trading agents.

    Observation space:
        [cash_ratio, position_ratio] + [technical_indicators * lookback]
        All values are normalized to ~[-1, 1] range.

    Action space:
        Continuous [-1, 1]:
          > 0 → long with size proportional to value
          < 0 → short with size proportional to |value|
          ~ 0 → hold / close

    Reward:
        portfolio_return - benchmark_return (excess return over buy-and-hold)
        minus transaction costs.
    """

    def __init__(
        self,
        df: pd.DataFrame,
        initial_balance: float = 10_000.0,
        lookback: int = LOOKBACK,
        max_position_pct: float = 0.20,
        transaction_cost: float = TRANSACTION_COST,
    ):
        """Initialize the trading environment.

        Args:
            df: Feature DataFrame from FeatureEngineer (must include 'close').
            initial_balance: Starting account balance in USDT.
            lookback: Number of historical steps in the observation window.
            max_position_pct: Maximum position as fraction of equity.
            transaction_cost: Trading fee as decimal (0.0005 = 0.05%).
        """
        self.df = df.reset_index(drop=True)
        self.initial_balance = initial_balance
        self.lookback = lookback
        self.max_position_pct = max_position_pct
        self.transaction_cost = transaction_cost

        # Identify feature columns (numeric, non-OHLCV)
        self.feature_cols = [
            c
            for c in df.columns
            if c not in EXCLUDE_COLS and df[c].dtype.kind in ("f", "i")
        ]
        if not self.feature_cols:
            raise ValueError("No numeric feature columns found in DataFrame")

        # Pre-compute feature statistics for normalization
        self._feat_mean = self.df[self.feature_cols].mean().values.astype(np.float32)
        self._feat_std = self.df[self.feature_cols].std().values.astype(np.float32)
        self._feat_std[self._feat_std == 0] = 1.0  # Avoid division by zero

        # Ensure 'close' is available for benchmark
        if "close" not in df.columns:
            raise ValueError("DataFrame must contain 'close' column")

        self.close = df["close"].values.astype(np.float32)
        self.features = self.df[self.feature_cols].values.astype(np.float32)

        # Observation and action dimensions
        self.obs_dim = 2 + len(self.feature_cols) * lookback
        self.action_dim = 1

        # Gymnasium spaces
        self.observation_space = gym.spaces.Box(
            low=-10.0, high=10.0, shape=(self.obs_dim,), dtype=np.float32
        )
        self.action_space = gym.spaces.Box(
            low=-1.0, high=1.0, shape=(1,), dtype=np.float32
        )

        # State
        self._step_idx = lookback
        self._max_step = len(self.df) - 1
        self._balance = initial_balance
        self._position_size = 0.0  # Positive = long, negative = short
        self._entry_price = 0.0
        self._benchmark_start_price = self.close[lookback]

    # -- Gymnasium API -------------------------------------------------------

    def reset(self, seed: int | None = None, options: dict | None = None) -> tuple[np.ndarray, dict]:
        """Reset the environment to initial state.

        Returns:
            (observation, info) tuple (Gymnasium API).
        """
        super().reset(seed=seed)
        if seed is not None:
            np.random.seed(seed)

        self._step_idx = self.lookback
        self._balance = self.initial_balance
        self._position_size = 0.0
        self._entry_price = 0.0
        self._benchmark_start_price = self.close[self.lookback]

        return self._get_obs(), {}

    def step(self, action: np.ndarray) -> tuple[np.ndarray, float, bool, bool, dict]:
        """Execute one step.

        Args:
            action: np.array of shape (1,) with value in [-1, 1].

        Returns:
            (observation, reward, terminated, truncated, info)
        """
        raw_action = float(np.clip(action.item() if hasattr(action, "item") else action, -1.0, 1.0))

        current_price = self.close[self._step_idx]
        prev_equity = self._get_equity(current_price)

        # --- Execute action ---
        target_position_pct = raw_action * self.max_position_pct

        if abs(target_position_pct) < 0.001:
            # Close any open position
            if self._position_size != 0:
                pnl = self._position_size * (current_price - self._entry_price)
                if self._position_size < 0:
                    pnl = -pnl  # Short: profit when price drops
                cost = abs(self._position_size) * self.transaction_cost
                self._balance += pnl - cost
                self._position_size = 0.0
                self._entry_price = 0.0
        else:
            # Open or adjust position
            desired_size = target_position_pct * self._balance / current_price

            if self._position_size == 0:
                # Open new position
                self._position_size = desired_size
                self._entry_price = current_price
                cost = abs(self._position_size * current_price) * self.transaction_cost
                self._balance -= cost
            elif np.sign(desired_size) == np.sign(self._position_size):
                # Same direction — adjust size
                size_delta = desired_size - self._position_size
                cost = abs(size_delta * current_price) * self.transaction_cost
                self._balance -= cost
                self._position_size = desired_size
            else:
                # Direction flip — close then open
                pnl = self._position_size * (current_price - self._entry_price)
                if self._position_size < 0:
                    pnl = -pnl
                self._balance += pnl
                cost_close = abs(self._position_size * current_price) * self.transaction_cost
                self._balance -= cost_close
                self._position_size = desired_size
                self._entry_price = current_price
                cost_open = abs(self._position_size * current_price) * self.transaction_cost
                self._balance -= cost_open

        # --- Advance step ---
        self._step_idx += 1
        terminated = self._step_idx >= self._max_step
        truncated = self._balance <= self.initial_balance * 0.3  # 70% drawdown = bankruptcy

        # --- Compute reward ---
        new_price = self.close[self._step_idx] if not terminated else current_price
        new_equity = self._get_equity(new_price)

        # Benchmark: equal-weight buy-and-hold
        benchmark_return = (new_price / self._benchmark_start_price) - 1.0
        portfolio_return = (new_equity / self.initial_balance) - 1.0

        reward = portfolio_return - benchmark_return

        # --- Info ---
        info = {
            "portfolio_return": portfolio_return,
            "benchmark_return": benchmark_return,
            "equity": new_equity,
            "position_size": self._position_size,
        }

        return self._get_obs(), reward, terminated, truncated, info

    # -- Observation construction --------------------------------------------

    def _get_obs(self) -> np.ndarray:
        """Build the normalized observation vector."""
        # Cash ratio (0-1)
        cash_ratio = np.array([self._balance / self.initial_balance], dtype=np.float32)

        # Position ratio (-1 to 1)
        current_price = self.close[self._step_idx]
        equity = max(self._get_equity(current_price), 1.0)
        pos_value = abs(self._position_size) * current_price
        pos_ratio = np.array([self._position_size * current_price / equity], dtype=np.float32)

        # Feature window: last `lookback` steps, normalized
        start = max(0, self._step_idx - self.lookback + 1)
        window = self.features[start : self._step_idx + 1]
        # Pad if not enough history
        if len(window) < self.lookback:
            pad = np.zeros((self.lookback - len(window), len(self.feature_cols)), dtype=np.float32)
            window = np.concatenate([pad, window], axis=0)

        # Normalize
        window_norm = (window - self._feat_mean) / (self._feat_std + 1e-8)
        window_norm = np.nan_to_num(window_norm, nan=0.0, posinf=1.0, neginf=-1.0)
        window_flat = window_norm.flatten().astype(np.float32)

        return np.concatenate([cash_ratio, pos_ratio, window_flat])

    def render(self, mode: str = "human") -> None:
        """Gymnasium render — no-op for training."""
        pass

    def _get_equity(self, price: float) -> float:
        """Total account equity = cash + unrealized PnL."""
        if self._position_size == 0:
            return self._balance
        if self._position_size > 0:
            unrealized = self._position_size * (price - self._entry_price)
        else:
            unrealized = -self._position_size * (price - self._entry_price)
        return self._balance + unrealized


# ---------------------------------------------------------------------------
# RlSignalAgent — inference wrapper with DirectionPredictor-compatible API
# ---------------------------------------------------------------------------

class RlSignalAgent:
    """Wraps a trained PPO agent for inference.

    Provides a `predict()` method that returns the same dict format as
    DirectionPredictor, so it can be used as a drop-in signal source.

    The model file is a PyTorch state dict saved by the training script.
    """

    def __init__(self, model_dir: str = "./models"):
        self._model_dir = Path(model_dir)
        self._model_dir.mkdir(parents=True, exist_ok=True)
        self._model = None
        self._feature_cols: list[str] = []
        self._feat_mean: np.ndarray | None = None
        self._feat_std: np.ndarray | None = None
        self._lookback = LOOKBACK
        self._loaded = False

    # ------------------------------------------------------------------
    # Model persistence
    # ------------------------------------------------------------------

    @property
    def model_path(self) -> Path:
        return self._model_dir / "rl_actor.zip"

    @property
    def config_path(self) -> Path:
        return self._model_dir / "rl_train_config.json"

    def load(self) -> bool:
        """Load trained model and config from disk.

        Returns:
            True if model loaded successfully, False otherwise.
        """
        if not self.model_path.exists():
            logger.info("No RL model found at %s — using LightGBM-only mode", self.model_path)
            self._loaded = False
            return False

        try:
            from stable_baselines3 import PPO

            self._model = PPO.load(str(self.model_path))
            logger.info("RL model loaded from %s", self.model_path)
        except ImportError:
            logger.warning("stable-baselines3 not installed — RL signal disabled")
            self._loaded = False
            return False
        except Exception as e:
            logger.warning("Failed to load RL model: %s", e)
            self._loaded = False
            return False

        # Load feature config
        if self.config_path.exists():
            try:
                with open(self.config_path) as f:
                    cfg = json.load(f)
                self._feature_cols = cfg.get("feature_cols", [])
                self._feat_mean = np.array(cfg["feat_mean"], dtype=np.float32) if cfg.get("feat_mean") else None
                self._feat_std = np.array(cfg["feat_std"], dtype=np.float32) if cfg.get("feat_std") else None
                self._lookback = cfg.get("lookback", LOOKBACK)
            except Exception as e:
                logger.warning("Failed to load RL config: %s", e)

        self._loaded = True
        return True

    @property
    def is_loaded(self) -> bool:
        return self._loaded and self._model is not None

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------

    def predict(self, latest_df: pd.DataFrame) -> list[dict]:
        """Generate RL signal for the latest candle.

        Args:
            latest_df: Feature DataFrame rows. Uses the last `lookback` rows
                       to construct the observation.

        Returns:
            list of dicts: [{"expected_return": float, "confidence": float,
                              "max_drawdown": float, "action": float}]
            Returns empty list if model not loaded or data insufficient.
        """
        if not self.is_loaded:
            return []

        try:
            obs = self._build_observation(latest_df)
            if obs is None:
                return []

            action, _states = self._model.predict(obs, deterministic=True)
            raw_action = float(action.item() if hasattr(action, "item") else action[0])

            # Map action [-1, 1] to expected_return
            # Action magnitude → expected return
            # Action sign → direction
            mapped_return = raw_action * 0.03  # Max 3% expected return per 4h

            # Confidence from action probability (PPO stores log_prob in _states)
            # When deterministic=True, confidence is based on action magnitude
            confidence = min(0.5 + abs(raw_action) * 0.45, 0.95)  # 0.5-0.95 range

            # Max drawdown estimate: higher confidence → lower expected drawdown
            max_dd = -0.02 - (1.0 - confidence) * 0.06  # -0.02 to -0.08

            return [
                {
                    "expected_return": round(float(mapped_return), 6),
                    "confidence": round(float(confidence), 4),
                    "max_drawdown": round(float(max_dd), 4),
                    "action": round(float(raw_action), 4),
                    "source": "rl",
                }
            ]
        except Exception as e:
            logger.warning("RL prediction failed: %s", e)
            return []

    def _build_observation(self, latest_df: pd.DataFrame) -> np.ndarray | None:
        """Construct the RL observation from the latest feature rows.

        Uses the last `lookback` rows of the DataFrame.
        """
        if len(latest_df) < self._lookback:
            logger.debug("Not enough data for RL obs: %d < %d", len(latest_df), self._lookback)
            return None

        window = latest_df.iloc[-self._lookback :]

        # Select feature columns
        if self._feature_cols:
            available = [c for c in self._feature_cols if c in window.columns]
            if len(available) < len(self._feature_cols) * 0.5:
                logger.debug("Too few RL feature columns available: %d/%d", len(available), len(self._feature_cols))
                return None
            feats = window[available].values.astype(np.float32)
        else:
            # Fallback: use all numeric columns except OHLCV
            cols = [c for c in window.columns if c not in EXCLUDE_COLS and window[c].dtype.kind in ("f", "i")]
            feats = window[cols].values.astype(np.float32)

        # Normalize using stored stats
        if self._feat_mean is not None and self._feat_std is not None:
            if len(self._feat_mean) == feats.shape[1]:
                feats = (feats - self._feat_mean) / (self._feat_std + 1e-8)
            else:
                # Feature count mismatch — use local normalization
                m = feats.mean(axis=0)
                s = feats.std(axis=0)
                s[s == 0] = 1.0
                feats = (feats - m) / s
        feats = np.nan_to_num(feats, nan=0.0, posinf=1.0, neginf=-1.0)

        # Cash ratio (assume 1.0 — inference doesn't track real balance)
        cash_ratio = np.array([1.0], dtype=np.float32)
        pos_ratio = np.array([0.0], dtype=np.float32)  # Agent sees zero position

        obs = np.concatenate([cash_ratio, pos_ratio, feats.flatten()]).astype(np.float32)

        # Pad/truncate to expected dimension
        expected_dim = 2 + len(self._feature_cols) * self._lookback if self._feature_cols else len(obs)
        if len(obs) < expected_dim:
            obs = np.pad(obs, (0, expected_dim - len(obs)))
        elif len(obs) > expected_dim:
            obs = obs[:expected_dim]

        return obs


# ---------------------------------------------------------------------------
# Training helper (used by rl_trainer.py and training_pipeline.py)
# ---------------------------------------------------------------------------

def train_rl_agent(
    df: pd.DataFrame,
    model_dir: str = "./models",
    total_timesteps: int = 100_000,
    lookback: int = LOOKBACK,
) -> dict:
    """Train a PPO agent on historical OHLCV features.

    Args:
        df: Feature DataFrame from FeatureEngineer.
        model_dir: Directory to save the trained model.
        total_timesteps: Number of environment steps for training.
        lookback: Observation window size.

    Returns:
        dict with training metrics: {model_path, feature_cols, lookback,
                                     feat_mean, feat_std}
    """
    try:
        from stable_baselines3 import PPO
        from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
    except ImportError:
        logger.error("stable-baselines3 required for RL training. pip install stable-baselines3")
        return {}

    model_path = Path(model_dir)
    model_path.mkdir(parents=True, exist_ok=True)

    logger.info("Training RL agent on %d rows, %d timesteps...", len(df), total_timesteps)

    # Create environment
    def make_env():
        return RlTradingEnv(df, lookback=lookback)

    env = DummyVecEnv([make_env])
    env = VecNormalize(env, norm_obs=True, norm_reward=True, clip_obs=10.0)

    # Train PPO
    model = PPO(
        "MlpPolicy",
        env,
        verbose=1,
        learning_rate=3e-4,
        n_steps=2048,
        batch_size=64,
        n_epochs=10,
        gamma=0.99,
        gae_lambda=0.95,
        clip_range=0.2,
        ent_coef=0.01,
        policy_kwargs=dict(
            net_arch=dict(pi=[128, 64], vf=[128, 64]),
        ),
    )
    model.learn(total_timesteps=total_timesteps, progress_bar=True)

    # Save model
    save_path = model_path / "rl_actor.zip"
    model.save(str(save_path))
    logger.info("RL model saved to %s", save_path)

    # Save VecNormalize stats
    norm_path = model_path / "rl_vecnormalize.pkl"
    env.save(str(norm_path))

    # Save feature config for inference
    feature_cols = [c for c in df.columns if c not in EXCLUDE_COLS and df[c].dtype.kind in ("f", "i")]
    config = {
        "feature_cols": feature_cols,
        "lookback": lookback,
        "feat_mean": df[feature_cols].mean().values.tolist(),
        "feat_std": df[feature_cols].std().fillna(1.0).values.tolist(),
        "total_timesteps": total_timesteps,
    }
    config_path = model_path / "rl_train_config.json"
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

    logger.info("RL training complete. Feature config saved to %s", config_path)
    return config
