"""Tests for agents/options/strategy_agent.py"""

import pytest
from unittest.mock import patch, MagicMock
from agents.options.strategy_agent import (
    _select_strategy,
    _suggest_strikes,
    _compute_position_greeks,
    _skip_reason,
    select_strategy,
    STRATEGIES,
)
from agents.trader_config import ALEX


class TestSelectStrategy:
    """
    These tests lock the decision matrix.
    Any change to strategy selection logic will break these tests —
    that is intentional. The matrix must be explicitly updated.
    """

    def test_avoid_earnings_always_skips(self):
        assert _select_strategy("bullish", 0.9, "low", "avoid_earnings") == "skip"
        assert _select_strategy("bearish", 0.9, "high", "avoid_earnings") == "skip"
        assert _select_strategy("neutral", 0.9, "high", "avoid_earnings") == "skip"

    def test_bullish_low_iv_is_long_call(self):
        assert _select_strategy("bullish", 0.7, "low", "buy_premium") == "long_call"

    def test_bullish_moderate_iv_is_long_call(self):
        assert _select_strategy("bullish", 0.6, "moderate", "neutral") == "long_call"

    def test_bullish_high_iv_is_bull_put_spread(self):
        assert _select_strategy("bullish", 0.7, "high", "sell_premium") == "bull_put_spread"

    def test_bearish_low_iv_is_long_put(self):
        assert _select_strategy("bearish", 0.7, "low", "buy_premium") == "long_put"

    def test_bearish_high_iv_is_bear_call_spread(self):
        assert _select_strategy("bearish", 0.7, "high", "sell_premium") == "bear_call_spread"

    def test_neutral_high_iv_is_iron_condor(self):
        assert _select_strategy("neutral", 0.5, "high", "sell_premium") == "iron_condor"

    def test_neutral_moderate_iv_is_iron_condor(self):
        assert _select_strategy("neutral", 0.5, "moderate", "neutral") == "iron_condor"

    def test_neutral_low_iv_is_skip(self):
        assert _select_strategy("neutral", 0.5, "low", "buy_premium") == "skip"

    def test_result_always_in_valid_strategies(self):
        combinations = [
            ("bullish", 0.8, "high", "sell_premium"),
            ("bearish", 0.6, "low", "buy_premium"),
            ("neutral", 0.4, "moderate", "neutral"),
            ("bullish", 0.2, "low", "buy_premium"),
        ]
        for args in combinations:
            result = _select_strategy(*args)
            assert result in STRATEGIES, f"Invalid strategy {result} for args {args}"


class TestSuggestStrikes:

    def test_long_call_returns_strike_and_expiration(self):
        strikes = _suggest_strikes("long_call", 150.0, 0.25)
        assert "strike" in strikes
        assert "expiration_days" in strikes

    def test_long_put_returns_strike_and_expiration(self):
        strikes = _suggest_strikes("long_put", 150.0, 0.25)
        assert "strike" in strikes
        assert strikes["strike"] < 150.0

    def test_bull_put_spread_short_above_long(self):
        strikes = _suggest_strikes("bull_put_spread", 150.0, 0.25)
        assert strikes["short_strike"] > strikes["long_strike"]

    def test_bear_call_spread_short_below_long(self):
        strikes = _suggest_strikes("bear_call_spread", 150.0, 0.25)
        assert strikes["short_strike"] < strikes["long_strike"]

    def test_iron_condor_put_below_call(self):
        strikes = _suggest_strikes("iron_condor", 150.0, 0.25)
        assert strikes["put_short_strike"] < strikes["call_short_strike"]
        assert strikes["put_long_strike"] < strikes["put_short_strike"]
        assert strikes["call_long_strike"] > strikes["call_short_strike"]

    def test_skip_returns_empty_dict(self):
        strikes = _suggest_strikes("skip", 150.0, 0.25)
        assert strikes == {}


class TestComputePositionGreeks:

    def test_long_call_has_positive_delta(self):
        strikes = {"strike": 150.0, "expiration_days": 30}
        greeks = _compute_position_greeks("long_call", strikes, 150.0, 0.25, 30)
        assert greeks["delta"] > 0

    def test_long_put_has_negative_delta(self):
        strikes = {"strike": 150.0, "expiration_days": 30}
        greeks = _compute_position_greeks("long_put", strikes, 150.0, 0.25, 30)
        assert greeks["delta"] < 0

    def test_iron_condor_has_near_zero_delta(self):
        """Iron condor is delta-neutral by design — both sides offset."""
        strikes = _suggest_strikes("iron_condor", 150.0, 0.25)
        greeks = _compute_position_greeks("iron_condor", strikes, 150.0, 0.25, 30)
        assert abs(greeks["delta"]) < 0.15

    def test_bull_put_spread_returns_all_greek_keys(self):
        strikes = _suggest_strikes("bull_put_spread", 150.0, 0.25)
        greeks = _compute_position_greeks("bull_put_spread", strikes, 150.0, 0.25, 30)
        assert {"price", "delta", "gamma", "theta", "vega"}.issubset(greeks.keys())


class TestSelectStrategyContract:

    def test_returns_all_required_keys_on_skip(self):
        thesis = {"direction": "neutral", "conviction": 0.2, "timeframe": "medium",
                  "catalyst": "none", "thesis": "No signal."}
        volatility = {"iv_environment": "low", "iv_rank": 20.0, "current_iv": 0.18,
                      "recommendation": "buy_premium", "expected_move_30d": 8.0,
                      "earnings_proximity": None}

        result = select_strategy("AAPL", thesis, volatility, 150.0, ALEX)
        assert result["strategy"] == "skip"
        assert "skip_reason" in result
        assert result["skip_reason"] is not None

    def test_returns_all_required_keys_on_trade(self):
        thesis = {"direction": "bullish", "conviction": 0.75, "timeframe": "medium",
                  "catalyst": "momentum", "thesis": "Strong upside."}
        volatility = {"iv_environment": "low", "iv_rank": 25.0, "current_iv": 0.22,
                      "recommendation": "buy_premium", "expected_move_30d": 12.0,
                      "earnings_proximity": None}

        with patch("agents.options.strategy_agent._generate_strategy_rationale",
                   return_value="Long call selected due to bullish thesis and low IV."):
            result = select_strategy("AAPL", thesis, volatility, 150.0, ALEX)

        assert result["strategy"] == "long_call"
        assert result["ticker"] == "AAPL"
        assert isinstance(result["strikes"], dict)
        assert isinstance(result["greeks"], dict)
        assert result["skip_reason"] is None
