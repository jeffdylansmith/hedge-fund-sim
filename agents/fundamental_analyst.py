import anthropic
import json
import os
import re
import sys
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()

_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

_SYSTEM_BASE = (
    "You are a fundamental analyst. You receive pre-computed financial metrics and scores. "
    "Respond ONLY with valid JSON — no markdown, no preamble.\n"
    "Return an object with one key per ticker, each containing:\n"
    '{\n'
    '  "score": the pre-computed score (integer, use exactly as provided),\n'
    '  "verdict": "strong_buy"|"buy"|"hold"|"sell"|"avoid",\n'
    '  "thesis": one sentence max explaining the verdict\n'
    '}'
)


def _compute_score(info: dict) -> int:
    score = 0

    pe = info.get("trailingPE")
    if pe is not None:
        if pe < 15:
            score += 2
        elif pe <= 25:
            score += 1

    rev_growth = info.get("revenueGrowth")
    if rev_growth is not None:
        if rev_growth > 0.15:
            score += 2
        elif rev_growth >= 0.05:
            score += 1

    de = info.get("debtToEquity")
    if de is not None:
        if de < 0.5:
            score += 2
        elif de <= 1.5:
            score += 1

    margin = info.get("profitMargins")
    if margin is not None:
        if margin > 0.20:
            score += 2
        elif margin >= 0.10:
            score += 1

    fcf = info.get("freeCashflow")
    if fcf is not None and fcf > 0:
        score += 2

    return score


def analyze_fundamentals(tickers: list[str], persona: str = "") -> dict:
    metrics_by_ticker = {}

    for ticker in tickers:
        try:
            ticker_obj = yf.Ticker(ticker)
            info = ticker_obj.info
        except Exception:
            info = {}

        metrics_by_ticker[ticker] = {
            "pe_ratio":       info.get("trailingPE"),
            "pb_ratio":       info.get("priceToBook"),
            "debt_to_equity": info.get("debtToEquity"),
            "revenue_growth": info.get("revenueGrowth"),
            "earnings_growth": info.get("earningsGrowth"),
            "free_cash_flow": info.get("freeCashflow"),
            "profit_margin":  info.get("profitMargins"),
            "market_cap":     info.get("marketCap"),
            "sector":         info.get("sector"),
            "forward_pe":     info.get("forwardPE"),
            "score":          _compute_score(info),
        }

    system = (persona + "\n\n" + _SYSTEM_BASE) if persona else _SYSTEM_BASE

    lines = []
    for ticker, m in metrics_by_ticker.items():
        lines.append(
            f"{ticker}: score={m['score']}/10, PE={m['pe_ratio']}, fwd_PE={m['forward_pe']}, "
            f"PB={m['pb_ratio']}, D/E={m['debt_to_equity']}, rev_growth={m['revenue_growth']}, "
            f"earn_growth={m['earnings_growth']}, margin={m['profit_margin']}, "
            f"FCF={m['free_cash_flow']}, mktcap={m['market_cap']}, sector={m['sector']}"
        )

    parsed = None
    try:
        msg = _client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": "\n".join(lines)}],
        )
        raw = msg.content[0].text.strip()

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            clean = re.sub(r"^```json\s*|^```\s*|```$", "", raw, flags=re.MULTILINE).strip()
            try:
                parsed = json.loads(clean)
            except json.JSONDecodeError as e:
                print(f"fundamental_analyst: JSON parse failed: {e}\nRaw: {raw[:200]}", file=sys.stderr)
    except Exception as e:
        print(f"fundamental_analyst: API call failed: {e}", file=sys.stderr)

    result = {}
    for ticker in tickers:
        m = metrics_by_ticker[ticker]
        base = {
            "pe_ratio":       m["pe_ratio"],
            "pb_ratio":       m["pb_ratio"],
            "debt_to_equity": m["debt_to_equity"],
            "revenue_growth": m["revenue_growth"],
            "profit_margin":  m["profit_margin"],
            "free_cash_flow": m["free_cash_flow"],
            "market_cap":     m["market_cap"],
            "sector":         m["sector"],
        }
        if parsed and ticker in parsed:
            llm = parsed[ticker]
            result[ticker] = {
                "score":   m["score"],
                "verdict": llm.get("verdict", "hold"),
                "thesis":  llm.get("thesis", "Analysis unavailable"),
                **base,
            }
        else:
            result[ticker] = {
                "score":   0,
                "verdict": "hold",
                "thesis":  "Analysis unavailable",
                **base,
            }

    return result
