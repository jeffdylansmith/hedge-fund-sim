import anthropic
import json
import os
import re
from dotenv import load_dotenv

load_dotenv()

_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

_SYSTEM = """You are a disciplined portfolio manager with 20 years of experience running a \
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

For each ticker in the provided watchlist, output exactly one decision.
Position sizing rules:
  - BUY: max 15% of $100,000 total capital per position = max $15,000 per trade.
    Calculate shares as floor(15000 / current_price).
  - SELL: sell all shares currently held.
  - HOLD: shares = 0.

Respond ONLY with a valid JSON array — no preamble, no markdown, no explanation.
Each element must have exactly these five keys:
  "ticker": string (uppercase)
  "action": "BUY", "SELL", or "HOLD"
  "shares": integer (0 for HOLD)
  "reasoning": brief string (one sentence)
  "confidence": float between 0.0 and 1.0
"""


def decide_portfolio(
    news_summary: dict,
    tech_signals: dict,
    positions: list,
    current_prices: dict,
    watchlist: list,
    trader_persona: str = "",
) -> list:
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

Watchlist: {', '.join(watchlist)}

Return a JSON array with one decision object per ticker in the watchlist."""

    effective_system = f"{trader_persona}\n\n{_SYSTEM}" if trader_persona else _SYSTEM

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
