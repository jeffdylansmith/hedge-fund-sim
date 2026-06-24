"""
greeks_risk_agent.py

Risk gate for options positions. Evaluates a proposed strategy against:
  1. Trader risk tolerance (options_risk_cap_pct from TraderConfig)
  2. Current options exposure already in the portfolio
  3. Greeks thresholds appropriate to trader profile
  4. Position sizing (contracts) given max loss constraints

This agent makes a binary decision: APPROVE or REJECT.
If approved, it also computes the correct number of contracts.

No LLM is used for the approve/reject decision — it is purely
mathematical. The LLM generates a one-sentence explanation only.

Why no LLM for the decision:
  Risk management must be deterministic and auditable.
  An LLM that sometimes approves risky positions is not a risk manager.
"""

import os
import math
import anthropic
from agents.trader_config import TraderConfig

_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))


# Greeks thresholds by risk tolerance tier
# These are conventional options risk management limits
# scaled to the trader personality in trader_config

THRESHOLDS = {
    "conservative": {
        "max_delta_exposure": 0.30,   # max net delta per position
        "max_theta_daily_pct": 0.02,  # max daily theta as % of position value
        "max_vega_exposure": 0.15,    # max vega per $1k of position
        "min_days_to_expiration": 21, # never enter with < 21 DTE
    },
    "moderate": {
        "max_delta_exposure": 0.50,
        "max_theta_daily_pct": 0.04,
        "max_vega_exposure": 0.25,
        "min_days_to_expiration": 14,
    },
    "aggressive": {
        "max_delta_exposure": 0.75,
        "max_delta_exposure": 0.75,
        "max_theta_daily_pct": 0.07,
        "max_vega_exposure": 0.40,
        "min_days_to_expiration": 7,
    },
}

# Map trader risk_tolerance prose to tier
# This is a simple keyword match — extend as needed
def _get_risk_tier(trader_config: TraderConfig) -> str:
    rt = trader_config.risk_tolerance.lower()
    if any(w in rt for w in ["conservative", "cautious", "low", "minimal"]):
        return "conservative"
    elif any(w in rt for w in ["aggressive", "high", "maximum", "bold"]):
        return "aggressive"
    # Fall back to options_risk_cap_pct when prose is ambiguous
    cap = trader_config.options_risk_cap_pct
    if cap >= 0.18:
        return "aggressive"
    elif cap <= 0.10:
        return "conservative"
    return "moderate"


def _compute_max_loss(
    strategy: str,
    strikes: dict,
    greeks: dict,
    contracts: int,
) -> float:
    """
    Computes max loss for the position in dollars.

    Long options: max loss = premium paid × 100 × contracts
    Spreads: max loss = (wing_width - net_credit) × 100 × contracts
    Iron condor: max loss = (narrower wing width - net_credit) × 100 × contracts
    Skip: 0

    One contract controls 100 shares — this is the options standard.
    """
    price = greeks.get("price", 0.0)

    if strategy in ("long_call", "long_put"):
        # Premium paid is the max loss
        return abs(price) * 100 * contracts

    elif strategy == "bull_put_spread":
        wing_width = strikes.get("short_strike", 0) - strikes.get("long_strike", 0)
        net_credit = price  # positive for credit spreads
        return max(0.0, (wing_width - net_credit) * 100 * contracts)

    elif strategy == "bear_call_spread":
        wing_width = strikes.get("long_strike", 0) - strikes.get("short_strike", 0)
        net_credit = price
        return max(0.0, (wing_width - net_credit) * 100 * contracts)

    elif strategy == "iron_condor":
        # Max loss is the narrower wing minus total credit received
        put_width = strikes.get("put_short_strike", 0) - strikes.get("put_long_strike", 0)
        call_width = strikes.get("call_long_strike", 0) - strikes.get("call_short_strike", 0)
        wing = min(put_width, call_width)
        net_credit = price
        return max(0.0, (wing - net_credit) * 100 * contracts)

    return 0.0


def _compute_contracts(
    strategy: str,
    strikes: dict,
    greeks: dict,
    portfolio_value: float,
    trader_config: TraderConfig,
    conviction: float,
    existing_options_exposure: float = 0.0,
) -> int:
    """
    Determines number of contracts based on risk cap and conviction.

    Formula:
      available_risk = (portfolio_value × options_risk_cap_pct) - existing_exposure
      position_risk  = available_risk × conviction
      contracts      = floor(position_risk / max_loss_per_contract)
      contracts      = clamp(contracts, 1, 10)

    Conviction scales position size — high conviction gets more contracts.
    Existing exposure is subtracted so total exposure stays within cap.

    Returns 0 if no capital available within the cap.
    """
    cap = portfolio_value * trader_config.options_risk_cap_pct
    available = cap - existing_options_exposure

    if available <= 0:
        return 0

    # Scale by conviction — minimum 30% of available, max 100%
    position_budget = available * max(0.30, conviction)

    # Max loss for 1 contract
    max_loss_1 = _compute_max_loss(strategy, strikes, greeks, 1)

    if max_loss_1 <= 0:
        return 1

    contracts = math.floor(position_budget / max_loss_1)
    return max(0, min(contracts, 10))  # cap at 10 contracts


def _check_greeks_thresholds(
    greeks: dict,
    strategy: str,
    thresholds: dict,
    contracts: int,
) -> list[str]:
    """
    Checks Greeks against trader thresholds.
    Returns list of violation strings (empty = all clear).
    """
    violations = []
    price = abs(greeks.get("price", 0.0)) * 100 * contracts

    delta = abs(greeks.get("delta", 0.0))
    if delta > thresholds["max_delta_exposure"]:
        violations.append(
            f"Delta {delta:.2f} exceeds threshold {thresholds['max_delta_exposure']:.2f}"
        )

    if price > 0:
        theta_pct = abs(greeks.get("theta", 0.0)) / (price / 100)
        if theta_pct > thresholds["max_theta_daily_pct"]:
            violations.append(
                f"Daily theta burn {theta_pct:.1%} exceeds threshold {thresholds['max_theta_daily_pct']:.1%}"
            )

    return violations


def _generate_risk_explanation(
    decision: str,
    strategy: str,
    contracts: int,
    max_loss: float,
    violations: list[str],
    trader_config: TraderConfig,
    persona: str = "",
) -> str:
    """One-sentence LLM explanation of the risk decision."""
    if violations:
        reason = f"Violations: {'; '.join(violations)}"
    else:
        reason = f"Max loss ${max_loss:,.0f} within {trader_config.options_risk_cap_pct:.0%} risk cap"

    prompt = f"""Write one sentence explaining this options risk decision for trader {trader_config.name}.
Decision: {decision}
Strategy: {strategy.replace('_', ' ')}
Contracts: {contracts}
{reason}
One sentence, under 25 words, no preamble."""

    system = persona if persona else "You are a concise options risk manager."

    try:
        response = _client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=60,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception:
        return f"{decision}: {strategy.replace('_', ' ')} with {contracts} contract(s), max loss ${max_loss:,.0f}."


def assess_options_risk(
    ticker: str,
    strategy: str,
    strikes: dict,
    greeks: dict,
    thesis: dict,
    trader_config: TraderConfig,
    portfolio_value: float,
    existing_options_exposure: float = 0.0,
    days_to_expiration: int = 30,
    persona: str = "",
) -> dict:
    """
    Main entry point. Evaluates a proposed options position.

    Returns:
        ticker: str
        decision: "APPROVE" | "REJECT"
        contracts: int (0 if rejected)
        max_loss_total: float
        max_loss_pct_portfolio: float
        greeks_violations: list[str]
        risk_tier: str
        explanation: str
    """
    if strategy == "skip":
        return {
            "ticker": ticker,
            "decision": "REJECT",
            "contracts": 0,
            "max_loss_total": 0.0,
            "max_loss_pct_portfolio": 0.0,
            "greeks_violations": ["Strategy is skip — no position to evaluate."],
            "risk_tier": "N/A",
            "explanation": "No options strategy was selected upstream.",
        }

    risk_tier = _get_risk_tier(trader_config)
    thresholds = THRESHOLDS[risk_tier]
    conviction = thesis.get("conviction", 0.5)

    # Step 1: determine contracts
    contracts = _compute_contracts(
        strategy, strikes, greeks, portfolio_value,
        trader_config, conviction, existing_options_exposure
    )

    if contracts == 0:
        return {
            "ticker": ticker,
            "decision": "REJECT",
            "contracts": 0,
            "max_loss_total": 0.0,
            "max_loss_pct_portfolio": 0.0,
            "greeks_violations": ["Options risk cap fully utilized — no capacity for new positions."],
            "risk_tier": risk_tier,
            "explanation": f"{trader_config.name} has reached their options risk cap of {trader_config.options_risk_cap_pct:.0%}.",
        }

    # Step 2: check DTE
    dte_violations = []
    if days_to_expiration < thresholds["min_days_to_expiration"]:
        dte_violations.append(
            f"DTE {days_to_expiration} below minimum {thresholds['min_days_to_expiration']} for {risk_tier} profile"
        )

    # Step 3: check Greeks
    greeks_violations = _check_greeks_thresholds(greeks, strategy, thresholds, contracts)
    all_violations = dte_violations + greeks_violations

    # Step 4: compute final max loss
    max_loss = _compute_max_loss(strategy, strikes, greeks, contracts)
    max_loss_pct = max_loss / portfolio_value if portfolio_value > 0 else 0.0

    # Step 5: decide
    decision = "REJECT" if all_violations else "APPROVE"

    explanation = _generate_risk_explanation(
        decision, strategy, contracts, max_loss, all_violations, trader_config, persona
    )

    return {
        "ticker": ticker,
        "decision": decision,
        "contracts": contracts,
        "max_loss_total": round(max_loss, 2),
        "max_loss_pct_portfolio": round(max_loss_pct, 4),
        "greeks_violations": all_violations,
        "risk_tier": risk_tier,
        "explanation": explanation,
    }
