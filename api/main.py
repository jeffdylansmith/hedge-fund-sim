from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client
from dotenv import load_dotenv
from datetime import datetime, timezone
import os

load_dotenv()

app = FastAPI(title="Hedge Fund Sim API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

STARTING_CASH = 33_333.0
TRADER_IDS = ["alex", "jordan", "casey"]


@app.get("/")
def root():
    return {"status": "running", "fund": "Meridian Capital (Simulated)"}


@app.get("/prices/{ticker}")
def get_prices(ticker: str, limit: int = 48):
    result = (
        supabase.table("prices")
        .select("*")
        .eq("ticker", ticker.upper())
        .order("timestamp", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data


@app.get("/news")
def get_news(limit: int = 20):
    result = (
        supabase.table("news_items")
        .select("*")
        .order("published_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data


@app.get("/portfolio")
def get_portfolio():
    positions = supabase.table("portfolio_positions").select("*").execute()
    trades = (
        supabase.table("trades")
        .select("*")
        .order("executed_at", desc=True)
        .limit(10)
        .execute()
    )
    return {"positions": positions.data, "recent_trades": trades.data}


@app.get("/fund/summary")
def get_fund_summary():
    # Cash balances
    balances = supabase.table("fund_balance").select("trader_id, cash").execute()
    balance_map = {row["trader_id"]: float(row["cash"]) for row in balances.data}
    total_cash = sum(balance_map.get(t, STARTING_CASH) for t in TRADER_IDS)

    # Estimate position values using latest prices
    positions = supabase.table("portfolio_positions").select("ticker, shares, trader_id").execute()
    tickers = list({p["ticker"] for p in positions.data})
    current_prices = {}
    for ticker in tickers:
        rows = (
            supabase.table("prices")
            .select("close")
            .eq("ticker", ticker)
            .order("timestamp", desc=True)
            .limit(1)
            .execute()
        )
        if rows.data:
            current_prices[ticker] = float(rows.data[0]["close"])

    position_value = sum(
        p["shares"] * current_prices.get(p["ticker"], 0.0)
        for p in positions.data
    )
    total_fund_value = total_cash + position_value

    # Total trades
    trades = supabase.table("trades").select("id", count="exact").execute()
    total_trades = trades.count or 0

    # Decisions today
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    decisions_today = (
        supabase.table("agent_decisions")
        .select("id", count="exact")
        .gte("created_at", today_start)
        .execute()
    )
    decisions_today_count = decisions_today.count or 0

    # Best performer by P&L (cash vs starting)
    best_trader = None
    best_pnl = None
    for trader_id in TRADER_IDS:
        cash = balance_map.get(trader_id, STARTING_CASH)
        trader_positions = [p for p in positions.data if p["trader_id"] == trader_id]
        pos_val = sum(p["shares"] * current_prices.get(p["ticker"], 0.0) for p in trader_positions)
        pnl = (cash + pos_val) - STARTING_CASH
        if best_pnl is None or pnl > best_pnl:
            best_pnl = pnl
            best_trader = trader_id

    return {
        "total_fund_value": round(total_fund_value, 2),
        "total_trades": total_trades,
        "decisions_today": decisions_today_count,
        "best_performer": best_trader,
        "best_performer_pnl": round(best_pnl, 2) if best_pnl is not None else 0.0,
    }


@app.get("/traders")
def get_traders():
    balances = supabase.table("fund_balance").select("trader_id, cash").execute()
    balance_map = {row["trader_id"]: float(row["cash"]) for row in balances.data}

    positions = supabase.table("portfolio_positions").select("ticker, shares, trader_id").execute()
    tickers = list({p["ticker"] for p in positions.data})
    current_prices = {}
    for ticker in tickers:
        rows = (
            supabase.table("prices")
            .select("close")
            .eq("ticker", ticker)
            .order("timestamp", desc=True)
            .limit(1)
            .execute()
        )
        if rows.data:
            current_prices[ticker] = float(rows.data[0]["close"])

    result = []
    for trader_id in TRADER_IDS:
        cash = balance_map.get(trader_id, STARTING_CASH)
        trader_positions = [p for p in positions.data if p["trader_id"] == trader_id]
        pos_val = sum(p["shares"] * current_prices.get(p["ticker"], 0.0) for p in trader_positions)
        pnl = (cash + pos_val) - STARTING_CASH

        recent_decision = (
            supabase.table("agent_decisions")
            .select("agent_name, ticker, action, reasoning, confidence, created_at")
            .eq("trader_id", trader_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        result.append({
            "trader_id": trader_id,
            "cash": round(cash, 2),
            "position_count": len(trader_positions),
            "position_value": round(pos_val, 2),
            "total_value": round(cash + pos_val, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round((pnl / STARTING_CASH) * 100, 2),
            "recent_decision": recent_decision.data[0] if recent_decision.data else None,
        })

    return result


@app.get("/feed")
def get_feed(limit: int = 20):
    result = (
        supabase.table("agent_decisions")
        .select("id, trader_id, agent_name, ticker, action, reasoning, confidence, created_at")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data


@app.get("/positions")
def get_positions():
    result = supabase.table("portfolio_positions").select("*").execute()
    grouped: dict = {}
    for pos in result.data:
        tid = pos["trader_id"]
        if tid not in grouped:
            grouped[tid] = []
        grouped[tid].append(pos)
    return grouped


@app.get("/pending")
def get_pending():
    result = (
        supabase.table("pending_decisions")
        .select("*")
        .eq("status", "pending")
        .order("created_at", desc=True)
        .execute()
    )
    return result.data


@app.post("/approve/{decision_id}")
def approve_decision(decision_id: int):
    row = (
        supabase.table("pending_decisions")
        .select("*")
        .eq("id", decision_id)
        .eq("status", "pending")
        .execute()
    )
    if not row.data:
        raise HTTPException(status_code=404, detail="Pending decision not found")

    decision = row.data[0]
    ticker = decision["ticker"]
    action = decision["action"]
    shares = decision["shares"]
    price = decision["price_at_decision"]
    trader_id = decision["trader_id"]

    if not shares or not price:
        raise HTTPException(status_code=400, detail="Missing shares or price — cannot execute")

    total_value = shares * price
    now = datetime.now(timezone.utc).isoformat()

    supabase.table("trades").insert({
        "ticker": ticker,
        "action": action.lower(),
        "shares": shares,
        "price": price,
        "total_value": total_value,
        "executed_at": now,
        "trader_id": trader_id,
    }).execute()

    existing = (
        supabase.table("portfolio_positions")
        .select("*")
        .eq("ticker", ticker)
        .eq("trader_id", trader_id)
        .execute()
    )

    if existing.data:
        current = existing.data[0]
        if action == "BUY":
            new_shares = current["shares"] + shares
            new_avg = ((current["shares"] * current["avg_cost"]) + total_value) / new_shares
            supabase.table("portfolio_positions").update({
                "shares": new_shares,
                "avg_cost": new_avg,
                "last_updated": now,
            }).eq("ticker", ticker).eq("trader_id", trader_id).execute()
        elif action == "SELL":
            new_shares = current["shares"] - shares
            if new_shares <= 0:
                supabase.table("portfolio_positions").delete().eq("ticker", ticker).eq("trader_id", trader_id).execute()
            else:
                supabase.table("portfolio_positions").update({
                    "shares": new_shares,
                    "last_updated": now,
                }).eq("ticker", ticker).eq("trader_id", trader_id).execute()
    elif action == "BUY":
        supabase.table("portfolio_positions").insert({
            "ticker": ticker,
            "shares": shares,
            "avg_cost": price,
            "last_updated": now,
            "trader_id": trader_id,
        }).execute()

    supabase.table("pending_decisions").update({
        "status": "approved",
        "reviewed_at": now,
    }).eq("id", decision_id).execute()

    return {"status": "approved", "decision_id": decision_id, "trade": f"{action} {shares} {ticker} @ ${price}"}


@app.post("/reject/{decision_id}")
def reject_decision(decision_id: int):
    row = (
        supabase.table("pending_decisions")
        .select("id")
        .eq("id", decision_id)
        .eq("status", "pending")
        .execute()
    )
    if not row.data:
        raise HTTPException(status_code=404, detail="Pending decision not found")

    now = datetime.now(timezone.utc).isoformat()
    supabase.table("pending_decisions").update({
        "status": "rejected",
        "reviewed_at": now,
    }).eq("id", decision_id).execute()

    return {"status": "rejected", "decision_id": decision_id}
