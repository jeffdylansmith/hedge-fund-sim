import json
import os
from datetime import datetime, timezone
from typing import TypedDict, List, Dict
from langgraph.graph import StateGraph, START, END
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from utils.db import supabase
from agents.news_analyst import analyze_news
from agents.technical_analyst import analyze_technicals
from agents.portfolio_manager import decide_portfolio
from agents.risk_manager import assess_risk
from agents.trader_config import TraderConfig, get_trader

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
alpaca_client = (
    TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)
    if ALPACA_API_KEY else None
)


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
    vp_threshold: float            # fraction of capital that triggers human review (from TraderConfig)
    trader_persona: str            # prepended to PM system prompt (from TraderConfig)
    news_analyst_persona: str      # prepended to news analyst system prompt (from TraderConfig)
    tech_analyst_persona: str      # prepended to technical analyst system prompt (from TraderConfig)
    risk_assessment: Dict          # {risk_score: int 1-10, concentration_pct: float, flags: list[str], recommendation: str, rationale: str}
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

    positions_rows = (
        supabase.table("portfolio_positions")
        .select("*")
        .eq("trader_id", state["trader_id"])
        .execute()
    )

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
        result = analyze_news(state["news_items"], persona=state.get("news_analyst_persona", ""))
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
        "trader_id": state["trader_id"],
    }).execute()

    return {"news_summary": result, "errors": errors}


def technical_analyst_node(state: HedgeFundState) -> dict:
    errors = list(state.get("errors", []))
    try:
        result = analyze_technicals(state["prices"], state["watchlist"], persona=state.get("tech_analyst_persona", ""))
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
        "trader_id": state["trader_id"],
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
            trader_persona=state.get("trader_persona", ""),
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
        "trader_id": state["trader_id"],
    }).execute()

    return {"trade_proposal": valid, "errors": errors}


def risk_manager_node(state: HedgeFundState) -> dict:
    errors = list(state.get("errors", []))
    try:
        balance_rows = (
            supabase.table("fund_balance")
            .select("cash")
            .eq("trader_id", state["trader_id"])
            .limit(1)
            .execute()
        )
        if not balance_rows.data:
            errors.append(
                f"risk_manager: no fund_balance row for trader_id={state['trader_id']!r}, defaulting to $33,333"
            )
            cash = 333_333.33
        else:
            cash = float(balance_rows.data[0]["cash"])

        trader_config = get_trader(state["trader_id"])
        result = assess_risk(
            trade_proposal=state["trade_proposal"],
            positions=state["positions"],
            cash=cash,
            news_summary=state["news_summary"],
            tech_signals=state["tech_signals"],
            trader_config=trader_config,
            current_prices=state["current_prices"],
            persona=state.get("trader_persona", ""),
        )
    except Exception as e:
        errors.append(f"risk_manager failed: {e}")
        return {"risk_assessment": {}, "errors": errors}

    supabase.table("agent_decisions").insert({
        "agent_name": "risk_manager",
        "ticker": None,
        "action": "risk_assessment",
        "reasoning": result.get("rationale", ""),
        "confidence": None,
        "trader_id": state["trader_id"],
    }).execute()

    return {"risk_assessment": result, "errors": errors}


def vp_check_node(state: HedgeFundState) -> dict:
    errors = list(state.get("errors", []))

    if not state.get("trade_proposal"):
        errors.append("vp_check: trade_proposal is empty, routing to human_review")
        return {"vp_verdict": "human_review", "errors": errors}

    balance_rows = (
        supabase.table("fund_balance")
        .select("cash")
        .eq("trader_id", state["trader_id"])
        .limit(1)
        .execute()
    )
    if not balance_rows.data:
        errors.append(
            f"vp_check: no fund_balance row for trader_id={state['trader_id']!r}, defaulting to $33,333"
        )
        capital = 333_333.33
    else:
        capital = float(balance_rows.data[0]["cash"])

    threshold = capital * state["vp_threshold"]

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
    trader_id = state["trader_id"]
    positions_by_ticker = {p["ticker"]: p for p in state.get("positions", [])}
    now = datetime.now(timezone.utc).isoformat()

    balance_rows = (
        supabase.table("fund_balance")
        .select("cash")
        .eq("trader_id", trader_id)
        .limit(1)
        .execute()
    )
    if not balance_rows.data:
        errors.append(
            f"execute_trade: no fund_balance row for trader_id={trader_id!r}, defaulting to $33,333"
        )
        cash = 333_333.33
    else:
        cash = float(balance_rows.data[0]["cash"])

    cash_delta = 0.0

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

        if action == "BUY":
            available = cash + cash_delta
            if total_value > available:
                errors.append(
                    f"execute_trade: insufficient funds for {ticker} — "
                    f"need ${total_value:,.2f}, have ${available:,.2f}, skipping"
                )
                continue

        trade_result = supabase.table("trades").insert({
            "ticker": ticker,
            "action": action.lower(),
            "shares": shares,
            "price": price,
            "total_value": total_value,
            "executed_at": now,
            "trader_id": trader_id,
        }).execute()
        trade_id = trade_result.data[0]["id"] if trade_result.data else None

        if alpaca_client:
            try:
                order = MarketOrderRequest(
                    symbol=ticker,
                    qty=shares,
                    side=OrderSide.BUY if action == "BUY" else OrderSide.SELL,
                    time_in_force=TimeInForce.DAY,
                )
                alpaca_client.submit_order(order)
                if trade_id:
                    supabase.table("trades").update(
                        {"alpaca_submitted": True}
                    ).eq("id", trade_id).execute()
            except Exception as e:
                errors.append(f"Alpaca order failed for {ticker}: {e}")

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
                }).eq("ticker", ticker).eq("trader_id", trader_id).execute()
            elif action == "SELL":
                new_shares = existing["shares"] - shares
                if new_shares <= 0:
                    supabase.table("portfolio_positions").delete().eq("ticker", ticker).eq("trader_id", trader_id).execute()
                else:
                    supabase.table("portfolio_positions").update({
                        "shares": new_shares,
                        "last_updated": now,
                    }).eq("ticker", ticker).eq("trader_id", trader_id).execute()
        else:
            if action == "BUY":
                supabase.table("portfolio_positions").insert({
                    "ticker": ticker,
                    "shares": shares,
                    "avg_cost": price,
                    "last_updated": now,
                    "trader_id": trader_id,
                }).execute()

        if action == "BUY":
            cash_delta -= total_value
        elif action == "SELL":
            cash_delta += total_value

    if cash_delta != 0.0:
        supabase.table("fund_balance").update(
            {"cash": cash + cash_delta}
        ).eq("trader_id", trader_id).execute()

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
            "trader_id": state["trader_id"],
        }).execute()

    return {"errors": errors}


def run_all_traders() -> None:
    from agents.trader_config import TRADERS
    from datetime import datetime

    print(f"\n{'='*60}")
    print(f"HEDGE FUND SIM — Multi-Trader Run")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    summary = []

    for config in TRADERS:
        print(f"--- {config.name} ({config.trader_id}) ---")
        try:
            result = run_graph(config)
            non_hold = [t for t in result["trade_proposal"] if t["action"] != "HOLD"]
            status = "OK"
            detail = (
                f"verdict={result['vp_verdict']}  "
                f"trades={len(result['trade_proposal'])}  "
                f"non-hold={len(non_hold)}  "
                f"errors={len(result['errors'])}"
            )
            if result["errors"]:
                print(f"  Errors: {result['errors']}")
        except Exception as exc:
            status = "FAILED"
            detail = str(exc)
            print(f"  EXCEPTION: {exc}")

        print(f"  {detail}\n")
        summary.append((config.name, status, detail))

    print(f"{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    for name, status, detail in summary:
        print(f"  {name:8s}  [{status}]  {detail}")
    print(f"{'='*60}\n")


def run_graph(config: TraderConfig) -> dict:
    persona = (
        f"You are trading on behalf of {config.name}, a {config.personality}. "
        f"Your risk tolerance is {config.risk_tolerance}."
    )
    initial_state: HedgeFundState = {
        "ticker": "",
        "trader_id": config.trader_id,
        "vp_threshold": config.vp_threshold,
        "trader_persona": persona,
        "news_analyst_persona": config.news_analyst_persona,
        "tech_analyst_persona": config.tech_analyst_persona,
        "watchlist": [],
        "prices": [],
        "current_prices": {},
        "news_items": [],
        "positions": [],
        "news_summary": {},
        "tech_signals": {},
        "trade_proposal": [],
        "vp_verdict": "",
        "risk_assessment": {},
        "errors": [],
    }
    return graph.invoke(initial_state)


_builder = StateGraph(HedgeFundState)

_builder.add_node("fetch_data", fetch_data)
_builder.add_node("news_analyst_node", news_analyst_node)
_builder.add_node("technical_analyst_node", technical_analyst_node)
_builder.add_node("portfolio_manager_node", portfolio_manager_node)
_builder.add_node("risk_manager_node", risk_manager_node)
_builder.add_node("vp_check_node", vp_check_node)
_builder.add_node("execute_trade_node", execute_trade_node)
_builder.add_node("human_review_node", human_review_node)

_builder.add_edge(START, "fetch_data")
_builder.add_edge("fetch_data", "news_analyst_node")
_builder.add_edge("news_analyst_node", "technical_analyst_node")
_builder.add_edge("technical_analyst_node", "portfolio_manager_node")
_builder.add_edge("portfolio_manager_node", "risk_manager_node")
_builder.add_edge("risk_manager_node", "vp_check_node")
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


if __name__ == "__main__":
    run_all_traders()
