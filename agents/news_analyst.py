import anthropic
import json
import os
import re
import sys
from dotenv import load_dotenv

load_dotenv()

_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

_SYSTEM = """You are a seasoned financial news analyst with 15 years of experience at \
major investment banks. You cut through noise and identify which headlines actually move \
markets. You are skeptical of hype, attentive to Fed language, and always consider \
second-order effects.

Respond ONLY with a valid JSON object — no preamble, no markdown, no explanation.
The object must have exactly these two keys:
  "summary": a 2-3 sentence market summary identifying the top themes and their likely impact
  "sentiment": one of exactly "bullish", "bearish", or "neutral"
"""


def analyze_news(news_items: list, persona: str = "") -> dict:
    if not news_items:
        return {"summary": "No news data available.", "sentiment": "neutral"}

    system = (persona + "\n\n" + _SYSTEM) if persona else _SYSTEM

    lines = [
        f"[{item['published_at']}] {item['headline']} (Source: {item['source']})"
        for item in news_items
    ]

    message = _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        system=system,
        messages=[{"role": "user", "content": "Analyze these market headlines:\n\n" + "\n".join(lines)}],
    )

    raw = message.content[0].text.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    clean = re.sub(r"^```json\s*|^```\s*|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError as e:
        print(f"news_analyst: JSON parse failed: {e}\nRaw response: {raw[:200]}", file=sys.stderr)
        return {"summary": "Analysis unavailable due to parse error.", "sentiment": "neutral"}
