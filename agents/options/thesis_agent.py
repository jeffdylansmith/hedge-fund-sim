"""
thesis_agent.py

Produces a structured directional thesis for options strategy selection.
This is a focused distillation of news + technical signals into a clean
signal the strategy_agent can consume without ambiguity.

Distinct from the existing news_analyst and technical_analyst — those
agents produce verbose analysis for the portfolio_manager. This agent
produces a compact, structured signal specifically for options decisions.
"""

import os
from typing import Optional
import anthropic

_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

VALID_DIRECTIONS = {"bullish", "bearish", "neutral"}
VALID_TIMEFRAMES = {"short", "medium", "long"}


def _build_thesis_prompt(
    ticker: str,
    news_summary: dict,
    tech_signals: dict,
    trader_config,
) -> str:
    """
    Builds the prompt for thesis generation.
    Kept as a separate function so it can be tested independently.
    """
    return f"""You are an options thesis analyst for trader {trader_config.name}.

Analyze these signals for {ticker} and produce a structured trading thesis.

NEWS SUMMARY:
Sentiment: {news_summary.get('sentiment', 'neutral')}
Summary: {news_summary.get('summary', 'No news available')}

TECHNICAL SIGNALS:
RSI: {tech_signals.get('rsi', 50)}
MACD: {tech_signals.get('macd', 0)}
Trend: {tech_signals.get('trend', 'sideways')}
Signals: {tech_signals.get('signals', [])}

TRADER PROFILE:
Risk tolerance: {trader_config.risk_tolerance}

Respond in this exact JSON format, no other text:
{{
  "direction": "bullish" | "bearish" | "neutral",
  "conviction": 0.0-1.0,
  "timeframe": "short" | "medium" | "long",
  "catalyst": "one phrase describing primary driver",
  "thesis": "one sentence max 25 words"
}}

Timeframe definitions: short=1-2 weeks, medium=3-6 weeks, long=6-12 weeks.
Conviction: 0.0-0.4 weak, 0.4-0.7 moderate, 0.7-1.0 strong."""


def _parse_thesis_response(raw: str) -> dict:
    """
    Parses and validates the LLM JSON response.
    Returns a safe default on any parse failure rather than raising —
    agents must never crash the graph on malformed LLM output.
    """
    import json
    import re

    default = {
        "direction": "neutral",
        "conviction": 0.3,
        "timeframe": "medium",
        "catalyst": "insufficient signal",
        "thesis": "Signals are mixed, no clear directional thesis.",
    }

    try:
        # Strip markdown fences if present
        cleaned = re.sub(r"```json|```", "", raw).strip()
        parsed = json.loads(cleaned)

        # Validate and sanitize each field
        direction = parsed.get("direction", "neutral").lower()
        if direction not in VALID_DIRECTIONS:
            direction = "neutral"

        conviction = float(parsed.get("conviction", 0.3))
        conviction = max(0.0, min(1.0, conviction))

        timeframe = parsed.get("timeframe", "medium").lower()
        if timeframe not in VALID_TIMEFRAMES:
            timeframe = "medium"

        catalyst = str(parsed.get("catalyst", default["catalyst"]))[:100]
        thesis = str(parsed.get("thesis", default["thesis"]))[:200]

        return {
            "direction": direction,
            "conviction": round(conviction, 2),
            "timeframe": timeframe,
            "catalyst": catalyst,
            "thesis": thesis,
        }

    except Exception:
        return default


def generate_thesis(
    ticker: str,
    news_summary: dict,
    tech_signals: dict,
    trader_config,
    persona: str = "",
) -> dict:
    """
    Main entry point. Generates a structured directional thesis.

    Returns:
        ticker: str
        direction: "bullish" | "bearish" | "neutral"
        conviction: float 0.0-1.0
        timeframe: "short" | "medium" | "long"
        catalyst: str
        thesis: str
    """
    prompt = _build_thesis_prompt(ticker, news_summary, tech_signals, trader_config)
    system = persona if persona else f"You are a concise options thesis analyst for {trader_config.name}."

    try:
        response = _client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text
    except Exception:
        raw = ""

    result = _parse_thesis_response(raw)
    result["ticker"] = ticker
    return result
