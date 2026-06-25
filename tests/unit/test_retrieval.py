"""Tests for utils/retrieval.py"""

import pytest
from unittest.mock import patch, MagicMock
from utils.retrieval import (
    _embed_query,
    _format_embedding_for_pgvector,
    format_episodes_for_prompt,
    retrieve_and_format,
    retrieve_similar_episodes,
)


class TestFormatEmbeddingForPgvector:

    def test_produces_bracket_format(self):
        result = _format_embedding_for_pgvector([0.1, 0.2, 0.3])
        assert result.startswith("[")
        assert result.endswith("]")

    def test_values_comma_separated(self):
        result = _format_embedding_for_pgvector([0.1, 0.2, 0.3])
        assert "," in result

    def test_correct_value_count(self):
        embedding = [0.1] * 1536
        result = _format_embedding_for_pgvector(embedding)
        # Count commas + 1 = number of values
        assert result.count(",") == 1535

    def test_round_trip_parseable(self):
        import ast
        embedding = [0.1, -0.5, 0.999]
        result = _format_embedding_for_pgvector(embedding)
        parsed = ast.literal_eval(result)
        assert len(parsed) == 3


class TestFormatEpisodesForPrompt:

    def test_empty_list_returns_empty_string(self):
        assert format_episodes_for_prompt([]) == ""

    def test_contains_episode_content(self):
        episodes = [{
            "content": "AAPL bull put spread trade episode.",
            "metadata": {"realized_pnl": 150.0, "ror_pct": 21.4,
                         "strategy": "bull_put_spread"},
            "similarity": 0.87,
        }]
        result = format_episodes_for_prompt(episodes)
        assert "AAPL bull put spread trade episode." in result

    def test_win_loss_label_correct(self):
        win_episode = [{
            "content": "Winning trade.",
            "metadata": {"realized_pnl": 200.0, "ror_pct": 28.5,
                         "strategy": "long_call"},
            "similarity": 0.82,
        }]
        loss_episode = [{
            "content": "Losing trade.",
            "metadata": {"realized_pnl": -300.0, "ror_pct": -42.8,
                         "strategy": "long_put"},
            "similarity": 0.79,
        }]
        win_result = format_episodes_for_prompt(win_episode)
        loss_result = format_episodes_for_prompt(loss_episode)
        assert "WIN" in win_result
        assert "LOSS" in loss_result

    def test_multiple_episodes_numbered(self):
        episodes = [
            {"content": "Episode A.", "metadata": {"realized_pnl": 100.0,
             "ror_pct": 14.2, "strategy": "bull_put_spread"}, "similarity": 0.90},
            {"content": "Episode B.", "metadata": {"realized_pnl": -50.0,
             "ror_pct": -7.1, "strategy": "long_call"}, "similarity": 0.81},
        ]
        result = format_episodes_for_prompt(episodes)
        assert "Episode 1" in result
        assert "Episode 2" in result

    def test_similarity_shown_as_percentage(self):
        episodes = [{
            "content": "Test episode.",
            "metadata": {"realized_pnl": 75.0, "ror_pct": 10.7,
                         "strategy": "iron_condor"},
            "similarity": 0.85,
        }]
        result = format_episodes_for_prompt(episodes)
        assert "85%" in result

    def test_none_similarity_handled(self):
        episodes = [{
            "content": "Test episode.",
            "metadata": {"realized_pnl": 75.0, "ror_pct": 10.7,
                         "strategy": "iron_condor"},
            "similarity": None,
        }]
        result = format_episodes_for_prompt(episodes)
        assert "Test episode." in result


class TestEmbedQuery:

    def test_returns_none_when_client_unavailable(self):
        with patch("utils.retrieval._openai_client", None):
            result = _embed_query("test query")
        assert result is None

    def test_returns_embedding_on_success(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=[0.1] * 1536)]
        mock_client.embeddings.create.return_value = mock_response

        with patch("utils.retrieval._openai_client", mock_client):
            result = _embed_query("test query")

        assert result == [0.1] * 1536

    def test_returns_none_on_api_error(self):
        mock_client = MagicMock()
        mock_client.embeddings.create.side_effect = Exception("API error")

        with patch("utils.retrieval._openai_client", mock_client):
            result = _embed_query("test query")

        assert result is None


class TestRetrieveSimilarEpisodes:

    def test_returns_empty_list_when_embedding_fails(self):
        with patch("utils.retrieval._embed_query", return_value=None):
            result = retrieve_similar_episodes("test query")
        assert result == []

    def test_returns_empty_list_on_db_error(self):
        with patch("utils.retrieval._embed_query",
                   return_value=[0.1] * 1536), \
             patch("utils.retrieval.supabase") as mock_db:
            mock_db.rpc.side_effect = Exception("DB error")
            mock_db.table.return_value.select.return_value \
                .eq.return_value.not_.return_value \
                .is_.return_value.limit.return_value \
                .execute.side_effect = Exception("DB error")
            result = retrieve_similar_episodes("test query")
        assert result == []

    def test_filters_by_similarity_threshold(self):
        mock_episodes = [
            {"content": "High sim", "metadata": {}, "ticker": "AAPL",
             "trader_id": "alex", "similarity": 0.92},
            {"content": "Low sim", "metadata": {}, "ticker": "AAPL",
             "trader_id": "alex", "similarity": 0.45},
        ]
        with patch("utils.retrieval._embed_query",
                   return_value=[0.1] * 1536), \
             patch("utils.retrieval.supabase") as mock_db:
            mock_db.rpc.return_value.execute.return_value \
                = MagicMock(data=mock_episodes)
            result = retrieve_similar_episodes("test query",
                                               min_similarity=0.70)
        assert len(result) == 1
        assert result[0]["content"] == "High sim"

    def test_respects_top_k(self):
        mock_episodes = [
            {"content": f"Episode {i}", "metadata": {}, "ticker": "AAPL",
             "trader_id": "alex", "similarity": 0.90 - i * 0.01}
            for i in range(10)
        ]
        with patch("utils.retrieval._embed_query",
                   return_value=[0.1] * 1536), \
             patch("utils.retrieval.supabase") as mock_db:
            mock_db.rpc.return_value.execute.return_value \
                = MagicMock(data=mock_episodes)
            result = retrieve_similar_episodes("test query", top_k=3,
                                               min_similarity=0.0)
        assert len(result) <= 3


class TestRetrieveAndFormat:

    def test_returns_empty_string_when_no_episodes(self):
        with patch("utils.retrieval.retrieve_similar_episodes",
                   return_value=[]):
            result = retrieve_and_format("test query")
        assert result == ""

    def test_returns_formatted_string_when_episodes_found(self):
        mock_episodes = [{
            "content": "AAPL bull put spread was profitable.",
            "metadata": {"realized_pnl": 180.0, "ror_pct": 25.7,
                         "strategy": "bull_put_spread"},
            "similarity": 0.88,
        }]
        with patch("utils.retrieval.retrieve_similar_episodes",
                   return_value=mock_episodes):
            result = retrieve_and_format("test query")
        assert "AAPL bull put spread was profitable." in result
        assert isinstance(result, str)
