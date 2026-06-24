"""
volatility_agent.py

Analyzes the implied volatility environment for a ticker.
Answers the question: are options cheap or expensive right now?

This agent does NOT make trading decisions. It produces a volatility
assessment that the strategy_agent uses to select appropriate strategies.

Math is pure Python (no LLM). LLM is used only for the one-sentence rationale.
"""

import os
import math
from datetime import datetime, timedelta
from typing import Optional
import yfinance as yf
import anthropic

_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))


def _get_iv_history(ticker: str, days: int = 365) -> list[float]:
    """
    Pulls historical implied volatility approximation from yfinance.

    yfinance does not expose IV directly on historical bars.
    We approximate historical IV using the VIX relationship and
    realized volatility (annualized standard deviation of daily returns).

    This is an approximation — in production you'd store actual IV
    from options chain snapshots daily. For now realized vol gives
    us the right shape and magnitude for rank/percentile calculations.

    Returns list of daily vol estimates, most recent last.
    """
    stock = yf.Ticker(ticker)
    hist = stock.history(period="1y")

    if hist.empty or len(hist) < 20:
        return []

    # Rolling 20-day realized volatility, annualized
    returns = hist["Close"].pct_change().dropna()
    vol_series = returns.rolling(window=20).std() * math.sqrt(252)
    vol_series = vol_series.dropna()

    return vol_series.tolist()


def _get_current_iv(ticker: str) -> float:
    """
    Gets current implied volatility from the nearest ATM options chain.
    Uses the front-month expiration (closest to 30 days out).
    Falls back to realized vol if options chain unavailable.
    """
    try:
        stock = yf.Ticker(ticker)
        current_price = stock.info.get("regularMarketPrice") or stock.fast_info.last_price

        # Get available expirations
        expirations = stock.options
        if not expirations:
            raise ValueError("No options chain available")

        # Find expiration closest to 30 days out
        today = datetime.today().date()
        target = today + timedelta(days=30)
        best_exp = min(
            expirations,
            key=lambda e: abs((datetime.strptime(e, "%Y-%m-%d").date() - target).days)
        )

        chain = stock.option_chain(best_exp)
        calls = chain.calls

        # Find ATM strike (closest to current price)
        calls = calls.copy()
        calls["distance"] = abs(calls["strike"] - current_price)
        atm_call = calls.loc[calls["distance"].idxmin()]

        iv = atm_call["impliedVolatility"]
        if iv and iv > 0:
            return float(iv)

    except Exception:
        pass

    # Fallback: use most recent realized vol
    history = _get_iv_history(ticker)
    return history[-1] if history else 0.25


def _compute_iv_rank(current_iv: float, iv_history: list[float]) -> float:
    """
    IV Rank: where is current IV in its 52-week high/low range?
    Formula: (current - low) / (high - low) * 100
    Returns 0-100. Above 50 = elevated. Above 80 = high.

    Weakness: sensitive to outlier spikes. One extreme day
    compresses everything else toward 0. IV Percentile is more robust.
    """
    if not iv_history or len(iv_history) < 2:
        return 50.0

    low = min(iv_history)
    high = max(iv_history)

    if high == low:
        return 50.0

    return round((current_iv - low) / (high - low) * 100, 1)


def _compute_iv_percentile(current_iv: float, iv_history: list[float]) -> float:
    """
    IV Percentile: what % of days in past year had lower IV than today?
    More robust than IV Rank because it's not distorted by single spikes.
    Above 50 = higher IV than most days this year.
    Above 80 = IV is in the top 20% of the year.
    """
    if not iv_history:
        return 50.0

    days_below = sum(1 for v in iv_history if v < current_iv)
    return round(days_below / len(iv_history) * 100, 1)


def _iv_environment(iv_rank: float) -> str:
    """
    Classify IV environment from rank.
    Thresholds are conventional options trading standards.
    """
    if iv_rank >= 70:
        return "high"
    elif iv_rank >= 35:
        return "moderate"
    else:
        return "low"


def _strategy_recommendation(iv_environment: str, earnings_proximity: Optional[int]) -> str:
    """
    Maps IV environment to a directional strategy recommendation.
    This is not the full strategy selection — that lives in strategy_agent.
    This just tells the strategy agent which "camp" we're in.

    high IV    → sell_premium  (options are expensive, favor selling)
    moderate   → neutral       (either direction depending on thesis)
    low IV     → buy_premium   (options are cheap, favor buying)

    Earnings proximity overrides: if earnings < 5 days away,
    recommend avoid — IV crush risk is too high for buyers,
    and gap risk is too high for sellers.
    """
    if earnings_proximity is not None and earnings_proximity <= 5:
        return "avoid_earnings"

    if iv_environment == "high":
        return "sell_premium"
    elif iv_environment == "low":
        return "buy_premium"
    else:
        return "neutral"


def _get_earnings_proximity(ticker: str) -> Optional[int]:
    """
    Returns days until next earnings, or None if not within 30 days.
    Uses yfinance calendar. Returns None on failure.
    """
    try:
        stock = yf.Ticker(ticker)
        cal = stock.calendar
        if cal is None:
            return None

        # calendar can be a dict or DataFrame depending on yfinance version
        if hasattr(cal, 'get'):
            earnings_date = cal.get("Earnings Date")
        elif hasattr(cal, 'iloc'):
            earnings_date = cal.iloc[0].get("Earnings Date") if not cal.empty else None
        else:
            return None

        if earnings_date is None:
            return None

        # Handle list (yfinance sometimes returns a range)
        if isinstance(earnings_date, list):
            earnings_date = earnings_date[0]

        days = (earnings_date.date() - datetime.today().date()).days
        return days if 0 <= days <= 30 else None

    except Exception:
        return None


def _generate_rationale(
    ticker: str,
    current_iv: float,
    iv_rank: float,
    iv_percentile: float,
    iv_environment: str,
    recommendation: str,
    earnings_proximity: Optional[int],
    persona: str = "",
) -> str:
    """
    One-sentence LLM rationale explaining the volatility assessment.
    Pure narrative — no decision-making here.
    """
    earnings_str = f"Earnings in {earnings_proximity} days." if earnings_proximity else "No imminent earnings."

    prompt = f"""You are a volatility analyst. Write exactly one sentence (max 30 words)
explaining this IV assessment for {ticker}.

Current IV: {current_iv:.1%}
IV Rank: {iv_rank:.0f}/100
IV Percentile: {iv_percentile:.0f}th percentile
Environment: {iv_environment}
Recommendation: {recommendation}
{earnings_str}

One sentence only. Be specific about the numbers. No preamble."""

    system = persona if persona else "You are a concise volatility analyst."

    try:
        response = _client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=80,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception:
        return f"{ticker} IV rank {iv_rank:.0f} suggests {recommendation.replace('_', ' ')} environment."


def assess_volatility(
    ticker: str,
    persona: str = "",
) -> dict:
    """
    Main entry point. Produces a full volatility assessment for a ticker.

    Returns:
        ticker: str
        current_iv: float (decimal, e.g. 0.31 = 31%)
        iv_rank: float (0-100)
        iv_percentile: float (0-100)
        iv_environment: "low" | "moderate" | "high"
        expected_move_30d: float (dollars, 1 std dev over 30 days)
        earnings_proximity: int | None (days to earnings, None if >30 days)
        recommendation: "buy_premium" | "sell_premium" | "neutral" | "avoid_earnings"
        rationale: str
    """
    from utils.options_math import expected_move

    current_iv = _get_current_iv(ticker)
    iv_history = _get_iv_history(ticker)
    iv_rank = _compute_iv_rank(current_iv, iv_history)
    iv_percentile = _compute_iv_percentile(current_iv, iv_history)
    environment = _iv_environment(iv_rank)
    earnings_proximity = _get_earnings_proximity(ticker)
    recommendation = _strategy_recommendation(environment, earnings_proximity)

    # Get current price for expected move calculation
    try:
        stock = yf.Ticker(ticker)
        price = stock.fast_info.last_price
    except Exception:
        price = 100.0

    em_30d = expected_move(price, current_iv, 30)

    rationale = _generate_rationale(
        ticker, current_iv, iv_rank, iv_percentile,
        environment, recommendation, earnings_proximity, persona
    )

    return {
        "ticker": ticker,
        "current_iv": round(current_iv, 4),
        "iv_rank": iv_rank,
        "iv_percentile": iv_percentile,
        "iv_environment": environment,
        "expected_move_30d": round(em_30d, 2),
        "earnings_proximity": earnings_proximity,
        "recommendation": recommendation,
        "rationale": rationale,
    }
