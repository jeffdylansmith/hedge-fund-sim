"""
Tests for agents/options/volatility_agent.py

Strategy:
- All external calls (yfinance, LLM) are mocked
- We test the pure math functions directly with known inputs
- We test assess_volatility() output contract (shape, types, value ranges)
- We do NOT test that yfinance returns specific values — that's their problem
"""

import pytest
from unittest.mock import patch, MagicMock
from agents.options.volatility_agent import (
    _compute_iv_rank,
    _compute_iv_percentile,
    _iv_environment,
    _strategy_recommendation,
    assess_volatility,
)


class TestIVRank:

    def test_iv_at_high_end_of_range(self):
        history = [0.20, 0.22, 0.25, 0.28, 0.30]
        rank = _compute_iv_rank(0.30, history)
        assert rank == 100.0

    def test_iv_at_low_end_of_range(self):
        history = [0.20, 0.22, 0.25, 0.28, 0.30]
        rank = _compute_iv_rank(0.20, history)
        assert rank == 0.0

    def test_iv_at_midpoint(self):
        history = [0.20, 0.30]
        rank = _compute_iv_rank(0.25, history)
        assert rank == 50.0

    def test_empty_history_returns_50(self):
        rank = _compute_iv_rank(0.25, [])
        assert rank == 50.0

    def test_flat_history_returns_50(self):
        """All same values — no range, return neutral 50."""
        rank = _compute_iv_rank(0.25, [0.25, 0.25, 0.25])
        assert rank == 50.0

    def test_result_in_0_to_100_range(self):
        history = [0.15 + i * 0.01 for i in range(30)]
        rank = _compute_iv_rank(0.22, history)
        assert 0.0 <= rank <= 100.0


class TestIVPercentile:

    def test_all_history_below_current(self):
        history = [0.20, 0.21, 0.22]
        pct = _compute_iv_percentile(0.30, history)
        assert pct == 100.0

    def test_all_history_above_current(self):
        history = [0.30, 0.31, 0.32]
        pct = _compute_iv_percentile(0.20, history)
        assert pct == 0.0

    def test_half_below_half_above(self):
        history = [0.20, 0.22, 0.26, 0.28]
        pct = _compute_iv_percentile(0.24, history)
        assert pct == 50.0

    def test_empty_history_returns_50(self):
        pct = _compute_iv_percentile(0.25, [])
        assert pct == 50.0

    def test_result_in_0_to_100_range(self):
        history = [0.15 + i * 0.005 for i in range(50)]
        pct = _compute_iv_percentile(0.22, history)
        assert 0.0 <= pct <= 100.0


class TestIVEnvironment:

    def test_high_iv_rank(self):
        assert _iv_environment(75.0) == "high"

    def test_boundary_high(self):
        assert _iv_environment(70.0) == "high"

    def test_moderate_iv_rank(self):
        assert _iv_environment(50.0) == "moderate"

    def test_boundary_moderate_low(self):
        assert _iv_environment(35.0) == "moderate"

    def test_low_iv_rank(self):
        assert _iv_environment(20.0) == "low"

    def test_boundary_low(self):
        assert _iv_environment(34.9) == "low"


class TestStrategyRecommendation:

    def test_high_iv_recommends_sell_premium(self):
        assert _strategy_recommendation("high", None) == "sell_premium"

    def test_low_iv_recommends_buy_premium(self):
        assert _strategy_recommendation("low", None) == "buy_premium"

    def test_moderate_iv_recommends_neutral(self):
        assert _strategy_recommendation("moderate", None) == "neutral"

    def test_earnings_within_5_days_overrides_all(self):
        assert _strategy_recommendation("high", 3) == "avoid_earnings"
        assert _strategy_recommendation("low", 1) == "avoid_earnings"
        assert _strategy_recommendation("moderate", 5) == "avoid_earnings"

    def test_earnings_at_6_days_does_not_override(self):
        assert _strategy_recommendation("high", 6) == "sell_premium"

    def test_earnings_none_uses_iv_environment(self):
        assert _strategy_recommendation("low", None) == "buy_premium"


class TestAssessVolatility:
    """
    Tests assess_volatility() output contract.
    All external I/O is mocked — no network calls, no LLM calls.
    """

    @pytest.fixture
    def mock_iv_data(self):
        """Standard mock: 30% current IV, moderate history, no earnings."""
        with patch("agents.options.volatility_agent._get_current_iv", return_value=0.30), \
             patch("agents.options.volatility_agent._get_iv_history",
                   return_value=[0.20 + i * 0.005 for i in range(40)]), \
             patch("agents.options.volatility_agent._get_earnings_proximity", return_value=None), \
             patch("agents.options.volatility_agent._generate_rationale",
                   return_value="IV rank is elevated, favoring premium selling strategies."), \
             patch("yfinance.Ticker") as mock_ticker:

            mock_ticker.return_value.fast_info.last_price = 150.0
            yield

    def test_returns_dict(self, mock_iv_data):
        result = assess_volatility("AAPL")
        assert isinstance(result, dict)

    def test_all_required_keys_present(self, mock_iv_data):
        result = assess_volatility("AAPL")
        required = {
            "ticker", "current_iv", "iv_rank", "iv_percentile",
            "iv_environment", "expected_move_30d", "earnings_proximity",
            "recommendation", "rationale"
        }
        assert required == result.keys()

    def test_ticker_matches_input(self, mock_iv_data):
        result = assess_volatility("NVDA")
        assert result["ticker"] == "NVDA"

    def test_current_iv_is_float(self, mock_iv_data):
        result = assess_volatility("AAPL")
        assert isinstance(result["current_iv"], float)

    def test_iv_rank_in_valid_range(self, mock_iv_data):
        result = assess_volatility("AAPL")
        assert 0.0 <= result["iv_rank"] <= 100.0

    def test_iv_percentile_in_valid_range(self, mock_iv_data):
        result = assess_volatility("AAPL")
        assert 0.0 <= result["iv_percentile"] <= 100.0

    def test_iv_environment_valid_value(self, mock_iv_data):
        result = assess_volatility("AAPL")
        assert result["iv_environment"] in {"low", "moderate", "high"}

    def test_recommendation_valid_value(self, mock_iv_data):
        result = assess_volatility("AAPL")
        assert result["recommendation"] in {
            "buy_premium", "sell_premium", "neutral", "avoid_earnings"
        }

    def test_expected_move_positive(self, mock_iv_data):
        result = assess_volatility("AAPL")
        assert result["expected_move_30d"] > 0

    def test_rationale_is_string(self, mock_iv_data):
        result = assess_volatility("AAPL")
        assert isinstance(result["rationale"], str)
        assert len(result["rationale"]) > 0

    def test_earnings_proximity_none_when_no_earnings(self, mock_iv_data):
        result = assess_volatility("AAPL")
        assert result["earnings_proximity"] is None

    def test_avoid_earnings_when_earnings_imminent(self):
        with patch("agents.options.volatility_agent._get_current_iv", return_value=0.25), \
             patch("agents.options.volatility_agent._get_iv_history", return_value=[0.20, 0.25, 0.30]), \
             patch("agents.options.volatility_agent._get_earnings_proximity", return_value=3), \
             patch("agents.options.volatility_agent._generate_rationale", return_value="Avoid earnings."), \
             patch("yfinance.Ticker") as mock_ticker:

            mock_ticker.return_value.fast_info.last_price = 150.0
            result = assess_volatility("AAPL")

        assert result["recommendation"] == "avoid_earnings"
        assert result["earnings_proximity"] == 3
