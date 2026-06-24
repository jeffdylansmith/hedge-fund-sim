import pytest
from agents.trader_config import ALEX, JORDAN, CASEY, TRADERS, TraderConfig

REQUIRED_FIELDS = [
    "trader_id",
    "name",
    "personality",
    "risk_tolerance",
    "vp_threshold",
    "news_analyst_persona",
    "tech_analyst_persona",
]

class TestTraderConfigFields:
    """Ensures all three trader instances have every required field populated."""

    @pytest.mark.parametrize("trader", [ALEX, JORDAN, CASEY])
    def test_all_required_fields_present(self, trader):
        for field in REQUIRED_FIELDS:
            assert hasattr(trader, field), f"{trader.trader_id} missing field: {field}"

    @pytest.mark.parametrize("trader", [ALEX, JORDAN, CASEY])
    def test_no_empty_string_fields(self, trader):
        for field in REQUIRED_FIELDS:
            value = getattr(trader, field)
            if isinstance(value, str):
                assert value.strip() != "", f"{trader.trader_id}.{field} is empty string"

    @pytest.mark.parametrize("trader", [ALEX, JORDAN, CASEY])
    def test_vp_threshold_in_valid_range(self, trader):
        assert 0.0 < trader.vp_threshold <= 1.0, (
            f"{trader.trader_id}.vp_threshold={trader.vp_threshold} out of range (0, 1]"
        )

    def test_trader_ids_are_unique(self):
        ids = [t.trader_id for t in TRADERS]
        assert len(ids) == len(set(ids)), "Duplicate trader_ids found"

    def test_traders_list_contains_all_three(self):
        ids = {t.trader_id for t in TRADERS}
        assert ids == {"alex", "jordan", "casey"}

    def test_config_is_immutable(self):
        with pytest.raises((AttributeError, TypeError)):
            ALEX.trader_id = "hacked"


class TestRiskManagerContract:
    """
    Documents the known interface of assess_risk().
    These tests lock the return shape so we catch regressions
    if the function signature or output dict changes.
    """

    def test_assess_risk_returns_dict(self, mock_supabase, mock_llm):
        from unittest.mock import patch
        from agents.risk_manager import assess_risk

        canned = '{"risk_score": 6, "recommendation": "caution", "flags": ["high_concentration"], "rationale": "Test rationale sentence."}'

        with patch("agents.risk_manager._client", mock_llm(canned)):
            result = assess_risk(
                trade_proposal=[{"ticker": "AAPL", "action": "BUY", "shares": 10, "reasoning": "test", "confidence": 0.7}],
                positions=[{"ticker": "AAPL", "shares": 50, "avg_cost": 150.0}],
                cash=10000.0,
                news_summary={"summary": "Positive news", "sentiment": "positive"},
                tech_signals={"rsi": 55, "macd": 0.5, "trend": "up", "signals": []},
                trader_config=ALEX,
                current_prices={"AAPL": 175.0},
                persona="",
            )

        assert isinstance(result, dict), "assess_risk must return a dict"

    def test_assess_risk_return_shape(self, mock_supabase, mock_llm):
        from unittest.mock import patch
        from agents.risk_manager import assess_risk

        canned = '{"risk_score": 4, "recommendation": "proceed", "flags": [], "rationale": "All clear."}'

        with patch("agents.risk_manager._client", mock_llm(canned)):
            result = assess_risk(
                trade_proposal=[{"ticker": "MSFT", "action": "BUY", "shares": 5, "reasoning": "test", "confidence": 0.8}],
                positions=[],
                cash=50000.0,
                news_summary={"summary": "Neutral", "sentiment": "neutral"},
                tech_signals={"rsi": 50, "macd": 0.0, "trend": "sideways", "signals": []},
                trader_config=JORDAN,
                current_prices={"MSFT": 420.0},
                persona="",
            )

        required_keys = {"risk_score", "recommendation", "flags", "rationale", "concentration_pct"}
        missing = required_keys - result.keys()
        assert not missing, f"assess_risk result missing keys: {missing}"

    def test_risk_score_in_valid_range(self, mock_supabase, mock_llm):
        from unittest.mock import patch
        from agents.risk_manager import assess_risk

        canned = '{"risk_score": 8, "recommendation": "veto", "flags": ["overlimit"], "rationale": "Too risky."}'

        with patch("agents.risk_manager._client", mock_llm(canned)):
            result = assess_risk(
                trade_proposal=[{"ticker": "TSLA", "action": "BUY", "shares": 100, "reasoning": "moon", "confidence": 0.9}],
                positions=[],
                cash=5000.0,
                news_summary={"summary": "Hype", "sentiment": "positive"},
                tech_signals={"rsi": 80, "macd": 1.2, "trend": "up", "signals": []},
                trader_config=CASEY,
                current_prices={"TSLA": 250.0},
                persona="",
            )

        assert 1 <= result["risk_score"] <= 10, f"risk_score {result['risk_score']} out of range 1-10"

    def test_recommendation_is_valid_value(self, mock_supabase, mock_llm):
        from unittest.mock import patch
        from agents.risk_manager import assess_risk

        canned = '{"risk_score": 3, "recommendation": "proceed", "flags": [], "rationale": "Looks good."}'

        with patch("agents.risk_manager._client", mock_llm(canned)):
            result = assess_risk(
                trade_proposal=[{"ticker": "NVDA", "action": "SELL", "shares": 5, "reasoning": "trim", "confidence": 0.6}],
                positions=[{"ticker": "NVDA", "shares": 20, "avg_cost": 800.0}],
                cash=20000.0,
                news_summary={"summary": "Mixed", "sentiment": "neutral"},
                tech_signals={"rsi": 45, "macd": -0.2, "trend": "down", "signals": []},
                trader_config=ALEX,
                current_prices={"NVDA": 950.0},
                persona="",
            )

        assert result["recommendation"] in {"proceed", "caution", "veto"}, (
            f"Invalid recommendation value: {result['recommendation']}"
        )
