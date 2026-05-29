import anthropic
import json
import os
import re
from dotenv import load_dotenv

load_dotenv()

_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

_SYSTEM = """You are a risk manager at a hedge fund. Your job is to evaluate a proposed trade \
against the current portfolio and market conditions and flag concerns before execution.

You will receive:
  - The proposed trades (list of objects with ticker, action, shares, reasoning, confidence)
  - Current portfolio positions (list of objects with ticker, shares, avg_cost)
  - Available cash
  - A pre-computed concentration_pct: the proposed trade value as a percentage of total portfolio \
value (cash + all position market values). Treat this as a given fact.
  - News summary (summary string and overall sentiment)
  - Technical signals (rsi, macd, trend, signals)

Evaluate the overall risk of executing the proposal. Identify specific concerns such as:
  - High portfolio concentration in a single position
  - Buying into overbought conditions (RSI > 70)
  - Selling into oversold conditions (RSI < 30)
  - Trade direction contradicting news sentiment or technical signals
  - Insufficient cash reserve after the trade

Respond ONLY with a valid JSON object — no preamble, no markdown, no explanation.
The object must have exactly these four keys:
  "risk_score": integer from 1 to 10 (1 = minimal risk, 10 = maximum risk)
  "flags": list of strings, each a specific risk concern (empty list if none)
  "recommendation": one of exactly "proceed", "caution", or "veto"
  "rationale": 2-3 sentence explanation of the overall risk assessment
"""


def assess_risk(
    trade_proposal: list,
    positions: list,
    cash: float,
    news_summary: dict,
    tech_signals: dict,
    trader_config,
    current_prices: dict = None,
    persona: str = "",
) -> dict:
    if current_prices is None:
        current_prices = {}

    position_value = sum(p["shares"] * p["avg_cost"] for p in positions)
    total_portfolio_value = cash + position_value

    positions_by_ticker = {p["ticker"]: p for p in positions}
    trade_value = 0.0
    for t in trade_proposal:
        if t.get("action") != "BUY":
            continue
        ticker = t["ticker"]
        existing = positions_by_ticker.get(ticker)
        price = existing["avg_cost"] if existing else current_prices.get(ticker, 0.0)
        trade_value += t.get("shares", 0) * price

    concentration_pct = (
        (trade_value / total_portfolio_value * 100) if total_portfolio_value > 0 else 0.0
    )

    positions_text = (
        "\n".join(
            f"  {p['ticker']}: {p['shares']} shares @ avg ${p['avg_cost']:.2f}"
            for p in positions
        )
        or "  No current positions."
    )

    user_content = f"""Proposed Trades:
{json.dumps(trade_proposal, indent=2)}

Current Positions:
{positions_text}

Available Cash: ${cash:,.2f}
Total Portfolio Value: ${total_portfolio_value:,.2f}
Concentration of Proposed Buy(s): {concentration_pct:.1f}% of total portfolio

News Summary:
{json.dumps(news_summary, indent=2)}

Technical Signals:
{json.dumps(tech_signals, indent=2)}

Evaluate the risk of executing this trade proposal and return a JSON object."""

    system = (persona + "\n\n" + _SYSTEM) if persona else _SYSTEM

    msg = _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        system=system,
        messages=[{"role": "user", "content": user_content}],
    )
    raw = msg.content[0].text.strip()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        clean = re.sub(r"^```json\s*|^```\s*|```$", "", raw, flags=re.MULTILINE).strip()
        result = json.loads(clean)

    result["concentration_pct"] = round(concentration_pct, 2)
    return result
