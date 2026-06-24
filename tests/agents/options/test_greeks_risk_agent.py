"""Tests for agents/options/greeks_risk_agent.py"""

import pytest
from unittest.mock import patch, MagicMock
from agents.options.greeks_risk_agent import (
    _get_risk_tier,
    _compute_max_loss,
    _compute_contracts,
    _check_greeks_thresholds,
    assess_options_risk,
    THRESHOLDS,
)
from agents.trader_config import ALEX, JORDAN, CASEY


class TestGetRiskTier:

    def test_jordan_is_conservative(self):
        tier = _get_risk_tier(JORDAN)
        assert tier in {"conservative", "moderate"}

    def test_casey_is_aggressive(self):
        tier = _get_risk_tier(CASEY)
        assert tier == "aggressive"

    def test_returns_valid_tier(self):
        for trader in [ALEX, JORDAN, CASEY]:
            assert _get_risk_tier(trader) in {"conservative", "moderate", "aggressive"}


class TestComputeMaxLoss:

    def test_long_call_max_loss_is_premium(self):
        greeks = {"price": 3.50, "delta": 0.45, "theta": -0.05, "vega": 0.12, "gamma": 0.02}
        loss = _compute_max_loss("long_call", {"strike": 150.0}, greeks, 1)
        assert abs(loss - 350.0) < 0.01  # $3.50 × 100 × 1 contract

    def test_long_call_scales_with_contracts(self):
        greeks = {"price": 3.50, "delta": 0.45, "theta": -0.05, "vega": 0.12, "gamma": 0.02}
        loss_1 = _compute_max_loss("long_call", {"strike": 150.0}, greeks, 1)
        loss_3 = _compute_max_loss("long_call", {"strike": 150.0}, greeks, 3)
        assert abs(loss_3 - loss_1 * 3) < 0.01

    def test_long_put_max_loss_is_premium(self):
        greeks = {"price": 2.80, "delta": -0.40, "theta": -0.04, "vega": 0.10, "gamma": 0.02}
        loss = _compute_max_loss("long_put", {"strike": 150.0}, greeks, 1)
        assert abs(loss - 280.0) < 0.01

    def test_bull_put_spread_max_loss_less_than_wing(self):
        strikes = {"short_strike": 145.0, "long_strike": 140.0}
        greeks = {"price": 1.50}  # net credit of $1.50
        loss = _compute_max_loss("bull_put_spread", strikes, greeks, 1)
        wing_width = 5.0
        max_possible = wing_width * 100
        assert loss < max_possible
        assert loss > 0

    def test_iron_condor_max_loss_uses_narrower_wing(self):
        strikes = {
            "put_long_strike": 135.0,
            "put_short_strike": 140.0,
            "call_short_strike": 160.0,
            "call_long_strike": 165.0,
        }
        greeks = {"price": 2.00}  # net credit
        loss = _compute_max_loss("iron_condor", strikes, greeks, 1)
        assert loss > 0
        assert loss <= 500.0  # max wing width × 100

    def test_skip_returns_zero(self):
        loss = _compute_max_loss("skip", {}, {}, 1)
        assert loss == 0.0


class TestComputeContracts:

    def test_returns_at_least_zero(self):
        greeks = {"price": 5.0, "delta": 0.5, "theta": -0.05, "vega": 0.15, "gamma": 0.02}
        strikes = {"strike": 150.0}
        contracts = _compute_contracts(
            "long_call", strikes, greeks,
            portfolio_value=100000.0,
            trader_config=ALEX,
            conviction=0.7,
            existing_options_exposure=0.0,
        )
        assert contracts >= 0

    def test_capped_at_10_contracts(self):
        # Very cheap option + large portfolio = would be many contracts without cap
        greeks = {"price": 0.10, "delta": 0.1, "theta": -0.001, "vega": 0.01, "gamma": 0.001}
        strikes = {"strike": 150.0}
        contracts = _compute_contracts(
            "long_call", strikes, greeks,
            portfolio_value=10_000_000.0,
            trader_config=CASEY,
            conviction=1.0,
            existing_options_exposure=0.0,
        )
        assert contracts <= 10

    def test_full_exposure_returns_zero_contracts(self):
        greeks = {"price": 5.0, "delta": 0.5, "theta": -0.05, "vega": 0.15, "gamma": 0.02}
        strikes = {"strike": 150.0}
        contracts = _compute_contracts(
            "long_call", strikes, greeks,
            portfolio_value=100000.0,
            trader_config=ALEX,
            conviction=0.7,
            existing_options_exposure=100000.0,  # already fully utilized
        )
        assert contracts == 0

    def test_higher_conviction_more_contracts(self):
        greeks = {"price": 3.0, "delta": 0.45, "theta": -0.04, "vega": 0.12, "gamma": 0.02}
        strikes = {"strike": 150.0}

        low = _compute_contracts(
            "long_call", strikes, greeks,
            portfolio_value=500000.0, trader_config=ALEX,
            conviction=0.4, existing_options_exposure=0.0,
        )
        high = _compute_contracts(
            "long_call", strikes, greeks,
            portfolio_value=500000.0, trader_config=ALEX,
            conviction=0.9, existing_options_exposure=0.0,
        )
        assert high >= low


class TestCheckGreeksThresholds:

    def test_low_delta_no_violation(self):
        greeks = {"price": 3.0, "delta": 0.20, "theta": -0.02, "vega": 0.10, "gamma": 0.01}
        violations = _check_greeks_thresholds(
            greeks, "long_call", THRESHOLDS["moderate"], contracts=1
        )
        assert len(violations) == 0

    def test_high_delta_violation(self):
        greeks = {"price": 3.0, "delta": 0.95, "theta": -0.02, "vega": 0.10, "gamma": 0.01}
        violations = _check_greeks_thresholds(
            greeks, "long_call", THRESHOLDS["conservative"], contracts=1
        )
        assert any("Delta" in v or "delta" in v.lower() for v in violations)

    def test_conservative_tighter_than_aggressive(self):
        greeks = {"price": 3.0, "delta": 0.55, "theta": -0.02, "vega": 0.10, "gamma": 0.01}
        conservative_v = _check_greeks_thresholds(
            greeks, "long_call", THRESHOLDS["conservative"], contracts=1
        )
        aggressive_v = _check_greeks_thresholds(
            greeks, "long_call", THRESHOLDS["aggressive"], contracts=1
        )
        # Conservative should flag what aggressive passes
        assert len(conservative_v) >= len(aggressive_v)


class TestAssessOptionsRisk:

    @pytest.fixture
    def standard_inputs(self):
        return {
            "ticker": "AAPL",
            "strategy": "long_call",
            "strikes": {"strike": 150.0, "expiration_days": 30},
            "greeks": {
                "price": 3.50,
                "delta": 0.45,
                "theta": -0.04,
                "vega": 0.12,
                "gamma": 0.02,
            },
            "thesis": {"direction": "bullish", "conviction": 0.70,
                       "timeframe": "medium", "catalyst": "momentum",
                       "thesis": "Strong upside."},
            "trader_config": ALEX,
            "portfolio_value": 1_000_000.0,
            "existing_options_exposure": 0.0,
            "days_to_expiration": 30,
        }

    def test_skip_strategy_always_rejected(self, standard_inputs):
        standard_inputs["strategy"] = "skip"
        with patch("agents.options.greeks_risk_agent._generate_risk_explanation",
                   return_value="Skipped."):
            result = assess_options_risk(**standard_inputs)
        assert result["decision"] == "REJECT"
        assert result["contracts"] == 0

    def test_returns_all_required_keys(self, standard_inputs):
        with patch("agents.options.greeks_risk_agent._generate_risk_explanation",
                   return_value="Approved."):
            result = assess_options_risk(**standard_inputs)
        required = {
            "ticker", "decision", "contracts", "max_loss_total",
            "max_loss_pct_portfolio", "greeks_violations", "risk_tier", "explanation"
        }
        assert required == result.keys()

    def test_decision_is_approve_or_reject(self, standard_inputs):
        with patch("agents.options.greeks_risk_agent._generate_risk_explanation",
                   return_value="Decision made."):
            result = assess_options_risk(**standard_inputs)
        assert result["decision"] in {"APPROVE", "REJECT"}

    def test_approved_position_has_nonzero_contracts(self, standard_inputs):
        with patch("agents.options.greeks_risk_agent._generate_risk_explanation",
                   return_value="Approved."):
            result = assess_options_risk(**standard_inputs)
        if result["decision"] == "APPROVE":
            assert result["contracts"] > 0

    def test_rejected_position_has_zero_contracts(self, standard_inputs):
        # Exceed the risk cap
        standard_inputs["existing_options_exposure"] = 1_000_000.0
        with patch("agents.options.greeks_risk_agent._generate_risk_explanation",
                   return_value="Rejected."):
            result = assess_options_risk(**standard_inputs)
        assert result["decision"] == "REJECT"
        assert result["contracts"] == 0

    def test_max_loss_never_exceeds_portfolio(self, standard_inputs):
        with patch("agents.options.greeks_risk_agent._generate_risk_explanation",
                   return_value="Fine."):
            result = assess_options_risk(**standard_inputs)
        assert result["max_loss_total"] <= standard_inputs["portfolio_value"]

    def test_ticker_matches_input(self, standard_inputs):
        with patch("agents.options.greeks_risk_agent._generate_risk_explanation",
                   return_value="Fine."):
            result = assess_options_risk(**standard_inputs)
        assert result["ticker"] == "AAPL"

    def test_casey_allows_more_risk_than_jordan(self):
        """Casey's higher cap should result in more contracts than Jordan on same position."""
        base = {
            "ticker": "NVDA",
            "strategy": "long_call",
            "strikes": {"strike": 500.0, "expiration_days": 30},
            "greeks": {"price": 8.0, "delta": 0.50, "theta": -0.08,
                       "vega": 0.20, "gamma": 0.01},
            "thesis": {"direction": "bullish", "conviction": 0.80,
                       "timeframe": "short", "catalyst": "AI momentum",
                       "thesis": "Strong upside."},
            "portfolio_value": 1_000_000.0,
            "existing_options_exposure": 0.0,
            "days_to_expiration": 30,
        }

        with patch("agents.options.greeks_risk_agent._generate_risk_explanation",
                   return_value="Fine."):
            casey_result = assess_options_risk(**{**base, "trader_config": CASEY})
            jordan_result = assess_options_risk(**{**base, "trader_config": JORDAN})

        assert casey_result["contracts"] >= jordan_result["contracts"]
