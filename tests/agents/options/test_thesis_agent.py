"""Tests for agents/options/thesis_agent.py"""

import pytest
from unittest.mock import patch, MagicMock
from agents.options.thesis_agent import (
    _build_thesis_prompt,
    _parse_thesis_response,
    generate_thesis,
)
from agents.trader_config import ALEX, JORDAN


class TestBuildThesisPrompt:

    def test_contains_ticker(self):
        prompt = _build_thesis_prompt(
            "AAPL",
            {"sentiment": "positive", "summary": "Strong iPhone sales"},
            {"rsi": 55, "macd": 0.5, "trend": "up", "signals": []},
            ALEX,
        )
        assert "AAPL" in prompt

    def test_contains_trader_name(self):
        prompt = _build_thesis_prompt(
            "MSFT",
            {"sentiment": "neutral", "summary": "Mixed signals"},
            {"rsi": 50, "macd": 0.0, "trend": "sideways", "signals": []},
            ALEX,
        )
        assert ALEX.name in prompt

    def test_contains_sentiment(self):
        prompt = _build_thesis_prompt(
            "NVDA",
            {"sentiment": "negative", "summary": "Revenue miss"},
            {"rsi": 40, "macd": -0.3, "trend": "down", "signals": []},
            JORDAN,
        )
        assert "negative" in prompt

    def test_contains_direction_options(self):
        prompt = _build_thesis_prompt(
            "TSLA",
            {"sentiment": "positive", "summary": "Record deliveries"},
            {"rsi": 65, "macd": 1.2, "trend": "up", "signals": []},
            ALEX,
        )
        assert "bullish" in prompt
        assert "bearish" in prompt
        assert "neutral" in prompt


class TestParseThesisResponse:

    def test_valid_json_parsed_correctly(self):
        raw = '{"direction": "bullish", "conviction": 0.75, "timeframe": "medium", "catalyst": "earnings beat", "thesis": "Strong momentum expected."}'
        result = _parse_thesis_response(raw)
        assert result["direction"] == "bullish"
        assert result["conviction"] == 0.75
        assert result["timeframe"] == "medium"

    def test_markdown_fences_stripped(self):
        raw = '```json\n{"direction": "bearish", "conviction": 0.6, "timeframe": "short", "catalyst": "rate fears", "thesis": "Downside risk elevated."}\n```'
        result = _parse_thesis_response(raw)
        assert result["direction"] == "bearish"

    def test_invalid_direction_defaults_to_neutral(self):
        raw = '{"direction": "sideways", "conviction": 0.5, "timeframe": "medium", "catalyst": "none", "thesis": "Test."}'
        result = _parse_thesis_response(raw)
        assert result["direction"] == "neutral"

    def test_conviction_clamped_to_0_1(self):
        raw = '{"direction": "bullish", "conviction": 1.5, "timeframe": "short", "catalyst": "hype", "thesis": "Too excited."}'
        result = _parse_thesis_response(raw)
        assert result["conviction"] <= 1.0

    def test_conviction_floor_at_0(self):
        raw = '{"direction": "bearish", "conviction": -0.3, "timeframe": "long", "catalyst": "fear", "thesis": "Very bearish."}'
        result = _parse_thesis_response(raw)
        assert result["conviction"] >= 0.0

    def test_invalid_timeframe_defaults_to_medium(self):
        raw = '{"direction": "neutral", "conviction": 0.4, "timeframe": "forever", "catalyst": "nothing", "thesis": "Flat."}'
        result = _parse_thesis_response(raw)
        assert result["timeframe"] == "medium"

    def test_completely_invalid_json_returns_default(self):
        result = _parse_thesis_response("not json at all")
        assert result["direction"] == "neutral"
        assert result["conviction"] == 0.3
        assert result["timeframe"] == "medium"

    def test_empty_string_returns_default(self):
        result = _parse_thesis_response("")
        assert result["direction"] == "neutral"

    def test_all_required_keys_present_on_valid_input(self):
        raw = '{"direction": "bullish", "conviction": 0.8, "timeframe": "short", "catalyst": "breakout", "thesis": "Strong move expected."}'
        result = _parse_thesis_response(raw)
        assert set(result.keys()) == {"direction", "conviction", "timeframe", "catalyst", "thesis"}


class TestGenerateThesis:

    def test_returns_dict_with_ticker(self):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"direction": "bullish", "conviction": 0.7, "timeframe": "medium", "catalyst": "momentum", "thesis": "Bullish setup."}')]

        with patch("agents.options.thesis_agent._client") as mock_client:
            mock_client.messages.create.return_value = mock_response
            result = generate_thesis(
                "AAPL",
                {"sentiment": "positive", "summary": "Good news"},
                {"rsi": 60, "macd": 0.5, "trend": "up", "signals": []},
                ALEX,
            )

        assert result["ticker"] == "AAPL"
        assert result["direction"] in {"bullish", "bearish", "neutral"}

    def test_llm_failure_returns_safe_default(self):
        with patch("agents.options.thesis_agent._client") as mock_client:
            mock_client.messages.create.side_effect = Exception("API error")
            result = generate_thesis(
                "MSFT",
                {"sentiment": "neutral", "summary": "Mixed"},
                {"rsi": 50, "macd": 0.0, "trend": "sideways", "signals": []},
                JORDAN,
            )

        assert result["ticker"] == "MSFT"
        assert result["direction"] == "neutral"

    def test_ticker_always_in_result(self):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="not json")]

        with patch("agents.options.thesis_agent._client") as mock_client:
            mock_client.messages.create.return_value = mock_response
            result = generate_thesis(
                "NVDA",
                {"sentiment": "positive", "summary": "AI boom"},
                {"rsi": 70, "macd": 1.5, "trend": "up", "signals": []},
                ALEX,
            )

        assert result["ticker"] == "NVDA"
