"""Tests for Risk Calculator (Layer 3) & Decision Arbitrator (Layer 4)."""

from engine.decision_arbitrator import (
    Action,
    Decision,
    DecisionArbitrator,
    RiskCalculator,
    RiskParams,
)


class TestRiskCalculator:
    """Layer 3: Risk parameter calculation."""

    def test_normal_market_allows_trade(self):
        """In trending market with good confidence, trades allowed."""
        rc = RiskCalculator()
        params = rc.calculate(
            account_equity=5000.0,
            current_positions=[],
            regime="TRENDING_STRONG",
            confidence=0.75,
            atr_pct=0.015,
        )
        assert params.allow_trade is True
        assert params.max_position_pct > 0
        assert params.stop_loss_pct > 0
        assert params.take_profit_pct > params.stop_loss_pct
        assert params.leverage >= 1

    def test_high_volatility_blocks_trade(self):
        """HIGH_VOLATILITY regime must block new trades."""
        rc = RiskCalculator()
        params = rc.calculate(
            account_equity=5000.0,
            current_positions=[],
            regime="HIGH_VOLATILITY",
            confidence=0.8,
            atr_pct=0.02,
        )
        assert params.allow_trade is False
        assert "HIGH_VOLATILITY" in params.reasons[0]

    def test_low_confidence_blocks_trade(self):
        """Confidence below 0.55 must block trades."""
        rc = RiskCalculator()
        params = rc.calculate(
            account_equity=5000.0,
            current_positions=[],
            regime="TRENDING_WEAK",
            confidence=0.40,
            atr_pct=0.015,
        )
        assert params.allow_trade is False

    def test_concentration_check(self):
        """If already at max exposure, block new trades."""
        rc = RiskCalculator(max_position_pct=0.20)
        params = rc.calculate(
            account_equity=5000.0,
            current_positions=[
                {"pair": "BTC/USDT:USDT", "side": "long", "size": 1200.0, "pnl": 50.0}
            ],
            regime="TRENDING_STRONG",
            confidence=0.75,
            atr_pct=0.015,
        )
        # 1200/5000 = 24% > 20% cap → blocked
        assert params.allow_trade is False

    def test_daily_loss_limit(self):
        """Daily loss exceeding limit must block trades."""
        rc = RiskCalculator(daily_loss_limit_pct=0.05)
        params = rc.calculate(
            account_equity=5000.0,
            current_positions=[],
            regime="TRENDING_STRONG",
            confidence=0.75,
            atr_pct=0.015,
            daily_pnl=-300.0,  # 6% loss
        )
        assert params.allow_trade is False

    def test_leverage_bounded(self):
        """Leverage must never exceed configured max."""
        rc = RiskCalculator(max_leverage=5)
        for regime in ["TRENDING_STRONG", "TRENDING_WEAK", "RANGING_WIDE", "HIGH_VOLATILITY"]:
            params = rc.calculate(
                account_equity=5000.0,
                current_positions=[],
                regime=regime,
                confidence=0.7,
                atr_pct=0.01,
            )
            assert params.leverage <= 5, f"{regime}: leverage {params.leverage} > 5"

    def test_stop_loss_at_least_half_pct(self):
        """Stop loss must be at least 0.5%."""
        rc = RiskCalculator()
        params = rc.calculate(
            account_equity=5000.0,
            current_positions=[],
            regime="LOW_VOLATILITY",
            confidence=0.6,
            atr_pct=0.001,  # Very small ATR
        )
        assert params.stop_loss_pct >= 0.005

    def test_take_profit_always_larger_than_stop_loss(self):
        """Risk:reward must be positive."""
        rc = RiskCalculator()
        params = rc.calculate(
            account_equity=5000.0,
            current_positions=[],
            regime="TRENDING_STRONG",
            confidence=0.7,
            atr_pct=0.02,
        )
        assert params.take_profit_pct > params.stop_loss_pct


class TestDecisionArbitrator:
    """Layer 4: Final decision with safety rules."""

    def make_arbitrator(self) -> DecisionArbitrator:
        return DecisionArbitrator(RiskCalculator())

    def test_strong_signal_produces_long(self):
        """Clear bullish signal in trending market → LONG."""
        arb = self.make_arbitrator()
        decision = arb.decide(
            account_equity=5000.0,
            current_positions=[],
            regime="TRENDING_STRONG",
            expected_return=0.015,
            confidence=0.75,
            max_drawdown=-0.005,
            atr_pct=0.015,
        )
        assert decision.action == Action.LONG
        assert decision.position_size_pct > 0

    def test_strong_signal_produces_short(self):
        """Clear bearish signal → SHORT."""
        arb = self.make_arbitrator()
        decision = arb.decide(
            account_equity=5000.0,
            current_positions=[],
            regime="TRENDING_STRONG",
            expected_return=-0.02,
            confidence=0.80,
            max_drawdown=-0.008,
            atr_pct=0.015,
        )
        assert decision.action == Action.SHORT

    def test_high_volatility_blocks_all(self):
        """Rule 1: HIGH_VOLATILITY → HOLD regardless of signal."""
        arb = self.make_arbitrator()
        decision = arb.decide(
            account_equity=5000.0,
            current_positions=[],
            regime="HIGH_VOLATILITY",
            expected_return=0.05,
            confidence=0.9,
            max_drawdown=-0.01,
            atr_pct=0.03,
        )
        assert decision.action == Action.HOLD

    def test_low_confidence_blocks(self):
        """Rule 2: confidence < 0.55 → HOLD."""
        arb = self.make_arbitrator()
        decision = arb.decide(
            account_equity=5000.0,
            current_positions=[],
            regime="TRENDING_STRONG",
            expected_return=0.03,
            confidence=0.30,
            max_drawdown=-0.005,
            atr_pct=0.015,
        )
        assert decision.action == Action.HOLD

    def test_large_drawdown_risk_blocks(self):
        """Rule 3: expected max drawdown > 5% equity → HOLD."""
        arb = self.make_arbitrator()
        decision = arb.decide(
            account_equity=5000.0,
            current_positions=[],
            regime="TRENDING_STRONG",
            expected_return=0.01,
            confidence=0.8,
            max_drawdown=-0.08,  # 8% drawdown risk
            atr_pct=0.015,
        )
        assert decision.action == Action.HOLD

    def test_no_same_direction_entry_with_losing_position(self):
        """Rule 4: don't add to losing position."""
        arb = self.make_arbitrator()
        decision = arb.decide(
            account_equity=5000.0,
            current_positions=[
                {"pair": "BTC/USDT:USDT", "side": "long", "size": 500.0, "pnl": -80.0}
            ],
            regime="TRENDING_STRONG",
            expected_return=0.02,  # Long signal but already have losing long
            confidence=0.75,
            max_drawdown=-0.003,
            atr_pct=0.015,
        )
        assert decision.action == Action.HOLD

    def test_consecutive_losses_stop(self):
        """Rule 6: 3+ consecutive losses → STOP."""
        arb = self.make_arbitrator()
        decision = arb.decide(
            account_equity=5000.0,
            current_positions=[],
            regime="TRENDING_STRONG",
            expected_return=0.02,
            confidence=0.8,
            max_drawdown=-0.003,
            atr_pct=0.015,
            consecutive_losses=3,
        )
        assert decision.action == Action.STOP

    def test_extreme_funding_blocks_wrong_direction(self):
        """Rule 5: extreme neg funding → no short; extreme pos → no long."""
        arb = self.make_arbitrator()

        # Negative funding → bullish bias, reject short
        d1 = arb.decide(
            account_equity=5000.0,
            current_positions=[],
            regime="TRENDING_STRONG",
            expected_return=-0.015,
            confidence=0.7,
            max_drawdown=-0.003,
            atr_pct=0.015,
            funding_signal=-2.5,  # Extreme negative = too many shorts
        )
        assert d1.action == Action.HOLD, f"Expected HOLD, got {d1.action}"

        # Positive funding → bearish bias, reject long
        d2 = arb.decide(
            account_equity=5000.0,
            current_positions=[],
            regime="TRENDING_STRONG",
            expected_return=0.015,
            confidence=0.7,
            max_drawdown=-0.003,
            atr_pct=0.015,
            funding_signal=2.5,  # Extreme positive = too many longs
        )
        assert d2.action == Action.HOLD, f"Expected HOLD, got {d2.action}"

    def test_decision_reason_is_descriptive(self):
        """Every decision must have a human-readable reason."""
        arb = self.make_arbitrator()
        decision = arb.decide(
            account_equity=5000.0,
            current_positions=[],
            regime="TRENDING_STRONG",
            expected_return=0.015,
            confidence=0.75,
            max_drawdown=-0.003,
            atr_pct=0.015,
        )
        assert len(decision.reason) > 20
        assert "LONG" in decision.reason

    def test_no_signal_produces_hold(self):
        """Expected return ~ 0 should produce HOLD."""
        arb = self.make_arbitrator()
        decision = arb.decide(
            account_equity=5000.0,
            current_positions=[],
            regime="RANGING_TIGHT",
            expected_return=0.00001,
            confidence=0.6,
            max_drawdown=-0.001,
            atr_pct=0.01,
        )
        # Near-zero return → no direction
        assert decision.action == Action.HOLD
