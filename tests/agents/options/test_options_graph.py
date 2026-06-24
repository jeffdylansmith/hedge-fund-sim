"""
Tests for agents/options/options_graph.py

Strategy:
- All four agent functions are mocked
- We test state flow, conditional routing, and error handling
- We never make network calls or LLM calls in tests
"""

import pytest
from unittest.mock import patch, MagicMock
from agents.options.options_graph import (
    volatility_node,
    thesis_node,
    strategy_node,
    risk_gate_node,
    should_continue_after_volatility,
    should_continue_after_thesis,
    should_continue_after_strategy,
    run_options_pipeline,
)
from agents.trader_config import ALEX


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def base_state():
    return {
        "ticker": "AAPL",
        "trader_config": ALEX,
        "portfolio_value": 1_000_000.0,
        "existing_options_exposure": 0.0,
        "news_summary": {"sentiment": "positive", "summary": "Good news"},
        "tech_signals": {"rsi": 60, "macd": 0.5, "trend": "up", "signals": []},
        "persona": "",
        "volatility": None,
        "thesis": None,
        "strategy": None,
        "risk_assessment": None,
        "errors": [],
        "should_skip": False,
    }


@pytest.fixture
def mock_volatility():
    return {
        "ticker": "AAPL",
        "current_iv": 0.28,
        "iv_rank": 45.0,
        "iv_percentile": 52.0,
        "iv_environment": "moderate",
        "expected_move_30d": 14.20,
        "earnings_proximity": None,
        "recommendation": "neutral",
        "rationale": "IV is moderate.",
    }


@pytest.fixture
def mock_thesis():
    return {
        "ticker": "AAPL",
        "direction": "bullish",
        "conviction": 0.72,
        "timeframe": "medium",
        "catalyst": "momentum",
        "thesis": "Strong upside expected.",
    }


@pytest.fixture
def mock_strategy():
    return {
        "ticker": "AAPL",
        "strategy": "long_call",
        "strikes": {"strike": 152.0, "expiration_days": 30},
        "greeks": {"price": 3.50, "delta": 0.48, "gamma": 0.02,
                   "theta": -0.04, "vega": 0.12},
        "rationale": "Long call on bullish thesis with moderate IV.",
        "skip_reason": None,
    }


@pytest.fixture
def mock_risk_approved():
    return {
        "ticker": "AAPL",
        "decision": "APPROVE",
        "contracts": 2,
        "max_loss_total": 700.0,
        "max_loss_pct_portfolio": 0.0007,
        "greeks_violations": [],
        "risk_tier": "moderate",
        "explanation": "Position approved within risk parameters.",
    }


# ---------------------------------------------------------------------------
# Node unit tests
# ---------------------------------------------------------------------------

class TestVolatilityNode:

    def test_sets_volatility_on_success(self, base_state, mock_volatility):
        with patch("agents.options.options_graph.assess_volatility",
                   return_value=mock_volatility):
            result = volatility_node(base_state)
        assert result["volatility"] == mock_volatility

    def test_no_skip_when_recommendation_is_not_earnings(self, base_state, mock_volatility):
        with patch("agents.options.options_graph.assess_volatility",
                   return_value=mock_volatility):
            result = volatility_node(base_state)
        assert result["should_skip"] is False

    def test_skips_on_avoid_earnings(self, base_state, mock_volatility):
        mock_volatility["recommendation"] = "avoid_earnings"
        with patch("agents.options.options_graph.assess_volatility",
                   return_value=mock_volatility):
            result = volatility_node(base_state)
        assert result["should_skip"] is True

    def test_sets_should_skip_on_exception(self, base_state):
        with patch("agents.options.options_graph.assess_volatility",
                   side_effect=Exception("network error")):
            result = volatility_node(base_state)
        assert result["should_skip"] is True
        assert result["volatility"] is None
        assert len(result["errors"]) > 0


class TestThesisNode:

    def test_skips_if_should_skip_true(self, base_state):
        base_state["should_skip"] = True
        result = thesis_node(base_state)
        assert result["thesis"] is None

    def test_sets_thesis_on_success(self, base_state, mock_thesis):
        with patch("agents.options.options_graph.generate_thesis",
                   return_value=mock_thesis):
            result = thesis_node(base_state)
        assert result["thesis"] == mock_thesis

    def test_sets_skip_on_exception(self, base_state):
        with patch("agents.options.options_graph.generate_thesis",
                   side_effect=Exception("LLM error")):
            result = thesis_node(base_state)
        assert result["should_skip"] is True
        assert result["thesis"] is None


class TestStrategyNode:

    def test_skips_if_should_skip_true(self, base_state):
        base_state["should_skip"] = True
        result = strategy_node(base_state)
        assert result["strategy"] is None

    def test_skips_if_no_thesis(self, base_state):
        base_state["thesis"] = None
        result = strategy_node(base_state)
        assert result["strategy"] is None

    def test_sets_skip_when_strategy_is_skip(
        self, base_state, mock_volatility, mock_thesis
    ):
        base_state["volatility"] = mock_volatility
        base_state["thesis"] = mock_thesis
        skip_result = {"strategy": "skip", "strikes": {}, "greeks": {},
                       "rationale": "No play.", "skip_reason": "Low IV neutral."}
        with patch("agents.options.options_graph.select_strategy",
                   return_value=skip_result), \
             patch("yfinance.Ticker") as mock_yf:
            mock_yf.return_value.fast_info.last_price = 150.0
            result = strategy_node(base_state)
        assert result["should_skip"] is True

    def test_sets_strategy_on_success(
        self, base_state, mock_volatility, mock_thesis, mock_strategy
    ):
        base_state["volatility"] = mock_volatility
        base_state["thesis"] = mock_thesis
        with patch("agents.options.options_graph.select_strategy",
                   return_value=mock_strategy), \
             patch("yfinance.Ticker") as mock_yf:
            mock_yf.return_value.fast_info.last_price = 150.0
            result = strategy_node(base_state)
        assert result["strategy"] == mock_strategy


class TestRiskGateNode:

    def test_rejects_when_should_skip(self, base_state):
        base_state["should_skip"] = True
        result = risk_gate_node(base_state)
        assert result["risk_assessment"]["decision"] == "REJECT"

    def test_rejects_when_no_strategy(self, base_state):
        base_state["strategy"] = None
        result = risk_gate_node(base_state)
        assert result["risk_assessment"]["decision"] == "REJECT"

    def test_passes_through_risk_assessment(
        self, base_state, mock_thesis, mock_strategy, mock_risk_approved
    ):
        base_state["thesis"] = mock_thesis
        base_state["strategy"] = mock_strategy
        with patch("agents.options.options_graph.assess_options_risk",
                   return_value=mock_risk_approved):
            result = risk_gate_node(base_state)
        assert result["risk_assessment"]["decision"] == "APPROVE"
        assert result["risk_assessment"]["contracts"] == 2


# ---------------------------------------------------------------------------
# Routing tests
# ---------------------------------------------------------------------------

class TestRouting:

    def test_continue_after_volatility_when_not_skip(self, base_state):
        base_state["should_skip"] = False
        assert should_continue_after_volatility(base_state) == "continue"

    def test_skip_after_volatility_when_skip(self, base_state):
        base_state["should_skip"] = True
        assert should_continue_after_volatility(base_state) == "skip"

    def test_continue_after_thesis_when_thesis_present(self, base_state, mock_thesis):
        base_state["should_skip"] = False
        base_state["thesis"] = mock_thesis
        assert should_continue_after_thesis(base_state) == "continue"

    def test_skip_after_thesis_when_no_thesis(self, base_state):
        base_state["thesis"] = None
        assert should_continue_after_thesis(base_state) == "skip"

    def test_continue_after_strategy_when_strategy_present(
        self, base_state, mock_strategy
    ):
        base_state["should_skip"] = False
        base_state["strategy"] = mock_strategy
        assert should_continue_after_strategy(base_state) == "continue"

    def test_skip_after_strategy_when_no_strategy(self, base_state):
        base_state["strategy"] = None
        assert should_continue_after_strategy(base_state) == "skip"


# ---------------------------------------------------------------------------
# Pipeline integration test (all mocked)
# ---------------------------------------------------------------------------

class TestRunOptionsPipeline:

    def test_full_pipeline_returns_state_dict(
        self, mock_volatility, mock_thesis, mock_strategy, mock_risk_approved
    ):
        with patch("agents.options.options_graph.assess_volatility",
                   return_value=mock_volatility), \
             patch("agents.options.options_graph.generate_thesis",
                   return_value=mock_thesis), \
             patch("agents.options.options_graph.select_strategy",
                   return_value=mock_strategy), \
             patch("agents.options.options_graph.assess_options_risk",
                   return_value=mock_risk_approved), \
             patch("yfinance.Ticker") as mock_yf:

            mock_yf.return_value.fast_info.last_price = 150.0

            result = run_options_pipeline(
                ticker="AAPL",
                trader_config=ALEX,
                portfolio_value=1_000_000.0,
                news_summary={"sentiment": "positive", "summary": "Good"},
                tech_signals={"rsi": 60, "macd": 0.5, "trend": "up", "signals": []},
            )

        assert result["risk_assessment"]["decision"] == "APPROVE"
        assert result["ticker"] == "AAPL"
        assert result["volatility"] is not None
        assert result["thesis"] is not None
        assert result["strategy"] is not None

    def test_pipeline_handles_volatility_failure_gracefully(self):
        with patch("agents.options.options_graph.assess_volatility",
                   side_effect=Exception("yfinance down")):
            result = run_options_pipeline(
                ticker="AAPL",
                trader_config=ALEX,
                portfolio_value=1_000_000.0,
                news_summary={},
                tech_signals={},
            )

        assert result["risk_assessment"]["decision"] == "REJECT"
        assert len(result["errors"]) > 0

    def test_pipeline_state_has_all_keys(
        self, mock_volatility, mock_thesis, mock_strategy, mock_risk_approved
    ):
        with patch("agents.options.options_graph.assess_volatility",
                   return_value=mock_volatility), \
             patch("agents.options.options_graph.generate_thesis",
                   return_value=mock_thesis), \
             patch("agents.options.options_graph.select_strategy",
                   return_value=mock_strategy), \
             patch("agents.options.options_graph.assess_options_risk",
                   return_value=mock_risk_approved), \
             patch("yfinance.Ticker") as mock_yf:

            mock_yf.return_value.fast_info.last_price = 150.0
            result = run_options_pipeline(
                ticker="NVDA",
                trader_config=ALEX,
                portfolio_value=1_000_000.0,
                news_summary={},
                tech_signals={},
            )

        expected_keys = {
            "ticker", "trader_config", "portfolio_value",
            "existing_options_exposure", "news_summary", "tech_signals",
            "persona", "volatility", "thesis", "strategy",
            "risk_assessment", "errors", "should_skip"
        }
        assert expected_keys.issubset(result.keys())
