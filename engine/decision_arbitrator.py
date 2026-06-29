"""Risk Calculator & Decision Arbitrator — AI Layers 3 & 4.

Layer 3 (RiskCalculator): Computes safe position size, stop-loss,
take-profit, and leverage based on account state and market regime.

Layer 4 (DecisionArbitrator): Makes the final trade decision by
combining regime, prediction, and risk limits, then applying
hardcoded safety rules that AI cannot override.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums & Data classes
# ---------------------------------------------------------------------------

class Action(str, Enum):
    LONG = "long"
    SHORT = "short"
    CLOSE_LONG = "close_long"
    CLOSE_SHORT = "close_short"
    HOLD = "hold"
    STOP = "stop"  # Emergency stop, no trading allowed


class Regime(str, Enum):
    TRENDING_STRONG = "TRENDING_STRONG"
    TRENDING_WEAK = "TRENDING_WEAK"
    RANGING_TIGHT = "RANGING_TIGHT"
    RANGING_WIDE = "RANGING_WIDE"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"


@dataclass
class RiskParams:
    """Output of RiskCalculator."""

    max_position_pct: float  # Max % of account equity to risk
    stop_loss_pct: float  # Stop loss as % of entry price (e.g. 0.015 = 1.5%)
    take_profit_pct: float  # Take profit as % of entry price
    leverage: int  # 2-5
    allow_trade: bool  # False = market too dangerous

    reasons: list[str] = field(default_factory=list)


@dataclass
class Decision:
    """Final trade decision from the arbitrator."""

    action: Action
    position_size_pct: float  # 0.0 - max_position_pct
    leverage: int
    stop_loss_pct: float
    take_profit_pct: float
    confidence: float
    expected_return: float = 0.0  # From Layer 2 prediction
    reason: str = ""  # Human-readable explanation


# ---------------------------------------------------------------------------
# Layer 3: Risk Calculator
# ---------------------------------------------------------------------------

class RiskCalculator:
    """Computes safe trading parameters based on account and market state.

    This is pure math + rules. No ML involved.
    """

    # Base risk parameters
    MAX_LEVERAGE = 5
    MAX_POSITION_PCT = 0.20
    MAX_DRAWDOWN_PCT = 0.15
    DAILY_LOSS_LIMIT_PCT = 0.05
    PER_TRADE_MAX_LOSS_PCT = 0.08  # Cap single-trade loss at 8% of position
    MIN_CONFIDENCE = 0.55

    def __init__(
        self,
        max_leverage: int = 5,
        max_position_pct: float = 0.20,
        max_drawdown_pct: float = 0.15,
        per_trade_max_loss_pct: float = 0.08,
        daily_loss_limit_pct: float = 0.05,
    ):
        self.MAX_LEVERAGE = max_leverage
        self.MAX_POSITION_PCT = max_position_pct
        self.MAX_DRAWDOWN_PCT = max_drawdown_pct
        self.DAILY_LOSS_LIMIT_PCT = daily_loss_limit_pct
        self.PER_TRADE_MAX_LOSS_PCT = per_trade_max_loss_pct

    def calculate(
        self,
        account_equity: float,
        current_positions: list[dict],
        regime: str,
        confidence: float,
        atr_pct: float,
        daily_pnl: float = 0.0,
    ) -> RiskParams:
        """Compute position sizing and risk limits.

        Args:
            account_equity: Total account value in USDT.
            current_positions: List of open positions [{pair, side, size, pnl}].
            regime: Market regime from Layer 1.
            confidence: Model confidence from Layer 2 (0-1).
            atr_pct: ATR(14) / price as percentage.
            daily_pnl: Today's realized PnL in USDT.

        Returns:
            RiskParams with position limits.
        """
        reasons: list[str] = []
        allow_trade = True

        # ---- 1. Regime-based adjustments ----
        regime_multipliers = {
            "TRENDING_STRONG": 1.0,
            "TRENDING_WEAK": 0.7,
            "LOW_VOLATILITY": 0.2,
            # Empirically proven loss-makers (test_report_regime.docx):
            "RANGING_WIDE": 0.0,
            "RANGING_TIGHT": 0.0,
            "HIGH_VOLATILITY": 0.0,
        }
        regime_mult = regime_multipliers.get(regime, 0.3)

        if regime_mult == 0.0:
            allow_trade = False
            reasons.append(f"{regime} regime: no new positions (blocked)")

        # ---- 2. Confidence-based adjustment (adaptive threshold) ----
        if confidence < self.MIN_CONFIDENCE:
            allow_trade = False
            reasons.append(f"Confidence {confidence:.2f} < {self.MIN_CONFIDENCE:.2f} threshold")

        confidence_mult = min(confidence / max(self.MIN_CONFIDENCE, 0.5), 1.0)

        # ---- 3. Position concentration ----
        total_exposure = sum(abs(p.get("size", 0)) for p in current_positions)
        exposure_pct = total_exposure / account_equity if account_equity > 0 else 1.0
        concentration_mult = max(0.2, 1.0 - exposure_pct / self.MAX_POSITION_PCT)

        if exposure_pct > self.MAX_POSITION_PCT:
            allow_trade = False
            reasons.append(f"Position concentration {exposure_pct:.0%} > limit {self.MAX_POSITION_PCT:.0%}")

        # ---- 4. Daily loss limit ----
        daily_loss_pct = abs(daily_pnl) / account_equity if daily_pnl < 0 and account_equity > 0 else 0.0
        if daily_loss_pct > self.DAILY_LOSS_LIMIT_PCT:
            allow_trade = False
            reasons.append(f"Daily loss {daily_loss_pct:.2%} > limit {self.DAILY_LOSS_LIMIT_PCT:.2%}")

        # ---- 5. Consecutive loss check ----
        recent_losses = sum(1 for p in current_positions if p.get("pnl", 0) < 0)
        loss_mult = 0.5 if recent_losses >= 3 else 1.0

        # ---- 6. Compute final position size ----
        base_position_pct = self.MAX_POSITION_PCT * regime_mult * confidence_mult
        base_position_pct *= concentration_mult * loss_mult
        max_position_pct = round(min(base_position_pct, self.MAX_POSITION_PCT), 4)

        # ---- 7. Stop-loss: ATR-based, regime + trend adaptive ----
        # Base SL = ATR * multiplier, adjusted by regime and trend direction
        if regime == "TRENDING_STRONG":
            atr_sl = atr_pct * 2.0  # Wider stop: ride the trend
        elif regime == "TRENDING_WEAK":
            atr_sl = atr_pct * 1.8  # Moderate in weak trends
        elif regime == "RANGING_WIDE":
            atr_sl = atr_pct * 1.5  # Default
        elif regime == "RANGING_TIGHT":
            atr_sl = atr_pct * 1.0  # Tighter in narrow ranges
        elif regime == "LOW_VOLATILITY":
            atr_sl = atr_pct * 0.8  # Very tight in quiet markets
        else:
            atr_sl = atr_pct * 1.5  # HIGH_VOLATILITY (shouldn't reach here)
        stop_loss_pct = round(max(atr_sl, 0.005), 4)
        # Cap: per-trade max loss / max leverage = max safe stop-loss
        max_sl_by_loss = self.PER_TRADE_MAX_LOSS_PCT / max(self.MAX_LEVERAGE, 1)
        stop_loss_pct = min(stop_loss_pct, max_sl_by_loss)
        stop_loss_pct = round(stop_loss_pct, 4)

        # ---- 8. Take-profit: regime-aware risk/reward ----
        if regime == "TRENDING_STRONG":
            tp_ratio = 3.0  # Let winners run in strong trends
        elif regime == "TRENDING_WEAK":
            tp_ratio = 2.5
        elif regime == "RANGING_TIGHT":
            tp_ratio = 1.5  # Take quick profits in tight ranges
        else:
            tp_ratio = 2.0
        take_profit_pct = round(stop_loss_pct * tp_ratio, 4)

        # ---- 9. Leverage selection ----
        leverage = self._select_leverage(regime, confidence, atr_pct)

        return RiskParams(
            max_position_pct=max_position_pct,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            leverage=leverage,
            allow_trade=allow_trade,
            reasons=reasons,
        )

    def _select_leverage(self, regime: str, confidence: float, atr_pct: float) -> int:
        """Select conservative leverage based on conditions."""
        # Base: 3x
        lev = 3

        # Strong trend + high confidence: can go to 5x
        if regime == "TRENDING_STRONG" and confidence > 0.7:
            lev = 5
        elif regime == "TRENDING_WEAK":
            lev = 4
        elif regime in ("RANGING_WIDE", "RANGING_TIGHT"):
            lev = 2
        elif regime == "HIGH_VOLATILITY":
            lev = 1  # No leverage effectively
        elif regime == "LOW_VOLATILITY":
            lev = 2

        # Cap by volatility: high ATR = lower leverage
        if atr_pct > 0.03:  # >3% ATR
            lev = min(lev, 2)
        elif atr_pct > 0.02:
            lev = min(lev, 3)

        return min(lev, self.MAX_LEVERAGE)


# ---------------------------------------------------------------------------
# Layer 4: Decision Arbitrator
# ---------------------------------------------------------------------------

class DecisionArbitrator:
    """Makes the final trade decision with hardcoded safety rules.

    The AI (Layers 1-2) suggests direction and confidence.
    The Risk Calculator (Layer 3) suggests position limits.
    This arbitrator applies non-negotiable safety rules and outputs
    the final action.

    Safety rules are hardcoded — the AI model CANNOT override them.
    """

    # ---- Hardcoded safety rules ----
    SAFETY_RULES = [
        "HIGH_VOLATILITY → no new positions",
        "confidence < 0.55 → hold",
        "expected max drawdown > 5% equity → hold",
        "existing losing position → no same-direction entry",
        "extreme negative funding → long only",
        "3 consecutive daily losses → stop 12 hours",
        "max position 20% equity (never all-in)",
        "max leverage 5x",
    ]

    def __init__(self, risk_calculator: RiskCalculator):
        self.risk = risk_calculator

    def decide(
        self,
        account_equity: float,
        current_positions: list[dict],
        regime: str,
        expected_return: float,
        confidence: float,
        max_drawdown: float,
        atr_pct: float,
        funding_signal: float = 0.0,
        daily_pnl: float = 0.0,
        consecutive_losses: int = 0,
        adaptive_confidence: float | None = None,
        adaptive_position_scalar: float | None = None,
    ) -> Decision:
        """Make the final trade decision.

        Args:
            ... (existing args)
            adaptive_confidence: SelfOptimizer's adaptive confidence threshold (overrides 0.55)
            adaptive_position_scalar: SelfOptimizer's adaptive position size scalar (0.3-1.0)

        Returns:
            Decision with action and parameters.
        """
        conf_threshold = adaptive_confidence if adaptive_confidence is not None else 0.55
        pos_scalar = adaptive_position_scalar if adaptive_position_scalar is not None else 1.0

        # ---- Rule 1: Dangerous regimes → no new trades ----
        # RANGING markets empirically cause -55% to -90% losses (test_report_regime.docx)
        if regime in ("HIGH_VOLATILITY", "RANGING_TIGHT", "RANGING_WIDE"):
            return self._hold(f"{regime} regime: no trade zone (trending only)")

        # ---- Rule 2: Low confidence → hold (adaptive threshold) ----
        if confidence < conf_threshold:
            return self._hold(f"Confidence {confidence:.2f} below adaptive threshold {conf_threshold:.2f}")

        # ---- RiskCalculator: pass adaptive confidence threshold ----
        # Override the risk calculator's internal confidence floor
        self.risk.MIN_CONFIDENCE = conf_threshold

        # ---- Rule 3: Drawdown risk too high → hold ----
        max_loss = abs(max_drawdown) * account_equity
        if max_loss > account_equity * 0.05:
            return self._hold(f"Max drawdown risk ${max_loss:.0f} > 5% equity")

        # ---- Rule 4: No same-direction entry if already losing ----
        for pos in current_positions:
            if pos.get("pnl", 0) < 0:
                if (expected_return > 0 and pos.get("side") == "long") or (
                    expected_return < 0 and pos.get("side") == "short"
                ):
                    return self._hold(
                        f"Existing losing {pos['side']} position on {pos['pair']}"
                    )

        # ---- Rule 5: Extreme funding → directional bias ----
        MIN_SIGNAL = 0.002
        if abs(expected_return) < MIN_SIGNAL:
            return self._hold(f"Signal {expected_return:.5f} below noise floor {MIN_SIGNAL}")

        direction = "long" if expected_return > 0 else "short"
        if funding_signal < -2.0 and direction == "short":
            return self._hold("Extreme negative funding: long only, rejecting short")
        if funding_signal > 2.0 and direction == "long":
            return self._hold("Extreme positive funding: rejecting long")

        # ---- Rule 6: Consecutive losses → stop ----
        if consecutive_losses >= 3:
            return Decision(
                action=Action.STOP,
                position_size_pct=0.0,
                leverage=1,
                stop_loss_pct=0.0,
                take_profit_pct=0.0,
                confidence=confidence,
                expected_return=expected_return,
                reason=f"{consecutive_losses} consecutive losses: stop 12 hours",
            )

        # ---- Rule 7: No clear direction handled by MIN_SIGNAL above ----

        # ---- Calculate risk parameters ----
        risk_params = self.risk.calculate(
            account_equity=account_equity,
            current_positions=current_positions,
            regime=regime,
            confidence=confidence,
            atr_pct=atr_pct,
            daily_pnl=daily_pnl,
        )

        if not risk_params.allow_trade:
            return self._hold(f"Risk check failed: {'; '.join(risk_params.reasons)}")

        # ---- Final decision ----
        action = Action.LONG if direction == "long" else Action.SHORT

        # Scale position by expected return magnitude AND adaptive scalar
        return_magnitude = min(abs(expected_return) / 0.02, 1.0)
        position_size = round(risk_params.max_position_pct * return_magnitude * pos_scalar, 4)

        return Decision(
            action=action,
            position_size_pct=position_size,
            leverage=risk_params.leverage,
            stop_loss_pct=risk_params.stop_loss_pct,
            take_profit_pct=risk_params.take_profit_pct,
            confidence=confidence,
            expected_return=expected_return,
            reason=(
                f"{action.value.upper()} {regime} | "
                f"expected_return={expected_return:.4f} | "
                f"confidence={confidence:.2f} | "
                f"size={position_size:.2%} | "
                f"SL={risk_params.stop_loss_pct:.2%} | "
                f"TP={risk_params.take_profit_pct:.2%} | "
                f"lev={risk_params.leverage}x"
            ),
        )

    def _hold(self, reason: str) -> Decision:
        return Decision(
            action=Action.HOLD,
            position_size_pct=0.0,
            leverage=1,
            stop_loss_pct=0.0,
            take_profit_pct=0.0,
            confidence=0.0,
            reason=reason,
        )
