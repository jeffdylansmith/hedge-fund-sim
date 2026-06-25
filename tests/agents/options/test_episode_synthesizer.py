"""Tests for agents/options/episode_synthesizer.py"""

import pytest
from unittest.mock import patch, MagicMock
from agents.options.episode_synthesizer import (
    _build_episode_narrative,
    _describe_delta,
    _describe_theta,
    _describe_vega,
    synthesize_episode,
    synthesize_batch,
)
from datetime import date


@pytest.fixture
def sample_position():
    return {
        "id": "test-position-uuid-001",
        "trader_id": "alex",
        "ticker": "AAPL",
        "strategy": "bull_put_spread",
        "contracts": 2,
        "entry_delta": 0.28,
        "entry_theta": 0.035,
        "entry_vega": -0.09,
        "entry_price": -1.50,
        "net_credit_debit": 1.50,
        "max_loss_total": 700.0,
        "realized_pnl": 180.0,
        "close_reason": "profit_target",
        "opened_at": "2025-11-01T10:30:00",
        "closed_at": "2025-11-18T15:45:00",
        "expiration_date": "2025-11-21",
    }


class TestBuildEpisodeNarrative:

    def test_contains_ticker(self, sample_position):
        narrative = _build_episode_narrative(sample_position)
        assert "AAPL" in narrative

    def test_contains_trader_id(self, sample_position):
        narrative = _build_episode_narrative(sample_position)
        assert "alex" in narrative

    def test_contains_strategy(self, sample_position):
        narrative = _build_episode_narrative(sample_position)
        assert "bull put spread" in narrative

    def test_contains_outcome(self, sample_position):
        narrative = _build_episode_narrative(sample_position)
        assert "profitable" in narrative or "180" in narrative

    def test_contains_close_reason(self, sample_position):
        narrative = _build_episode_narrative(sample_position)
        assert "profit target" in narrative

    def test_narrative_is_substantial(self, sample_position):
        """Narrative must have enough text to embed meaningfully."""
        narrative = _build_episode_narrative(sample_position)
        assert len(narrative) > 200

    def test_losing_trade_described_correctly(self, sample_position):
        sample_position["realized_pnl"] = -350.0
        narrative = _build_episode_narrative(sample_position)
        assert "losing" in narrative

    def test_no_raw_json_field_names(self, sample_position):
        """
        Narrative should not contain raw field names like 'entry_delta'.
        The whole point is semantic richness, not structured data.
        """
        narrative = _build_episode_narrative(sample_position)
        assert "entry_delta" not in narrative
        assert "net_credit_debit" not in narrative

    def test_credit_strategy_described_correctly(self, sample_position):
        narrative = _build_episode_narrative(sample_position)
        assert "credit" in narrative or "premium" in narrative

    def test_debit_strategy_described_correctly(self, sample_position):
        sample_position["net_credit_debit"] = -3.50
        sample_position["strategy"] = "long_call"
        narrative = _build_episode_narrative(sample_position)
        assert "paid" in narrative or "debit" in narrative


class TestDescribeDelta:

    def test_positive_delta_is_bullish(self):
        desc = _describe_delta(0.45, "long_call")
        assert "bullish" in desc

    def test_negative_delta_is_bearish(self):
        desc = _describe_delta(-0.40, "long_put")
        assert "bearish" in desc

    def test_high_delta_is_strong(self):
        desc = _describe_delta(0.75, "long_call")
        assert "strong" in desc

    def test_low_delta_is_low(self):
        desc = _describe_delta(0.20, "bull_put_spread")
        assert "low" in desc

    def test_none_delta_returns_fallback(self):
        desc = _describe_delta(None, "iron_condor")
        assert "not recorded" in desc


class TestDescribeTheta:

    def test_positive_theta_is_favorable(self):
        desc = _describe_theta(0.04)
        assert "favor" in desc or "collected" in desc

    def test_negative_theta_is_unfavorable(self):
        desc = _describe_theta(-0.05)
        assert "against" in desc or "lost" in desc

    def test_none_theta_returns_fallback(self):
        desc = _describe_theta(None)
        assert "not recorded" in desc


class TestDescribeVega:

    def test_positive_vega_benefits_from_rising_iv(self):
        desc = _describe_vega(0.12)
        assert "rising" in desc or "benefit" in desc

    def test_negative_vega_benefits_from_falling_iv(self):
        desc = _describe_vega(-0.08)
        assert "falling" in desc or "crush" in desc

    def test_none_vega_returns_fallback(self):
        desc = _describe_vega(None)
        assert "not recorded" in desc


class TestSynthesizeEpisode:

    def test_success_with_embedding(self, sample_position):
        mock_embedding = [0.1] * 1536

        with patch("agents.options.episode_synthesizer._embed_text",
                   return_value=mock_embedding), \
             patch("agents.options.episode_synthesizer._store_episode",
                   return_value="new-doc-uuid"):
            result = synthesize_episode(sample_position)

        assert result["success"] is True
        assert result["document_id"] == "new-doc-uuid"
        assert result["embedding_succeeded"] is True
        assert result["narrative_length"] > 0

    def test_succeeds_without_embedding(self, sample_position):
        """
        Episode should still store if embedding fails.
        Embedding can be backfilled later.
        """
        with patch("agents.options.episode_synthesizer._embed_text",
                   return_value=None), \
             patch("agents.options.episode_synthesizer._store_episode",
                   return_value="new-doc-uuid"):
            result = synthesize_episode(sample_position)

        assert result["success"] is True
        assert result["embedding_succeeded"] is False

    def test_returns_failure_on_storage_error(self, sample_position):
        with patch("agents.options.episode_synthesizer._embed_text",
                   return_value=[0.1] * 1536), \
             patch("agents.options.episode_synthesizer._store_episode",
                   return_value=None):
            result = synthesize_episode(sample_position)

        assert result["success"] is False

    def test_result_has_all_keys(self, sample_position):
        with patch("agents.options.episode_synthesizer._embed_text",
                   return_value=None), \
             patch("agents.options.episode_synthesizer._store_episode",
                   return_value="uuid"):
            result = synthesize_episode(sample_position)

        assert set(result.keys()) == {
            "success", "document_id", "narrative_length",
            "embedding_succeeded", "error"
        }

    def test_exception_returns_failure_dict(self, sample_position):
        with patch("agents.options.episode_synthesizer._build_episode_narrative",
                   side_effect=Exception("synthesis error")):
            result = synthesize_episode(sample_position)

        assert result["success"] is False
        assert result["error"] is not None


class TestSynthesizeBatch:

    def test_empty_list_returns_empty(self):
        result = synthesize_batch([])
        assert result == []

    def test_position_not_found_returns_failure(self):
        mock_result = MagicMock()
        mock_result.data = None

        with patch("agents.options.episode_synthesizer.supabase") as mock_db:
            mock_db.table.return_value.select.return_value \
                .eq.return_value.eq.return_value \
                .single.return_value.execute.return_value = mock_result
            results = synthesize_batch(["nonexistent-uuid"])

        assert len(results) == 1
        assert results[0]["success"] is False
