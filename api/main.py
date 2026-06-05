from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from supabase import create_client
from dotenv import load_dotenv
from datetime import datetime, date, time, timezone
import anthropic
import resend
import pytz
import logging
import os
import yfinance as yf

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("meridian")

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    log.info(
        "scheduler: started — discovery 09:00, trader runs 08:30–16:00, "
        "EOD flatten 15:45, EOD summary 16:05, reconcile every 30min ET"
    )
    send_alert(
        subject="Meridian Capital — App Started",
        body=(
            f"The app restarted successfully at "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}.\n\n"
            f"Scheduler is running. Next market session begins at 8:30am ET."
        ),
    )
    try:
        summary = reconcile_positions()
        log.info(f"startup reconciliation: {summary}")
    except Exception as e:
        log.warning(f"startup reconciliation failed: {e}")
    yield
    scheduler.shutdown(wait=False)
    log.info("scheduler: shut down")


app = FastAPI(title="Hedge Fund Sim API", lifespan=lifespan)

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

_anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

_TRADER_PERSONALITIES = {
    "alex":   "aggressive momentum trader who buys breakouts and hates missing moves",
    "jordan": "conservative macro investor who thinks everyone trades too much",
    "casey":  "contrarian who suspects every consensus trade is a trap",
}

STARTING_CASH = 333_333.33
TRADER_IDS = ["alex", "jordan", "casey"]
ET = pytz.timezone("America/New_York")
_last_reconcile_summary: dict = {}

resend.api_key = os.getenv("RESEND_API_KEY", "")
ALERT_EMAIL = os.getenv("ALERT_EMAIL_ADDRESS", "")

_NYSE_HOLIDAYS_2026 = {
    date(2026, 1,  1),   # New Year's Day
    date(2026, 1, 19),   # MLK Day
    date(2026, 2, 16),   # Presidents Day
    date(2026, 4,  3),   # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 7,  3),   # Independence Day (observed)
    date(2026, 9,  7),   # Labor Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 11, 27),  # Day after Thanksgiving (treat as closed)
    date(2026, 12, 25),  # Christmas
}


def is_market_hours() -> bool:
    now_et = datetime.now(ET)
    if now_et.weekday() >= 5:
        return False
    if now_et.date() in _NYSE_HOLIDAYS_2026:
        return False
    t = now_et.time()
    return time(8, 30) <= t <= time(16, 0)


def send_alert(subject: str, body: str) -> None:
    if not resend.api_key or not ALERT_EMAIL:
        return
    try:
        resend.Emails.send({
            "from": "Meridian Capital <onboarding@resend.dev>",
            "to": ALERT_EMAIL,
            "subject": subject,
            "text": body,
        })
        log.info(f"alert sent: {subject}")
    except Exception as e:
        log.warning(f"alert failed: {e}")


def check_fund_health() -> None:
    balances = supabase.table("fund_balance").select("cash").execute()
    total_cash = sum(float(r["cash"]) for r in balances.data)

    positions = supabase.table("portfolio_positions").select("ticker, shares").execute()
    tickers = list({p["ticker"] for p in positions.data})
    price_map = get_latest_prices(tickers)
    position_value = sum(
        float(p["shares"]) * price_map.get(p["ticker"], 0.0)
        for p in positions.data
    )
    total_fund_value = total_cash + position_value

    if total_fund_value < 500_000:
        drawdown_pct = (1_000_000 - total_fund_value) / 1_000_000 * 100
        send_alert(
            subject="⚠️ Meridian Capital — Fund Drawdown Alert",
            body=(
                f"Total fund value has dropped below $500,000.\n\n"
                f"Current value: ${total_fund_value:,.2f}\n"
                f"Starting capital: $1,000,000.00\n"
                f"Drawdown: {drawdown_pct:.1f}%\n\n"
                f"Log in to review: https://hedge-fund-sim.vercel.app"
            ),
        )


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


def refresh_current_prices() -> None:
    try:
        rows = supabase.table("watchlist").select("ticker").execute()
        tickers = [r["ticker"] for r in rows.data]
    except Exception as e:
        log.error(f"refresh_prices: could not fetch watchlist: {e}")
        return

    refreshed, failed = 0, []
    for ticker in tickers:
        try:
            df = yf.download(
                ticker, period="1d", interval="1h",
                progress=False, auto_adjust=True, multi_level_index=False,
            )
            if df.empty:
                failed.append(f"{ticker}(no data)")
                continue
            row = df.iloc[-1]
            ts = df.index[-1]
            supabase.table("prices").upsert({
                "ticker": ticker,
                "timestamp": ts.isoformat(),
                "open":   float(row["Open"]),
                "high":   float(row["High"]),
                "low":    float(row["Low"]),
                "close":  float(row["Close"]),
                "volume": int(row["Volume"]),
            }, on_conflict="ticker,timestamp").execute()
            refreshed += 1
        except Exception as e:
            failed.append(f"{ticker}({e})")

    msg = f"refresh_prices: {refreshed}/{len(tickers)} tickers refreshed"
    if failed:
        msg += f" — failures: {', '.join(failed)}"
    log.info(msg)


def run_market_session() -> None:
    if not is_market_hours():
        log.info("scheduler: skipping — outside market hours")
        _log_run("skipped")
        return

    if _is_paused():
        log.info("scheduler: skipping — paused via scheduler_config")
        _log_run("paused")
        return

    now_et = datetime.now(ET)
    log.info(f"scheduler: starting market session at {now_et.strftime('%Y-%m-%d %H:%M ET')}")
    try:
        refresh_current_prices()
        from agents.graph import run_all_traders
        run_all_traders()
        log.info("scheduler: market session complete")
        _log_run("success")
    except Exception as e:
        log.error(f"scheduler: market session failed: {e}")
        _log_run("error", str(e)[:500])

    try:
        check_fund_health()
    except Exception as e:
        log.warning(f"fund health check failed: {e}")


def run_discovery_job() -> None:
    if not is_market_hours():
        log.info("discovery: skipping — outside market hours")
        return

    now_et = datetime.now(ET)
    log.info(f"discovery: starting ticker discovery at {now_et.strftime('%Y-%m-%d %H:%M ET')}")
    try:
        from ingestion.discover_tickers import run_discovery
        run_discovery()
        log.info("discovery: ticker discovery complete")
    except Exception as e:
        log.error(f"discovery: ticker discovery failed: {e}")


def run_fundamental_analysis() -> None:
    try:
        rows = supabase.table("watchlist").select("ticker").execute()
        tickers = [r["ticker"] for r in rows.data]
        if not tickers:
            log.info("fundamental analysis: watchlist empty, skipping")
            return

        from agents.fundamental_analyst import analyze_fundamentals
        results = analyze_fundamentals(tickers)

        now = datetime.now(timezone.utc).isoformat()
        for ticker, data in results.items():
            supabase.table("fundamental_scores").upsert({
                "ticker":         ticker,
                "score":          data["score"],
                "verdict":        data["verdict"],
                "thesis":         data["thesis"],
                "pe_ratio":       data["pe_ratio"],
                "pb_ratio":       data["pb_ratio"],
                "debt_to_equity": data["debt_to_equity"],
                "revenue_growth": data["revenue_growth"],
                "profit_margin":  data["profit_margin"],
                "free_cash_flow": data["free_cash_flow"],
                "market_cap":     data["market_cap"],
                "sector":         data["sector"],
                "analyzed_at":    now,
            }, on_conflict="ticker").execute()

        log.info(f"fundamental analysis: {len(results)} tickers scored")
    except Exception as e:
        log.error(f"fundamental analysis: failed: {e}")


def flatten_all_positions() -> None:
    if _is_paused():
        log.info("eod_flatten: skipping — paused")
        return

    positions = supabase.table("portfolio_positions").select("*").execute()
    if not positions.data:
        log.info("EOD flatten: no open positions to close")
        return

    tickers = list({p["ticker"] for p in positions.data})
    current_prices = get_latest_prices(tickers)
    now = datetime.now(timezone.utc).isoformat()

    closed = 0
    records_written = 0
    failures = 0
    trader_set: set = set()

    for pos in positions.data:
        trader_id  = pos["trader_id"]
        ticker     = pos["ticker"]
        shares     = pos["shares"]
        sale_price = current_prices.get(ticker, float(pos["avg_cost"]))
        total_value = shares * sale_price

        try:
            supabase.rpc("flatten_position", {
                "p_trader_id": trader_id,
                "p_ticker":    ticker,
                "p_shares":    shares,
                "p_price":     sale_price,
            }).execute()
        except Exception as e:
            log.error(f"eod_flatten: RPC failed for {ticker} ({trader_id}): {e}")
            failures += 1
            continue

        closed += 1
        trader_set.add(trader_id)

        if alpaca_client:
            try:
                order = MarketOrderRequest(
                    symbol=ticker,
                    qty=shares,
                    side=OrderSide.SELL,
                    time_in_force=TimeInForce.DAY,
                )
                alpaca_client.submit_order(order)
            except Exception as e:
                log.warning(f"eod_flatten: Alpaca SELL failed for {ticker}: {e}")

        try:
            alpaca_ok = alpaca_client is not None
            supabase.table("trades").insert({
                "ticker":           ticker,
                "action":           "sell",
                "shares":           shares,
                "price":            sale_price,
                "total_value":      total_value,
                "executed_at":      now,
                "trader_id":        trader_id,
                "alpaca_submitted": alpaca_ok,
            }).execute()
            records_written += 1
        except Exception as e:
            log.warning(f"eod_flatten: trade record failed for {ticker} ({trader_id}): {e}")

    log.info(
        f"EOD flatten: closed {closed} positions via RPC, "
        f"{records_written} trade records written, {failures} failures"
    )


def generate_daily_summaries() -> None:
    if _is_paused():
        log.info("eod_summary: skipping — paused")
        return

    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat()
    now = datetime.now(timezone.utc).isoformat()

    for trader_id in TRADER_IDS:
        try:
            trades_rows = (
                supabase.table("trades")
                .select("ticker, action, shares, price, total_value")
                .eq("trader_id", trader_id)
                .gte("executed_at", today_start)
                .execute()
            )
            decisions_rows = (
                supabase.table("agent_decisions")
                .select("action, ticker")
                .eq("trader_id", trader_id)
                .gte("created_at", today_start)
                .neq("action", "trader_reaction")
                .neq("action", "daily_summary")
                .execute()
            )

            trade_lines = [
                f"{t['action'].upper()} {t['shares']} {t['ticker']} @ ${t['price']:.2f}"
                for t in trades_rows.data
            ]

            # ── Pre-compute all P&L figures in Python ────────────────────────
            sells = [t for t in trades_rows.data if t["action"] == "sell"]
            buys  = [t for t in trades_rows.data if t["action"] == "buy"]

            total_sell_value = sum(float(t["total_value"]) for t in sells)
            total_buy_value  = sum(float(t["total_value"]) for t in buys)
            net_cash_flow    = total_sell_value - total_buy_value

            # Realized P&L: for each sell, find cost basis from most recent prior BUY
            realized_pnl = 0.0
            for sell in sells:
                ticker      = sell["ticker"]
                sell_price  = float(sell["price"])
                sell_shares = float(sell["shares"])
                prior_buy = (
                    supabase.table("trades")
                    .select("price")
                    .eq("trader_id", trader_id)
                    .eq("ticker", ticker)
                    .eq("action", "buy")
                    .lt("executed_at", today_start)
                    .order("executed_at", desc=True)
                    .limit(1)
                    .execute()
                )
                cost_basis = float(prior_buy.data[0]["price"]) if prior_buy.data else sell_price
                realized_pnl += (sell_price - cost_basis) * sell_shares

            # Current cash and true P&L vs starting capital
            balance_row = (
                supabase.table("fund_balance")
                .select("cash")
                .eq("trader_id", trader_id)
                .limit(1)
                .execute()
            )
            current_cash = float(balance_row.data[0]["cash"]) if balance_row.data else STARTING_CASH
            actual_pnl   = current_cash - STARTING_CASH

            buy_count  = sum(1 for d in decisions_rows.data if d["action"] == "BUY")
            sell_count = sum(1 for d in decisions_rows.data if d["action"] == "SELL")
            hold_count = sum(1 for d in decisions_rows.data if d["action"] == "HOLD")

            trader_name = trader_id.capitalize()
            personality = _TRADER_PERSONALITIES.get(trader_id, "")

            system = (
                f"{trader_name} is a {personality}. "
                "Write your EOD trading journal entry. Max 5 lines. "
                "Be specific about what you traded and why. Stay in character. No disclaimers. "
                "All P&L figures are pre-computed and provided to you. "
                "Use these exact numbers — do not recalculate or estimate P&L yourself."
            )
            user_content = (
                f"Your trades today:\n"
                + ("\n".join(trade_lines) if trade_lines else "No trades executed today.")
                + f"\n\nDecisions made: {buy_count} BUY, {sell_count} SELL, {hold_count} HOLD\n"
                + f"Realized P&L on closed positions today: ${realized_pnl:+,.2f}\n"
                + f"Total value sold: ${total_sell_value:,.2f}\n"
                + f"Total value bought: ${total_buy_value:,.2f}\n"
                + f"Current cash balance: ${current_cash:,.2f}\n"
                + f"True P&L vs starting capital ($333,333.33): ${actual_pnl:+,.2f}\n\n"
                + "Summarize your day."
            )

            msg = _anthropic_client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=256,
                system=system,
                messages=[{"role": "user", "content": user_content}],
            )
            summary_text = msg.content[0].text.strip()

            supabase.table("agent_decisions").insert({
                "agent_name": trader_name,
                "trader_id":  trader_id,
                "ticker":     "PORTFOLIO",
                "action":     "daily_summary",
                "reasoning":  summary_text,
                "confidence": None,
                "created_at": now,
            }).execute()

            log.info(f"eod_summary: {trader_name} summary written")

        except Exception as e:
            log.error(f"eod_summary: {trader_id} failed: {e}")

    log.info(f"EOD summary: completed for {len(TRADER_IDS)} traders")


def reconcile_positions() -> dict:
    global _last_reconcile_summary
    checked = corrections = errors = 0

    try:
        if not alpaca_client:
            log.info("reconcile: no Alpaca client configured, skipping")
            return {"checked": 0, "corrections": 0, "errors": 0, "ran_at": datetime.now(timezone.utc).isoformat()}

        alpaca_pos_list = alpaca_client.get_all_positions()
        alpaca_by_ticker = {
            p.symbol: {"shares": int(float(p.qty)), "avg_entry_price": float(p.avg_entry_price)}
            for p in alpaca_pos_list
        }

        db_rows = supabase.table("portfolio_positions").select("*").execute()
        db_by_key: dict = {}
        for p in db_rows.data:
            db_by_key[(p["trader_id"], p["ticker"])] = p

        # Map each Alpaca ticker → trader_id via most recent trade (any alpaca_submitted status)
        ticker_to_trader: dict = {}
        for ticker in alpaca_by_ticker:
            rows = (
                supabase.table("trades")
                .select("trader_id")
                .eq("ticker", ticker)
                .order("executed_at", desc=True)
                .limit(1)
                .execute()
            )
            if rows.data:
                ticker_to_trader[ticker] = rows.data[0]["trader_id"]

        # Fallback: trader with the most total trades
        counts = {}
        for tid in TRADER_IDS:
            r = supabase.table("trades").select("id", count="exact").eq("trader_id", tid).execute()
            counts[tid] = r.count or 0
        fallback_trader = max(counts, key=counts.get) if counts else TRADER_IDS[0]

        alpaca_pairs: dict = {}
        for ticker, pos in alpaca_by_ticker.items():
            trader_id = ticker_to_trader.get(ticker, fallback_trader)
            alpaca_pairs[(trader_id, ticker)] = pos

        all_pairs = set(alpaca_pairs.keys()) | set(db_by_key.keys())

        for (trader_id, ticker) in all_pairs:
            checked += 1
            alpaca_pos = alpaca_pairs.get((trader_id, ticker))
            db_pos = db_by_key.get((trader_id, ticker))

            alpaca_shares = alpaca_pos["shares"] if alpaca_pos else 0
            db_shares     = int(db_pos["shares"]) if db_pos else 0
            avg_cost      = float(db_pos["avg_cost"]) if db_pos else (
                alpaca_pos["avg_entry_price"] if alpaca_pos else 0.0
            )

            resolution = None
            notes      = None

            try:
                if alpaca_pos and db_pos:
                    if alpaca_shares == db_shares:
                        continue  # CASE 4: exact match — nothing to do
                    # CASE 1: share count mismatch
                    cash_delta = (db_shares - alpaca_shares) * avg_cost
                    supabase.rpc("reconcile_position", {
                        "p_trader_id":    trader_id,
                        "p_ticker":       ticker,
                        "p_alpaca_shares": alpaca_shares,
                        "p_avg_cost":     avg_cost,
                        "p_cash_delta":   cash_delta,
                    }).execute()
                    resolution = "corrected"
                    notes = (
                        f"DB had {db_shares} shares, Alpaca has {alpaca_shares}; "
                        f"cash adjusted by ${cash_delta:+,.2f}"
                    )

                elif alpaca_pos and not db_pos:
                    # CASE 2: Alpaca position missing from DB
                    supabase.rpc("reconcile_position", {
                        "p_trader_id":    trader_id,
                        "p_ticker":       ticker,
                        "p_alpaca_shares": alpaca_shares,
                        "p_avg_cost":     alpaca_pos["avg_entry_price"],
                        "p_cash_delta":   0,
                    }).execute()
                    resolution = "position_added"
                    notes = (
                        f"Added missing position: {alpaca_shares} shares "
                        f"@ ${alpaca_pos['avg_entry_price']:.2f}"
                    )

                elif db_pos and not alpaca_pos:
                    # CASE 3: DB position not in Alpaca (ghost)
                    supabase.rpc("remove_ghost_position", {
                        "p_trader_id": trader_id,
                        "p_ticker":    ticker,
                        "p_shares":    db_shares,
                        "p_avg_cost":  avg_cost,
                    }).execute()
                    resolution = "position_removed"
                    notes = f"Removed ghost position: {db_shares} shares not found in Alpaca"

                if resolution:
                    supabase.table("reconciliation_log").insert({
                        "trader_id":     trader_id,
                        "ticker":        ticker,
                        "db_shares":     db_shares,
                        "alpaca_shares": alpaca_shares,
                        "resolution":    resolution,
                        "notes":         notes,
                    }).execute()
                    corrections += 1
                    log.info(f"reconcile: {resolution} — {ticker} ({trader_id}): {notes}")

            except Exception as e:
                print(f"Reconciliation error for {ticker}: {type(e).__name__}: {e}")
                log.error(f"reconcile: failed for {ticker} ({trader_id}): {e}")
                errors += 1

    except Exception as e:
        log.error(f"reconcile: outer error: {e}")
        errors += 1

    if corrections > 0:
        send_alert(
            subject="🔧 Meridian Capital — Reconciliation Corrections Made",
            body=(
                f"Reconciliation found and corrected {corrections} discrepancies "
                f"between the database and Alpaca.\n\n"
                f"Run at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
                f"Positions checked: {checked}\n"
                f"Corrections made: {corrections}\n\n"
                f"This was handled automatically. "
                f"Review at: https://hedge-fund-sim.vercel.app"
            ),
        )

    _last_reconcile_summary = {
        "checked":     checked,
        "corrections": corrections,
        "errors":      errors,
        "ran_at":      datetime.now(timezone.utc).isoformat(),
    }
    log.info(f"reconcile: checked={checked}, corrections={corrections}, errors={errors}")
    return _last_reconcile_summary


# Hourly on the :30 mark — is_market_hours() gates actual execution (8:30am–4pm ET, no holidays)
scheduler.add_job(
    run_market_session,
    IntervalTrigger(hours=1, start_date="2026-01-01 08:30:00", timezone=ET),
)

# 9:00 AM ET on weekdays — ticker discovery before first trader run
scheduler.add_job(run_discovery_job, CronTrigger(hour=9, minute=0, day_of_week="mon-fri", timezone=ET))

# 9:30 AM ET on weekdays — fundamental analysis after discovery
scheduler.add_job(run_fundamental_analysis, CronTrigger(hour=9, minute=30, day_of_week="mon-fri", timezone=ET))

# 3:45 PM ET on weekdays — flatten all positions before market close
scheduler.add_job(flatten_all_positions, CronTrigger(hour=15, minute=45, day_of_week="mon-fri", timezone=ET))

# 4:05 PM ET on weekdays — generate EOD summaries after close
scheduler.add_job(generate_daily_summaries, CronTrigger(hour=16, minute=5, day_of_week="mon-fri", timezone=ET))

# Every 30 minutes 24/7 — reconcile Alpaca vs DB (no market-hours or pause gate)
scheduler.add_job(reconcile_positions, IntervalTrigger(minutes=30))


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
        "market_open": is_market_hours(),
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


@app.post("/run")
def run_now():
    ran_at = datetime.now(timezone.utc).isoformat()
    log.info("run: manual trigger via POST /run")
    try:
        from agents.graph import run_all_traders
        run_all_traders()
        _log_run("success")
        return {"ran_at": ran_at, "status": "ok", "errors": []}
    except Exception as e:
        error = str(e)
        log.error(f"run: failed — {error}")
        _log_run("error", error[:500])
        raise HTTPException(status_code=500, detail=error)


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
# Helpers
# ---------------------------------------------------------------------------

def get_latest_prices(tickers: list[str]) -> dict:
    prices = {}
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
            prices[ticker] = float(rows.data[0]["close"])
    return prices


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

    positions = supabase.table("portfolio_positions").select("ticker, shares, avg_cost, trader_id").execute()
    tickers = list({p["ticker"] for p in positions.data})
    current_prices = get_latest_prices(tickers)

    position_value = sum(
        p["shares"] * current_prices.get(p["ticker"], p["avg_cost"])
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
        pos_val = sum(p["shares"] * current_prices.get(p["ticker"], p["avg_cost"]) for p in trader_positions)
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
    current_prices = get_latest_prices(tickers)

    result = []
    for trader_id in TRADER_IDS:
        cash = balance_map.get(trader_id, STARTING_CASH)
        trader_positions = [p for p in positions.data if p["trader_id"] == trader_id]
        pos_val = sum(p["shares"] * current_prices.get(p["ticker"], p["avg_cost"]) for p in trader_positions)
        pnl = (cash + pos_val) - STARTING_CASH

        wins = sum(1 for p in trader_positions if current_prices.get(p["ticker"], p["avg_cost"]) > p["avg_cost"])
        losses = sum(1 for p in trader_positions if current_prices.get(p["ticker"], p["avg_cost"]) < p["avg_cost"])

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


@app.get("/traders/{trader_id}/positions")
def get_trader_positions(trader_id: str):
    if trader_id not in TRADER_IDS:
        raise HTTPException(status_code=404, detail=f"Unknown trader_id: {trader_id!r}")

    rows = (
        supabase.table("portfolio_positions")
        .select("*")
        .eq("trader_id", trader_id)
        .execute()
    )

    tickers = [p["ticker"] for p in rows.data]
    price_map = get_latest_prices(tickers)

    positions = []
    for p in rows.data:
        ticker       = p["ticker"]
        shares       = float(p["shares"])
        avg_cost     = float(p["avg_cost"])
        stale        = ticker not in price_map
        current_price = price_map.get(ticker, avg_cost)
        cost_basis    = round(shares * avg_cost, 2)
        market_value  = round(shares * current_price, 2)
        unreal_pnl    = round(market_value - cost_basis, 2)
        unreal_pct    = round(unreal_pnl / cost_basis * 100, 2) if cost_basis else 0.0
        positions.append({
            "ticker":              ticker,
            "shares":              shares,
            "avg_cost":            round(avg_cost, 4),
            "current_price":       round(current_price, 4),
            "cost_basis":          cost_basis,
            "market_value":        market_value,
            "unrealized_pnl":      unreal_pnl,
            "unrealized_pnl_pct":  unreal_pct,
            "price_is_stale":      stale,
        })

    positions.sort(key=lambda x: x["market_value"], reverse=True)

    summary = {
        "total_cost_basis":    round(sum(p["cost_basis"]   for p in positions), 2),
        "total_market_value":  round(sum(p["market_value"] for p in positions), 2),
        "total_unrealized_pnl": round(sum(p["unrealized_pnl"] for p in positions), 2),
        "position_count":      len(positions),
    }

    return {"trader_id": trader_id, "positions": positions, "summary": summary}


@app.get("/discovery/status")
def get_discovery_status():
    result = (
        supabase.table("discovered_opportunities")
        .select("*")
        .eq("status", "active")
        .order("added_at", desc=True)
        .execute()
    )
    return result.data


@app.post("/eod/flatten")
def trigger_eod_flatten():
    log.info("eod_flatten: manual trigger via POST /eod/flatten")
    try:
        flatten_all_positions()
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/eod/summary")
def trigger_eod_summary():
    log.info("eod_summary: manual trigger via POST /eod/summary")
    try:
        generate_daily_summaries()
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/reconciliation/run")
def run_reconciliation():
    log.info("reconciliation: manual trigger via POST /reconciliation/run")
    try:
        return reconcile_positions()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/reconciliation/status")
def reconciliation_status():
    rows = (
        supabase.table("reconciliation_log")
        .select("*")
        .order("checked_at", desc=True)
        .limit(20)
        .execute()
    )
    return {
        "last_run": _last_reconcile_summary,
        "recent_corrections": rows.data,
    }


@app.get("/fundamentals")
def get_fundamentals():
    result = (
        supabase.table("fundamental_scores")
        .select("*")
        .order("score", desc=True)
        .execute()
    )
    return result.data


@app.post("/fundamentals/run")
def trigger_fundamental_analysis():
    log.info("fundamental analysis: manual trigger via POST /fundamentals/run")
    try:
        run_fundamental_analysis()
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


_TRADER_PERSONALITIES = {
    "alex":   "aggressive momentum trader who buys breakouts and hates missing moves",
    "jordan": "conservative macro investor who thinks everyone trades too much",
    "casey":  "contrarian who suspects every consensus trade is a trap",
}


@app.get("/training/export")
def export_training_data(outcome: str = Query(default=None, description="Filter: profitable|unprofitable|open")):
    # Fetch all portfolio_review decisions (reasoning is a JSON array of trade objects)
    reviews = (
        supabase.table("agent_decisions")
        .select("trader_id, reasoning, created_at")
        .eq("action", "portfolio_review")
        .order("created_at")
        .execute()
    )

    # Fetch all trades for outcome lookup
    all_trades = supabase.table("trades").select("trader_id, ticker, action, price, executed_at").order("executed_at").execute()
    trades_list = all_trades.data or []

    # Index buy trades: (trader_id, ticker) → list of {price, executed_at}
    buys_index: dict = {}
    sells_index: dict = {}
    for t in trades_list:
        key = (t["trader_id"], t["ticker"])
        if t["action"] == "buy":
            buys_index.setdefault(key, []).append(t)
        elif t["action"] == "sell":
            sells_index.setdefault(key, []).append(t)

    examples = []
    for review in (reviews.data or []):
        trader_id = review["trader_id"]
        created_at = review["created_at"]
        personality = _TRADER_PERSONALITIES.get(trader_id, "unknown")

        try:
            decisions = json.loads(review["reasoning"])
        except (json.JSONDecodeError, TypeError):
            continue

        if not isinstance(decisions, list):
            continue

        for decision in decisions:
            if not isinstance(decision, dict):
                continue
            action = str(decision.get("action", "")).upper()
            if action not in ("BUY", "SELL"):
                continue

            ticker = str(decision.get("ticker", "")).upper()
            reasoning = str(decision.get("reasoning", ""))[:200]
            confidence = decision.get("confidence")

            # Determine outcome for BUY decisions
            trade_outcome = "open"
            if action == "BUY":
                key = (trader_id, ticker)
                # Find the buy price from trades around the same time
                buy_price = None
                for b in buys_index.get(key, []):
                    if b["executed_at"] >= created_at:
                        buy_price = float(b["price"])
                        buy_at = b["executed_at"]
                        break

                if buy_price is not None:
                    # Find a subsequent sell
                    for s in sells_index.get(key, []):
                        if s["executed_at"] > buy_at:
                            sell_price = float(s["price"])
                            trade_outcome = "profitable" if sell_price > buy_price else "unprofitable"
                            break

            example = {
                "instruction": (
                    f"You are {trader_id} with personality: {personality}. "
                    f"Market context: not available. "
                    f"Technical signals for {ticker}: not available. "
                    f"Current portfolio: approximated from trade history. "
                    f"Available cash: not available. "
                    f"Should you BUY, SELL, or HOLD {ticker}?"
                ),
                "response": f"{action} {ticker}. {reasoning}",
                "metadata": {
                    "trader_id": trader_id,
                    "ticker": ticker,
                    "outcome": trade_outcome,
                    "confidence": confidence,
                    "created_at": created_at,
                },
            }
            examples.append(example)

    if outcome:
        examples = [e for e in examples if e["metadata"]["outcome"] == outcome]

    outcome_counts = {"profitable": 0, "unprofitable": 0, "open": 0}
    for e in examples:
        o = e["metadata"]["outcome"]
        if o in outcome_counts:
            outcome_counts[o] += 1

    header = json.dumps({
        "total": len(examples),
        "outcome_breakdown": outcome_counts,
        "filter": outcome,
    })

    def generate_jsonl():
        yield f"# {header}\n"
        for ex in examples:
            yield json.dumps(ex) + "\n"

    return StreamingResponse(
        generate_jsonl(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": "attachment; filename=training_data.jsonl"},
    )
