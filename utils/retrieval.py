"""
retrieval.py

RAG retrieval interface. Embeds a query and finds semantically
similar documents from rag_documents via pgvector cosine search.

Used by agents to inject historical context into their prompts.
Designed to degrade gracefully — returns empty list if corpus is
empty, pgvector unavailable, or embedding fails. Agents handle
empty retrieval by omitting the context block entirely.

At larger scale:
- Retrieval would filter by market regime, date range, trader profile
- Results would be reranked using a cross-encoder model for precision
- Caching would prevent re-embedding identical queries within a session
- Async retrieval would run in parallel with other agent work
"""

import os
from typing import Optional
from openai import OpenAI
from utils.db import supabase

try:
    _openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
except Exception:
    _openai_client = None

EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_TOP_K = 5
SIMILARITY_THRESHOLD = 0.70  # minimum similarity to include in results


def _embed_query(query: str) -> Optional[list[float]]:
    """
    Embeds the query string using the same model used at storage time.
    Critical: query and documents must use identical embedding models.
    Mixing models produces meaningless similarity scores.
    """
    if not _openai_client:
        return None
    try:
        response = _openai_client.embeddings.create(
            input=query,
            model=EMBEDDING_MODEL,
        )
        return response.data[0].embedding
    except Exception as e:
        print(f"[retrieval] Query embedding failed: {e}")
        return None


def _format_embedding_for_pgvector(embedding: list[float]) -> str:
    """
    Converts Python list to pgvector string format.
    pgvector expects: '[0.1, 0.2, 0.3, ...]'
    """
    return "[" + ",".join(str(x) for x in embedding) + "]"


def retrieve_similar_episodes(
    query: str,
    ticker: Optional[str] = None,
    trader_id: Optional[str] = None,
    top_k: int = DEFAULT_TOP_K,
    min_similarity: float = SIMILARITY_THRESHOLD,
) -> list[dict]:
    """
    Retrieves the most semantically similar episodes to the query.

    Filters:
      ticker    — restrict to episodes for a specific stock
      trader_id — restrict to a specific trader's history

    Returns list of dicts with:
      content:    the narrative text
      metadata:   structured data (strategy, pnl, greeks, etc.)
      similarity: float 0-1 (1.0 = identical meaning)

    Returns empty list on any failure — callers must handle gracefully.
    """
    embedding = _embed_query(query)
    if not embedding:
        return []

    try:
        embedding_str = _format_embedding_for_pgvector(embedding)

        # Build query — pgvector cosine distance via RPC
        # <=> is cosine distance: 0 = identical, 2 = opposite
        # We order ascending (smallest distance = most similar)
        query_str = f"""
            SELECT
                content,
                metadata,
                ticker,
                trader_id,
                event_date,
                1 - (embedding <=> '{embedding_str}'::vector) AS similarity
            FROM rag_documents
            WHERE source_type = 'options_episode'
              AND embedding IS NOT NULL
            ORDER BY embedding <=> '{embedding_str}'::vector
            LIMIT {top_k * 2}
        """

        result = supabase.rpc("execute_sql", {"query": query_str}).execute()

        # Fallback: use table API with post-filter if RPC not available
        if not result.data:
            result = supabase.table("rag_documents") \
                .select("content, metadata, ticker, trader_id, event_date") \
                .eq("source_type", "options_episode") \
                .not_.is_("embedding", "null") \
                .limit(top_k * 3) \
                .execute()

            if not result.data:
                return []

            # Without vector search, return most recent (best available fallback)
            episodes = result.data[:top_k]
            return [
                {
                    "content": ep["content"],
                    "metadata": ep.get("metadata", {}),
                    "ticker": ep.get("ticker"),
                    "similarity": None,
                }
                for ep in episodes
            ]

        episodes = result.data or []

        # Apply filters and similarity threshold
        filtered = []
        for ep in episodes:
            similarity = ep.get("similarity", 0.0)
            if similarity < min_similarity:
                continue
            if ticker and ep.get("ticker") != ticker:
                continue
            if trader_id and ep.get("trader_id") != trader_id:
                continue
            filtered.append({
                "content": ep["content"],
                "metadata": ep.get("metadata", {}),
                "ticker": ep.get("ticker"),
                "similarity": round(similarity, 4),
            })

        return filtered[:top_k]

    except Exception as e:
        print(f"[retrieval] Retrieval failed: {e}")
        return []


def format_episodes_for_prompt(episodes: list[dict]) -> str:
    """
    Formats retrieved episodes into a prompt-injectable context block.
    Returns empty string if no episodes — callers omit the block entirely.

    Format chosen for LLM readability:
    - Each episode clearly separated
    - Similarity score gives the LLM signal about relevance
    - Metadata highlights the outcome for easy pattern matching
    """
    if not episodes:
        return ""

    lines = ["RELEVANT HISTORICAL EPISODES FROM SIMILAR SETUPS:"]
    lines.append("=" * 50)

    for i, ep in enumerate(episodes, 1):
        similarity = ep.get("similarity")
        metadata = ep.get("metadata", {})

        sim_str = f" (similarity: {similarity:.0%})" if similarity else ""
        pnl = metadata.get("realized_pnl", 0)
        ror = metadata.get("ror_pct", 0)
        strategy = metadata.get("strategy", "unknown").replace("_", " ")
        outcome = "WIN" if pnl > 0 else "LOSS"

        lines.append(f"\nEpisode {i}{sim_str} — {outcome} "
                     f"(${pnl:+,.0f}, {ror:+.1f}% ROR):")
        lines.append(ep["content"])
        lines.append("-" * 30)

    return "\n".join(lines)


def retrieve_and_format(
    query: str,
    ticker: Optional[str] = None,
    trader_id: Optional[str] = None,
    top_k: int = DEFAULT_TOP_K,
) -> str:
    """
    Convenience function: retrieve + format in one call.
    Returns empty string if no relevant episodes found.
    Used directly by agents.
    """
    episodes = retrieve_similar_episodes(
        query=query,
        ticker=ticker,
        trader_id=trader_id,
        top_k=top_k,
    )
    return format_episodes_for_prompt(episodes)
