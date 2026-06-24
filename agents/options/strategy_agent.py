"""
strategy_agent.py

Selects an options strategy based on directional thesis + volatility assessment.

The core decision logic is a deterministic matrix — not LLM-driven.
This is intentional: strategy selection must be auditable and consistent.
The LLM adds a rationale narrative after the decision is made.

Strategies supported:
  long_call         — bullish, low IV, defined risk
  long_put          — bearish, low IV, defined risk
  bull_put_spread   — bullish, high IV, credit spread
  bear_call_spread  — bearish, high IV, credit spread
  iron_condor       — neutral, high IV, range-bound
  skip              — no suitable strategy (neutral+low IV, or earnings override)
"""

import os
import anthropic
from utils.options_math import expected_move, all_greeks

_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

STRATEGIES = {
    "long_call",
    "long_put",
    "bull_put_spread",
    "bear_call_spread",
    "iron_condor",
    "skip",
}


def _select_strategy(
    direction: str,
    conviction: float,
    iv_environment: str,
    recommendation: str,
) -> str:
    """
    Deterministic strategy selection matrix.
    This function contains no LLM calls — it is pure logic.
    Every path through this function is testable with exact assertions.

    The matrix:
    avoid_earnings              → skip (always, regardless of thesis)
    neutral + low IV            → skip (no edge)
    neutral + moderate/high IV  → iron_condor
    bullish + low/moderate IV   → long_call
    bullish + high IV           → bull_put_spread (sell expensive premium)
    bearish + low/moderate IV   → long_put
    bearish + high IV           → bear_call_spread (sell expensive premium)

    Conviction below 0.35 with neutral direction forces skip —
    not enough signal to justify a position.
    """
    if recommendation == "avoid_earnings":
        return "skip"

    if direction == "neutral":
        if iv_environment == "low":
            return "skip"
        return "iron_condor"

    if conviction < 0.35 and direction == "neutral":
        return "skip"

    if direction == "bullish":
        if iv_environment == "high":
            return "bull_put_spread"
        return "long_call"

    if direction == "bearish":
        if iv_environment == "high":
            return "bear_call_spread"
        return "long_put"

    return "skip"


def _suggest_strikes(
    strategy: str,
    current_price: float,
    iv: float,
    days_to_expiration: int = 30,
) -> dict:
    """
    Suggests strikes based on expected move and strategy type.
    These are starting points — the greeks_risk_agent validates them.

    Uses the expected move formula to position strikes at meaningful
    probability levels relative to current price.

    For spreads, the short strike is near 1 std dev out,
    the long strike (protection) is near 1.5 std devs out.
    """
    em = expected_move(current_price, iv, days_to_expiration)

    if strategy == "long_call":
        # ATM or slightly OTM — balance cost vs delta
        return {
            "strike": round(current_price * 1.01, 0),
            "expiration_days": days_to_expiration,
        }

    elif strategy == "long_put":
        return {
            "strike": round(current_price * 0.99, 0),
            "expiration_days": days_to_expiration,
        }

    elif strategy == "bull_put_spread":
        # Short put just below 1 std dev, long put 1.5 std devs down
        return {
            "short_strike": round(current_price - em, 0),
            "long_strike": round(current_price - em * 1.5, 0),
            "expiration_days": days_to_expiration,
        }

    elif strategy == "bear_call_spread":
        return {
            "short_strike": round(current_price + em, 0),
            "long_strike": round(current_price + em * 1.5, 0),
            "expiration_days": days_to_expiration,
        }

    elif strategy == "iron_condor":
        # Short strikes at 1 std dev both sides, long strikes at 1.5
        return {
            "put_short_strike": round(current_price - em, 0),
            "put_long_strike": round(current_price - em * 1.5, 0),
            "call_short_strike": round(current_price + em, 0),
            "call_long_strike": round(current_price + em * 1.5, 0),
            "expiration_days": days_to_expiration,
        }

    return {}


def _compute_position_greeks(
    strategy: str,
    strikes: dict,
    current_price: float,
    iv: float,
    days_to_expiration: int,
    r: float = 0.05,
) -> dict:
    """
    Computes net Greeks for the selected strategy and strikes.
    T is converted from days to years for Black-Scholes.

    For spreads, net Greeks = long leg Greeks - short leg Greeks.
    For single-leg strategies, Greeks are the option's Greeks directly.
    """
    T = days_to_expiration / 365

    if strategy == "long_call":
        return all_greeks(current_price, strikes["strike"], T, r, iv, "call")

    elif strategy == "long_put":
        return all_greeks(current_price, strikes["strike"], T, r, iv, "put")

    elif strategy == "bull_put_spread":
        short = all_greeks(current_price, strikes["short_strike"], T, r, iv, "put")
        long_ = all_greeks(current_price, strikes["long_strike"], T, r, iv, "put")
        return {
            "price": round(short["price"] - long_["price"], 4),
            "delta": round(short["delta"] - long_["delta"], 4),
            "gamma": round(short["gamma"] - long_["gamma"], 4),
            "theta": round(short["theta"] - long_["theta"], 4),
            "vega": round(short["vega"] - long_["vega"], 4),
            "strategy": strategy,
        }

    elif strategy == "bear_call_spread":
        short = all_greeks(current_price, strikes["short_strike"], T, r, iv, "call")
        long_ = all_greeks(current_price, strikes["long_strike"], T, r, iv, "call")
        return {
            "price": round(short["price"] - long_["price"], 4),
            "delta": round(short["delta"] - long_["delta"], 4),
            "gamma": round(short["gamma"] - long_["gamma"], 4),
            "theta": round(short["theta"] - long_["theta"], 4),
            "vega": round(short["vega"] - long_["vega"], 4),
            "strategy": strategy,
        }

    elif strategy == "iron_condor":
        put_short = all_greeks(current_price, strikes["put_short_strike"], T, r, iv, "put")
        put_long = all_greeks(current_price, strikes["put_long_strike"], T, r, iv, "put")
        call_short = all_greeks(current_price, strikes["call_short_strike"], T, r, iv, "call")
        call_long = all_greeks(current_price, strikes["call_long_strike"], T, r, iv, "call")
        return {
            "price": round(
                (put_short["price"] - put_long["price"]) +
                (call_short["price"] - call_long["price"]), 4
            ),
            "delta": round(
                (put_short["delta"] - put_long["delta"]) +
                (call_short["delta"] - call_long["delta"]), 4
            ),
            "gamma": round(
                (put_short["gamma"] - put_long["gamma"]) +
                (call_short["gamma"] - call_long["gamma"]), 4
            ),
            "theta": round(
                (put_short["theta"] - put_long["theta"]) +
                (call_short["theta"] - call_long["theta"]), 4
            ),
            "vega": round(
                (put_short["vega"] - put_long["vega"]) +
                (call_short["vega"] - call_long["vega"]), 4
            ),
            "strategy": strategy,
        }

    return {"strategy": "skip", "price": 0.0}


def _generate_strategy_rationale(
    ticker: str,
    strategy: str,
    thesis: dict,
    volatility: dict,
    greeks: dict,
    persona: str = "",
) -> str:
    """
    LLM-generated rationale explaining why this strategy was selected.
    Two sentences max. References the actual numbers.
    """
    if strategy == "skip":
        return "No suitable options strategy identified given current signals."

    prompt = f"""Explain in exactly two sentences why {strategy.replace('_', ' ')} \
was selected for {ticker}. Reference the specific numbers below.

Direction: {thesis['direction']} (conviction: {thesis['conviction']:.0%})
IV Environment: {volatility['iv_environment']} (IV Rank: {volatility['iv_rank']:.0f})
Expected 30d move: ${volatility['expected_move_30d']:.2f}
Net delta: {greeks.get('delta', 'N/A')}
Net theta: {greeks.get('theta', 'N/A')}

Two sentences only. No preamble."""

    system = persona if persona else "You are a concise options strategist."

    try:
        response = _client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception:
        return f"{strategy.replace('_', ' ')} selected based on {thesis['direction']} thesis and {volatility['iv_environment']} IV environment."


def select_strategy(
    ticker: str,
    thesis: dict,
    volatility: dict,
    current_price: float,
    trader_config,
    persona: str = "",
) -> dict:
    """
    Main entry point. Selects strategy and computes Greeks for the position.

    Returns:
        ticker: str
        strategy: str (one of STRATEGIES)
        strikes: dict (strategy-dependent strike structure)
        greeks: dict (net Greeks for the full position)
        rationale: str
        skip_reason: str | None
    """
    strategy = _select_strategy(
        direction=thesis.get("direction", "neutral"),
        conviction=thesis.get("conviction", 0.0),
        iv_environment=volatility.get("iv_environment", "moderate"),
        recommendation=volatility.get("recommendation", "neutral"),
    )

    if strategy == "skip":
        return {
            "ticker": ticker,
            "strategy": "skip",
            "strikes": {},
            "greeks": {},
            "rationale": "No suitable options strategy given current signals.",
            "skip_reason": _skip_reason(thesis, volatility),
        }

    iv = volatility.get("current_iv", 0.25)
    strikes = _suggest_strikes(strategy, current_price, iv)
    greeks = _compute_position_greeks(strategy, strikes, current_price, iv, 30)
    rationale = _generate_strategy_rationale(
        ticker, strategy, thesis, volatility, greeks, persona
    )

    return {
        "ticker": ticker,
        "strategy": strategy,
        "strikes": strikes,
        "greeks": greeks,
        "rationale": rationale,
        "skip_reason": None,
    }


def _skip_reason(thesis: dict, volatility: dict) -> str:
    """Human-readable explanation of why we skipped."""
    if volatility.get("recommendation") == "avoid_earnings":
        days = volatility.get("earnings_proximity")
        return f"Earnings in {days} days — IV crush and gap risk too high."
    if thesis.get("direction") == "neutral" and volatility.get("iv_environment") == "low":
        return "Neutral thesis with low IV — no premium to sell, no directional edge to buy."
    if thesis.get("conviction", 0) < 0.35:
        return f"Conviction too low ({thesis.get('conviction', 0):.0%}) to justify options position."
    return "Insufficient signal for options trade."
