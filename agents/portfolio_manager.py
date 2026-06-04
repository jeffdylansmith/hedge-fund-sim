import anthropic
import json
import os
import re
from dotenv import load_dotenv

load_dotenv()

_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

_SYSTEM_BASE = """You are a disciplined portfolio manager with 20 years of experience running a \
mid-sized hedge fund. You have lived through the 2008 crash and 2020 pandemic. Your number \
one rule is never let a bad position get worse. You think in risk/reward ratios and always \
ask yourself what happens if you are wrong. Never go all-in on a single position, always \
maintain some cash reserve, and be deeply skeptical of consensus trades.

You will receive:
  - A news summary with keys: "summary" (string), "sentiment" ("bullish", "bearish", or "neutral")
  - Technical signals with keys: "rsi" (object mapping ticker → RSI float or null), \
"macd" (object mapping ticker → "bullish" or "bearish"), \
"trend" (object mapping ticker → SMA relationship description), \
"signals" (string summary of strongest signals)
  - Current portfolio positions (list of objects with ticker, shares, avg_cost)
  - Current prices (object mapping ticker → latest close price as float)
  - Available cash

For each ticker in the provided watchlist, output exactly one decision.
{sizing_rules}

Respond ONLY with a valid JSON array — no preamble, no markdown, no explanation.
Each element must have exactly these five keys:
  "ticker": string (uppercase)
  "action": "BUY", "SELL", or "HOLD"
  "shares": integer (0 for HOLD)
  "reasoning": brief string (one sentence)
  "confidence": float between 0.0 and 1.0
"""


_TRADER_INSTRUCTIONS = {
    "alex": "IMPORTANT: You are managing Alex's book. Alex is a momentum trader. RSI above 75 + bullish MACD = BUY. Do not propose all HOLDs. Deploy capital.",
    "jordan": (
        "IMPORTANT: You are managing Jordan's book. Jordan is a conservative macro investor — capital preservation comes before returns. "
        "Do NOT buy unless you have a clear macro thesis: the news sentiment, technical trend, and fundamentals must all point the same direction. "
        "Momentum alone is never enough. "
        "Cap each BUY at 10% of available cash (not the default 15%) — calculate shares as floor(cash * 0.10 / current_price). "
        "When in doubt, HOLD. Sitting in cash is a valid and often correct decision. "
        "If only one or two signals align, the right answer is almost always HOLD."
    ),
    "casey": (
        "IMPORTANT: You are managing Casey's book. Casey is a contrarian — the crowd is usually wrong at the extremes. "
        "If news sentiment is strongly bullish on a ticker, treat that as a warning sign of an overcrowded trade, not a buy signal. "
        "Strongly bullish sentiment + strong uptrend = likely too late to buy, consider SELL or HOLD. "
        "Conversely, bad news on a stock you watch is worth examining: if the selloff looks overdone relative to the actual impact, that may be a BUY. "
        "Never pile into a ticker just because RSI and MACD are screaming momentum — a strong trend is Casey's cue to ask whether the move is exhausted, not to chase it. "
        "Fade consensus. Look for divergences between sentiment and price action."
    ),
}


def decide_portfolio(
    news_summary: dict,
    tech_signals: dict,
    positions: list,
    current_prices: dict,
    watchlist: list,
    cash: float = 0.0,
    trader_persona: str = "",
    trader_id: str = "",
) -> list:
    max_per_trade = cash * 0.15
    sizing_rules = (
        f"Position sizing rules:\n"
        f"  - BUY: max 15% of ${cash:,.0f} available cash per position = max ${max_per_trade:,.0f} per trade.\n"
        f"    Calculate shares as floor({max_per_trade:,.0f} / current_price).\n"
        f"  - SELL: sell all shares currently held.\n"
        f"  - HOLD: shares = 0."
    )
    trader_instruction = _TRADER_INSTRUCTIONS.get(trader_id, "")
    system = _SYSTEM_BASE.format(sizing_rules=sizing_rules)
    if trader_instruction:
        system = system.rstrip() + f"\n\n{trader_instruction}\n"

    positions_text = (
        "\n".join(
            f"  {p['ticker']}: {p['shares']} shares @ avg ${p['avg_cost']:.2f}"
            for p in positions
        )
        or "  No current positions."
    )
    prices_text = "\n".join(f"  {t}: ${p:.2f}" for t, p in current_prices.items())

    user_content = f"""News Summary:
{json.dumps(news_summary, indent=2)}

Technical Signals:
{json.dumps(tech_signals, indent=2)}

Current Positions:
{positions_text}

Current Prices:
{prices_text}

Available Cash: ${cash:,.2f}

Watchlist: {', '.join(watchlist)}

Return a JSON array with one decision object per ticker in the watchlist."""

    effective_system = f"{trader_persona}\n\n{system}" if trader_persona else system

    msg = _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=effective_system,
        messages=[{"role": "user", "content": user_content}],
    )
    raw = msg.content[0].text.strip()

    # Attempt 1: parse raw response
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Attempt 2: strip markdown fences and retry
    clean = re.sub(r"^```json\s*|^```\s*|```$", "", raw, flags=re.MULTILINE).strip()
    return json.loads(clean)
