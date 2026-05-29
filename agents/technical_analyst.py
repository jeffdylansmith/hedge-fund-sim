import anthropic
import json
import os
import re
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

_SYSTEM = """You are a quantitative technical analyst. You receive pre-computed indicator \
data for each ticker and identify trading signals.

Respond ONLY with a valid JSON object — no preamble, no markdown, no explanation.
The object must have exactly these four keys:
  "rsi": object mapping each ticker to its RSI(14) value as a float
  "macd": object mapping each ticker to "bullish" or "bearish" based on the MACD cross
  "trend": object mapping each ticker to a short description of price vs SMAs \
(e.g. "above both SMAs", "below SMA20", "below both SMAs")
  "signals": a 2-3 sentence summary of the strongest actionable signals across all tickers
"""


def _compute_indicators(ticker: str, rows: list) -> str:
    if len(rows) < 2:
        return f"\nTicker: {ticker}\nInsufficient data."

    df = pd.DataFrame(rows)
    df = df.sort_values("timestamp")
    df["close"] = pd.to_numeric(df["close"])
    df["volume"] = pd.to_numeric(df["volume"])

    df["sma_10"] = df["close"].rolling(window=10).mean()
    df["sma_20"] = df["close"].rolling(window=20).mean()

    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(window=14).mean()
    loss = -delta.clip(upper=0).rolling(window=14).mean()
    rs = gain / loss
    df["rsi"] = 100 - (100 / (1 + rs))

    exp12 = df["close"].ewm(span=12, adjust=False).mean()
    exp26 = df["close"].ewm(span=26, adjust=False).mean()
    df["macd"] = exp12 - exp26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()

    latest = df.iloc[-1]
    prev = df.iloc[-2]
    price_change = ((latest["close"] - prev["close"]) / prev["close"]) * 100

    return f"""
Ticker: {ticker}
Current Price: ${latest['close']:.2f}
1-Period Change: {price_change:+.2f}%
Volume: {latest['volume']:,}

--- Indicators ---
SMA 10: ${latest['sma_10']:.2f}  ({'Price ABOVE' if latest['close'] > latest['sma_10'] else 'Price BELOW'} SMA10)
SMA 20: ${latest['sma_20']:.2f}  ({'Price ABOVE' if latest['close'] > latest['sma_20'] else 'Price BELOW'} SMA20)
RSI(14): {latest['rsi']:.1f}     ({'Overbought' if latest['rsi'] > 70 else 'Oversold' if latest['rsi'] < 30 else 'Neutral'})
MACD: {latest['macd']:.4f}
MACD Signal: {latest['macd_signal']:.4f}
MACD Cross: {'Bullish' if latest['macd'] > latest['macd_signal'] else 'Bearish'}
"""


def analyze_technicals(prices: list, watchlist: list, persona: str = "") -> dict:
    prices_by_ticker: dict[str, list] = {}
    for row in prices:
        prices_by_ticker.setdefault(row["ticker"], []).append(row)

    system = (persona + "\n\n" + _SYSTEM) if persona else _SYSTEM

    blocks = [_compute_indicators(ticker, prices_by_ticker.get(ticker, [])) for ticker in watchlist]

    message = _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=system,
        messages=[
            {
                "role": "user",
                "content": "Analyze these technical indicators:\n\n" + "\n".join(blocks),
            }
        ],
    )

    raw = message.content[0].text.strip()
    clean = re.sub(r"^```json\s*|^```\s*|```$", "", raw, flags=re.MULTILINE).strip()
    return json.loads(clean)
