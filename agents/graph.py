import json
from datetime import datetime, timezone
from typing import TypedDict, List, Dict
from langgraph.graph import StateGraph, START, END
from utils.db import supabase
from agents.news_analyst import analyze_news
from agents.technical_analyst import analyze_technicals
from agents.portfolio_manager import decide_portfolio


class HedgeFundState(TypedDict):
    ticker: str                    # entry-point ticker passed at invocation
    trader_id: str                 # used by vp_check to look up fund_balance
    watchlist: List[str]           # tickers fetched from watchlist table
    prices: List[Dict]             # all rows from prices table (all tickers)
    news_items: List[Dict]         # all rows from news_items table
    positions: List[Dict]          # current rows from portfolio_positions table
    current_prices: Dict           # {ticker: float} latest close per ticker
    news_summary: Dict             # {summary: str, sentiment: bullish|bearish|neutral}
    tech_signals: Dict             # {rsi: {ticker: float}, macd: {ticker: str}, trend: {ticker: str}, signals: str}
    trade_proposal: List[Dict]     # [{ticker, action, shares, reasoning, confidence}]
    vp_verdict: str                # "execute_trade" or "human_review"
    errors: List[str]              # accumulated non-fatal errors from any node


def fetch_data(state: HedgeFundState) -> dict:
    watchlist_rows = supabase.table("watchlist").select("ticker").execute()
    tickers = [row["ticker"] for row in watchlist_rows.data]

    all_prices = []
    current_prices = {}
    for ticker in tickers:
        rows = (
            supabase.table("prices")
            .select("*")
            .eq("ticker", ticker)
            .order("timestamp", desc=True)
            .limit(48)
            .execute()
        )
        if rows.data:
            current_prices[ticker] = float(rows.data[0]["close"])
        all_prices.extend(rows.data)

    news_rows = (
        supabase.table("news_items")
        .select("headline, source, published_at")
        .order("published_at", desc=True)
        .limit(20)
        .execute()
    )

    positions_rows = supabase.table("portfolio_positions").select("*").execute()

    return {
        "watchlist": tickers,
        "prices": all_prices,
        "current_prices": current_prices,
        "news_items": news_rows.data,
        "positions": positions_rows.data,
    }


def news_analyst_node(state: HedgeFundState) -> dict:
    errors = list(state.get("errors", []))
    try:
        result = analyze_news(state["news_items"])
    except Exception as e:
        errors.append(f"news_analyst failed: {e}")
        return {"news_summary": {}, "errors": errors}

    if "summary" not in result or "sentiment" not in result:
        errors.append(f"news_analyst returned invalid keys: {list(result.keys())}")
        return {"news_summary": {}, "errors": errors}

    supabase.table("agent_decisions").insert({
        "agent_name": "News Analyst",
        "ticker": None,
        "action": "analyze",
        "reasoning": result["summary"],
        "confidence": None,
    }).execute()

    return {"news_summary": result, "errors": errors}


def technical_analyst_node(state: HedgeFundState) -> dict:
    errors = list(state.get("errors", []))
    try:
        result = analyze_technicals(state["prices"], state["watchlist"])
    except Exception as e:
        errors.append(f"technical_analyst failed: {e}")
        return {"tech_signals": {}, "errors": errors}

    if not all(k in result for k in ("rsi", "macd", "trend", "signals")):
        errors.append(f"technical_analyst returned invalid keys: {list(result.keys())}")
        return {"tech_signals": {}, "errors": errors}

    supabase.table("agent_decisions").insert({
        "agent_name": "Technical Analyst",
        "ticker": None,
        "action": "analyze",
        "reasoning": result["signals"],
        "confidence": None,
    }).execute()

    return {"tech_signals": result, "errors": errors}


_TRADE_KEYS = {"ticker", "action", "shares", "reasoning", "confidence"}


def portfolio_manager_node(state: HedgeFundState) -> dict:
    errors = list(state.get("errors", []))
    try:
        result = decide_portfolio(
            news_summary=state["news_summary"],
            tech_signals=state["tech_signals"],
            positions=state["positions"],
            current_prices=state["current_prices"],
            watchlist=state["watchlist"],
        )
    except Exception as e:
        errors.append(f"portfolio_manager failed: {e}")
        return {"trade_proposal": [], "errors": errors}

    valid = []
    for trade in result:
        if not isinstance(trade, dict) or not _TRADE_KEYS.issubset(trade.keys()):
            errors.append(f"portfolio_manager: invalid trade object: {trade}")
            continue
        valid.append({
            "ticker": str(trade["ticker"]).upper(),
            "action": str(trade["action"]).upper(),
            "shares": int(trade["shares"]) if trade["shares"] else 0,
            "reasoning": str(trade["reasoning"]),
            "confidence": float(trade["confidence"]),
        })

    supabase.table("agent_decisions").insert({
        "agent_name": "Portfolio Manager",
        "ticker": None,
        "action": "portfolio_review",
        "reasoning": json.dumps(valid),
        "confidence": None,
    }).execute()

    return {"trade_proposal": valid, "errors": errors}


def vp_check_node(state: HedgeFundState) -> dict:
    errors = list(state.get("errors", []))

    if not state.get("trade_proposal"):
        errors.append("vp_check: trade_proposal is empty, routing to human_review")
        return {"vp_verdict": "human_review", "errors": errors}

    balance_rows = (
        supabase.table("fund_balance")
        .select("cash")
        .limit(1)
        .execute()
    )
    if not balance_rows.data:
        errors.append("vp_check: fund_balance table is empty, routing to human_review")
        return {"vp_verdict": "human_review", "errors": errors}

    capital = float(balance_rows.data[0]["cash"])
    threshold = capital * 0.5

    for trade in state["trade_proposal"]:
        price = state["current_prices"].get(trade["ticker"], 0.0)
        notional = trade["shares"] * price
        if notional > threshold:
            errors.append(
                f"vp_check: {trade['ticker']} notional ${notional:,.2f} exceeds 50% threshold ${threshold:,.2f}"
            )
            return {"vp_verdict": "human_review", "errors": errors}

    return {"vp_verdict": "execute_trade", "errors": errors}


def route_after_vp(state: HedgeFundState) -> str:
    verdict = state.get("vp_verdict", "")
    return verdict if verdict in ("execute_trade", "human_review") else "human_review"


def execute_trade_node(state: HedgeFundState) -> dict:
    errors = list(state.get("errors", []))
    positions_by_ticker = {p["ticker"]: p for p in state.get("positions", [])}
    now = datetime.now(timezone.utc).isoformat()

    for trade in state.get("trade_proposal", []):
        if trade["action"] == "HOLD":
            continue

        ticker = trade["ticker"]
        action = trade["action"]
        shares = trade["shares"]
        price = state["current_prices"].get(ticker, 0.0)

        if not shares or not price:
            errors.append(f"execute_trade: missing shares or price for {ticker}, skipping")
            continue

        total_value = shares * price

        supabase.table("trades").insert({
            "ticker": ticker,
            "action": action.lower(),
            "shares": shares,
            "price": price,
            "total_value": total_value,
            "executed_at": now,
        }).execute()

        existing = positions_by_ticker.get(ticker)
        if existing:
            if action == "BUY":
                new_shares = existing["shares"] + shares
                new_avg_cost = (
                    (existing["shares"] * existing["avg_cost"]) + total_value
                ) / new_shares
                supabase.table("portfolio_positions").update({
                    "shares": new_shares,
                    "avg_cost": new_avg_cost,
                    "last_updated": now,
                }).eq("ticker", ticker).execute()
            elif action == "SELL":
                new_shares = existing["shares"] - shares
                if new_shares <= 0:
                    supabase.table("portfolio_positions").delete().eq("ticker", ticker).execute()
                else:
                    supabase.table("portfolio_positions").update({
                        "shares": new_shares,
                        "last_updated": now,
                    }).eq("ticker", ticker).execute()
        else:
            if action == "BUY":
                supabase.table("portfolio_positions").insert({
                    "ticker": ticker,
                    "shares": shares,
                    "avg_cost": price,
                    "last_updated": now,
                }).execute()

    return {"errors": errors}


def human_review_node(state: HedgeFundState) -> dict:
    errors = list(state.get("errors", []))

    for trade in state.get("trade_proposal", []):
        if trade["action"] == "HOLD":
            continue

        ticker = trade["ticker"]
        price = state["current_prices"].get(ticker)

        supabase.table("pending_decisions").insert({
            "agent_name": "Portfolio Manager",
            "ticker": ticker,
            "action": trade["action"],
            "shares": trade["shares"],
            "price_at_decision": price,
            "reasoning": trade.get("reasoning", "")[:500],
            "status": "pending",
        }).execute()

    return {"errors": errors}


_builder = StateGraph(HedgeFundState)

_builder.add_node("fetch_data", fetch_data)
_builder.add_node("news_analyst_node", news_analyst_node)
_builder.add_node("technical_analyst_node", technical_analyst_node)
_builder.add_node("portfolio_manager_node", portfolio_manager_node)
_builder.add_node("vp_check_node", vp_check_node)
_builder.add_node("execute_trade_node", execute_trade_node)
_builder.add_node("human_review_node", human_review_node)

_builder.add_edge(START, "fetch_data")
_builder.add_edge("fetch_data", "news_analyst_node")
_builder.add_edge("news_analyst_node", "technical_analyst_node")
_builder.add_edge("technical_analyst_node", "portfolio_manager_node")
_builder.add_edge("portfolio_manager_node", "vp_check_node")
_builder.add_conditional_edges(
    "vp_check_node",
    route_after_vp,
    {
        "execute_trade": "execute_trade_node",
        "human_review": "human_review_node",
    },
)
_builder.add_edge("execute_trade_node", END)
_builder.add_edge("human_review_node", END)

graph = _builder.compile()
