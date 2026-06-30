"""AI Decision Core for autonomous futures trading.

Note: AIStrategy is the optional freqtrade-integration path and requires
freqtrade to be installed. The standalone engine (LiveTrader, training,
backtest) does NOT need it. Import it lazily so `import engine` works
without freqtrade.
"""

from engine.decision_arbitrator import (
    Action,
    Decision,
    DecisionArbitrator,
    Regime,
    RiskCalculator,
    RiskParams,
)
from engine.direction_predictor import DirectionPredictor
from engine.features import FeatureEngineer
from engine.regime_classifier import RegimeClassifier, RegimeLabeler

try:
    from engine.ai_strategy import AIStrategy
except ImportError:  # freqtrade not installed — standalone engine mode
    AIStrategy = None  # type: ignore[assignment,misc]

__all__ = [
    "AIStrategy",
    "Action",
    "Decision",
    "DecisionArbitrator",
    "DirectionPredictor",
    "FeatureEngineer",
    "Regime",
    "RegimeClassifier",
    "RegimeLabeler",
    "RiskCalculator",
    "RiskParams",
]
