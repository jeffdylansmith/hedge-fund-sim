"""
episode_synthesizer.py

Transforms closed options positions into structured narrative episodes
suitable for semantic search via RAG.

Why narrative and not structured data:
  Embedding models convert text to vectors based on semantic meaning.
  A JSON blob of field names and numbers has poor semantic density —
  "entry_delta: 0.45, theta: -0.04" embeds differently than
  "moderate directional exposure with manageable time decay."
  The narrative form retrieves better because it uses the same
  language agents use when querying.

Pipeline:
  closed position record
      → _build_episode_narrative() → rich text document
      → _embed_text() → 1536-dimension vector
      → store in rag_documents with metadata for filtered retrieval

This runs at position close time. At larger scale this would be
a separate async pipeline stage triggered by a close event.
"""

import os
from datetime import datetime
from typing import Optional
from openai import OpenAI
from utils.db import supabase

_openai_client = None
try:
    _openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
except Exception:
    pass

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536


def _build_episode_narrative(position: dict) -> str:
    """
    Synthesizes a closed position record into a narrative episode.

    The narrative is written in the same language an agent would use
    when querying — directional terms, Greeks descriptions, outcome framing.
    This maximizes semantic similarity between stored episodes and
    future agent queries.

    Structure:
      1. Setup — what was the market context when we entered?
      2. Strategy — what did we do and why?
      3. Risk profile — what were the Greeks saying?
      4. Outcome — what happened and what did we learn?
    """
    ticker = position.get("ticker", "UNKNOWN")
    strategy = position.get("strategy", "unknown").replace("_", " ")
    trader_id = position.get("trader_id", "unknown")
    contracts = position.get("contracts", 1)

    # Entry Greeks
    entry_delta = position.get("entry_delta")
    entry_theta = position.get("entry_theta")
    entry_vega = position.get("entry_vega")
    entry_price = position.get("entry_price", 0.0)
    net_credit_debit = position.get("net_credit_debit", 0.0)

    # Outcome
    realized_pnl = position.get("realized_pnl", 0.0)
    max_loss_total = position.get("max_loss_total", 1.0)
    close_reason = position.get("close_reason", "unknown")
    opened_at = position.get("opened_at", "")
    closed_at = position.get("closed_at", "")
    expiration_date = position.get("expiration_date", "")

    # Compute return on risk (ROR) — P&L as % of max loss risked
    ror = (realized_pnl / max_loss_total * 100) if max_loss_total > 0 else 0.0
    outcome_direction = "profitable" if realized_pnl > 0 else "losing"

    # Greeks descriptions
    delta_desc = _describe_delta(entry_delta, strategy)
    theta_desc = _describe_theta(entry_theta)
    vega_desc = _describe_vega(entry_vega)

    # Position type description
    is_credit = net_credit_debit > 0
    position_type = "credit" if is_credit else "debit"
    premium_desc = (
        f"collected ${abs(net_credit_debit * contracts * 100):,.0f} in premium"
        if is_credit
        else f"paid ${abs(net_credit_debit * contracts * 100):,.0f} in premium"
    )

    # Close reason narrative
    close_narrative = {
        "expired": "held to expiration",
        "stop_loss": "closed at stop loss to limit further losses",
        "profit_target": "closed at profit target (50% of max profit)",
        "manual": "manually closed",
    }.get(close_reason, f"closed: {close_reason}")

    narrative = f"""
Options trade episode for {ticker} by trader {trader_id}.

TRADE SETUP:
Trader {trader_id} entered a {strategy} position on {ticker} using {contracts} contract(s).
This was a {position_type} strategy — the position {premium_desc}.
Entry date: {opened_at[:10] if opened_at else 'unknown'}.
Expiration: {expiration_date}.

RISK PROFILE AT ENTRY:
{delta_desc}
{theta_desc}
{vega_desc}
Maximum risk on the trade: ${max_loss_total:,.0f}.

OUTCOME:
The position was {close_narrative}.
Realized P&L: ${realized_pnl:+,.2f} ({ror:+.1f}% return on risk).
This was a {outcome_direction} trade.
Close date: {closed_at[:10] if closed_at else 'unknown'}.

CLASSIFICATION:
Strategy type: {strategy}
Outcome: {'win' if realized_pnl > 0 else 'loss'}
Close reason: {close_reason}
Return on risk: {ror:+.1f}%
""".strip()

    return narrative


def _describe_delta(delta: Optional[float], strategy: str) -> str:
    if delta is None:
        return "Delta exposure was not recorded at entry."
    abs_delta = abs(delta)
    if abs_delta > 0.6:
        intensity = "strong"
    elif abs_delta > 0.35:
        intensity = "moderate"
    else:
        intensity = "low"

    direction = "bullish" if delta > 0 else "bearish" if delta < 0 else "neutral"
    return (
        f"The position had {intensity} {direction} directional exposure "
        f"(delta: {delta:+.2f}), meaning a $1 move in {direction.replace('bullish','up').replace('bearish','down')} "
        f"direction would change position value by approximately ${abs_delta * 100:.0f} per contract."
    )


def _describe_theta(theta: Optional[float]) -> str:
    if theta is None:
        return "Theta (time decay) was not recorded at entry."
    daily_decay = theta * 100  # per contract per day
    if daily_decay > 0:
        return (
            f"Time decay worked in our favor — the position collected approximately "
            f"${daily_decay:.2f} per contract per day (theta: {theta:+.4f})."
        )
    else:
        return (
            f"Time decay worked against the position — approximately "
            f"${abs(daily_decay):.2f} per contract per day was lost to theta decay "
            f"(theta: {theta:+.4f})."
        )


def _describe_vega(vega: Optional[float]) -> str:
    if vega is None:
        return "Vega (volatility sensitivity) was not recorded at entry."
    per_contract = vega * 100
    if vega > 0:
        return (
            f"The position benefited from rising implied volatility "
            f"(vega: {vega:+.4f}, ~${per_contract:.2f} per 1% IV move per contract)."
        )
    else:
        return (
            f"The position benefited from falling implied volatility — "
            f"IV crush would help this position "
            f"(vega: {vega:+.4f}, ~${abs(per_contract):.2f} per 1% IV drop per contract)."
        )


def _embed_text(text: str) -> Optional[list[float]]:
    """
    Embeds text using OpenAI text-embedding-3-small.
    Returns 1536-dimension vector or None on failure.

    Cost: ~$0.02 per 1M tokens. An episode narrative is ~300 tokens.
    1000 episodes = ~$0.006. Effectively free at this scale.

    At larger scale (millions of documents) you'd batch embed
    using the OpenAI batch API for 50% cost reduction.
    """
    try:
        response = _openai_client.embeddings.create(
            input=text,
            model=EMBEDDING_MODEL,
        )
        return response.data[0].embedding
    except Exception as e:
        print(f"[episode_synthesizer] Embedding failed: {e}")
        return None


def _store_episode(
    position: dict,
    narrative: str,
    embedding: Optional[list[float]],
) -> Optional[str]:
    """
    Stores the episode in rag_documents.
    Stores narrative even if embedding failed — can backfill embeddings later.
    """
    try:
        event_date = None
        closed_at = position.get("closed_at", "")
        if closed_at:
            event_date = closed_at[:10]

        metadata = {
            "strategy": position.get("strategy"),
            "contracts": position.get("contracts"),
            "realized_pnl": position.get("realized_pnl"),
            "max_loss_total": position.get("max_loss_total"),
            "close_reason": position.get("close_reason"),
            "entry_delta": position.get("entry_delta"),
            "entry_theta": position.get("entry_theta"),
            "entry_vega": position.get("entry_vega"),
            "ror_pct": (
                position.get("realized_pnl", 0) /
                position.get("max_loss_total", 1) * 100
            ),
        }

        row = {
            "source_type": "options_episode",
            "source_id": str(position.get("id")),
            "trader_id": position.get("trader_id"),
            "ticker": position.get("ticker"),
            "content": narrative,
            "metadata": metadata,
            "embedding_model": EMBEDDING_MODEL,
            "event_date": event_date,
        }

        if embedding:
            row["embedding"] = embedding

        result = supabase.table("rag_documents").insert(row).execute()
        return result.data[0]["id"] if result.data else None

    except Exception as e:
        print(f"[episode_synthesizer] Storage failed: {e}")
        return None


def synthesize_episode(position: dict) -> dict:
    """
    Main entry point. Called after a position is closed.

    Returns:
        success: bool
        document_id: str | None
        narrative_length: int
        embedding_succeeded: bool
        error: str | None
    """
    try:
        narrative = _build_episode_narrative(position)
        embedding = _embed_text(narrative)
        document_id = _store_episode(position, narrative, embedding)

        return {
            "success": document_id is not None,
            "document_id": document_id,
            "narrative_length": len(narrative),
            "embedding_succeeded": embedding is not None,
            "error": None,
        }

    except Exception as e:
        return {
            "success": False,
            "document_id": None,
            "narrative_length": 0,
            "embedding_succeeded": False,
            "error": str(e),
        }


def synthesize_batch(position_ids: list[str]) -> list[dict]:
    """
    Synthesizes episodes for multiple closed positions.
    Used for backfill — processing positions that closed before
    the synthesizer existed.

    At larger scale this would be chunked and processed
    asynchronously to avoid blocking.
    """
    results = []
    for position_id in position_ids:
        try:
            result = supabase.table("options_positions") \
                .select("*") \
                .eq("id", position_id) \
                .eq("status", "closed") \
                .single() \
                .execute()

            if result.data:
                synthesis = synthesize_episode(result.data)
                synthesis["position_id"] = position_id
                results.append(synthesis)
            else:
                results.append({
                    "position_id": position_id,
                    "success": False,
                    "error": "Position not found or not closed",
                })
        except Exception as e:
            results.append({
                "position_id": position_id,
                "success": False,
                "error": str(e),
            })

    return results
