from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from alpaca.trading.client import TradingClient
from supabase import create_client
from dotenv import load_dotenv
from datetime import datetime, timezone
import pytz
import logging
import os

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("meridian")

app = FastAPI(title="Hedge Fund Sim API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "https://hedge-fund-sim-production.up.railway.app",
        "https://hedge-fund-sim.vercel.app",
        "https://*.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

_alpaca_key = os.getenv("ALPACA_API_KEY")
_alpaca_secret = os.getenv("ALPACA_SECRET_KEY")
alpaca_client = (
    TradingClient(_alpaca_key, _alpaca_secret, paper=True)
    if _alpaca_key else None
)

STARTING_CASH = 33_333.0
TRADER_IDS = ["alex", "jordan", "casey"]
ET = pytz.timezone("America/New_York")

# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

scheduler = BackgroundScheduler(timezone=ET)


def _is_paused() -> bool:
    try:
        row = (
            supabase.table("scheduler_config")
            .select("value")
            .eq("key", "paused")
            .limit(1)
            .execute()
        )
        return bool(row.data and row.data[0]["value"] == "true")
    except Exception as e:
        log.warning(f"scheduler: could not read pause state: {e}")
        return False


def _log_run(status: str, error: str | None = None) -> None:
    try:
        supabase.table("scheduler_runs").insert({
            "ran_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "error": error,
        }).execute()
    except Exception as e:
        log.warning(f"scheduler: could not write run log: {e}")


def run_market_session() -> None:
    now_et = datetime.now(ET)
    if now_et.weekday() >= 5:  # Saturday=5, Sunday=6
        log.info("scheduler: skipping — weekend")
        return

    if _is_paused():
        log.info("scheduler: skipping — paused via scheduler_config")
        _log_run("paused")
        return

    log.info(f"scheduler: starting market session at {now_et.strftime('%Y-%m-%d %H:%M ET')}")
    try:
        from agents.graph import run_all_traders
        run_all_traders()
        log.info("scheduler: market session complete")
        _log_run("success")
    except Exception as e:
        log.error(f"scheduler: market session failed: {e}")
        _log_run("error", str(e)[:500])


# 10:00 AM, 1:00 PM, 3:30 PM ET on weekdays
_TRIGGERS = [
    CronTrigger(hour=10, minute=0,  day_of_week="mon-fri", timezone=ET),
    CronTrigger(hour=13, minute=0,  day_of_week="mon-fri", timezone=ET),
    CronTrigger(hour=15, minute=30, day_of_week="mon-fri", timezone=ET),
]
for trigger in _TRIGGERS:
    scheduler.add_job(run_market_session, trigger)


@app.on_event("startup")
async def startup():
    scheduler.start()
    log.info("scheduler: started — 3 daily triggers (10:00, 13:00, 15:30 ET)")


@app.on_event("shutdown")
async def shutdown():
    scheduler.shutdown(wait=False)
    log.info("scheduler: shut down")


# ---------------------------------------------------------------------------
# Scheduler endpoints
# ---------------------------------------------------------------------------

@app.get("/scheduler/status")
def scheduler_status():
    jobs = scheduler.get_jobs()
    next_runs = sorted(
        [j.next_run_time for j in jobs if j.next_run_time],
        key=lambda t: t
    )[:3]
    return {
        "running": scheduler.running,
        "paused": _is_paused(),
        "next_runs": [t.isoformat() for t in next_runs],
    }


@app.post("/scheduler/pause")
def pause_scheduler():
    try:
        existing = supabase.table("scheduler_config").select("key").eq("key", "paused").execute()
        if existing.data:
            supabase.table("scheduler_config").update({"value": "true"}).eq("key", "paused").execute()
        else:
            supabase.table("scheduler_config").insert({"key": "paused", "value": "true"}).execute()
        log.info("scheduler: paused via API")
        return {"paused": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/scheduler/unpause")
def unpause_scheduler():
    try:
        existing = supabase.table("scheduler_config").select("key").eq("key", "paused").execute()
        if existing.data:
            supabase.table("scheduler_config").update({"value": "false"}).eq("key", "paused").execute()
        else:
            supabase.table("scheduler_config").insert({"key": "paused", "value": "false"}).execute()
        log.info("scheduler: unpaused via API")
        return {"paused": False}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Alpaca endpoints
# ---------------------------------------------------------------------------

@app.get("/alpaca/positions")
def get_alpaca_positions():
    if not alpaca_client:
        raise HTTPException(status_code=503, detail="Alpaca client not configured — set ALPACA_API_KEY and ALPACA_SECRET_KEY env vars")
    try:
        positions = alpaca_client.get_all_positions()
        return [p.model_dump() for p in positions]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Alpaca API error: {e}")


# ---------------------------------------------------------------------------
# Existing endpoints
# ---------------------------------------------------------------------------

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
    balances = supabase.table("fund_balance").select("trader_id, cash").execute()
    balance_map = {row["trader_id"]: float(row["cash"]) for row in balances.data}
    total_cash = sum(balance_map.get(t, STARTING_CASH) for t in TRADER_IDS)

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

    trades = supabase.table("trades").select("id", count="exact").execute()
    total_trades = trades.count or 0

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    decisions_today = (
        supabase.table("agent_decisions")
        .select("id", count="exact")
        .gte("created_at", today_start)
        .execute()
    )
    decisions_today_count = decisions_today.count or 0

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

    positions = supabase.table("portfolio_positions").select("ticker, shares, avg_cost, trader_id").execute()
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

        wins = sum(1 for p in trader_positions if current_prices.get(p["ticker"], 0.0) > p["avg_cost"])
        losses = sum(1 for p in trader_positions if current_prices.get(p["ticker"], 0.0) < p["avg_cost"])

        trade_count_res = (
            supabase.table("trades")
            .select("id", count="exact")
            .eq("trader_id", trader_id)
            .execute()
        )

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
            "wins": wins,
            "losses": losses,
            "trade_count": trade_count_res.count or 0,
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
