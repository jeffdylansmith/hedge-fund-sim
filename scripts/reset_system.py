"""
Reset Meridian Capital simulation to a clean slate.

Steps:
  1. Verify Alpaca account equity is ~$1M
  2. Pause scheduler via Supabase config
  3. Cancel all open Alpaca orders
  4. Close all open Alpaca positions
  5. Clear simulation tables
  6. Reset fund_balance to $333,333.33 for each trader
  7. Unpause scheduler
"""

import os
import sys
import time
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db import supabase

TRADERS     = ["alex", "jordan", "casey"]
RESET_CASH  = 333_333.33
# table -> timestamp column used for the delete filter
CLEAR_TABLES = {
    "reconciliation_log":    "checked_at",
    "trades":                "executed_at",
    "portfolio_positions":   "last_updated",
    "portfolio_value_history": "recorded_at",
    "agent_decisions":       "created_at",
    "scheduler_runs":        "ran_at",
}


# ── 1. Alpaca client ──────────────────────────────────────────────────────────

alpaca_key    = os.getenv("ALPACA_API_KEY")
alpaca_secret = os.getenv("ALPACA_SECRET_KEY")

if not alpaca_key or not alpaca_secret:
    print("WARNING: ALPACA_API_KEY / ALPACA_SECRET_KEY not set — skipping Alpaca steps")
    alpaca = None
else:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import ClosePositionRequest
    alpaca = TradingClient(alpaca_key, alpaca_secret, paper=True)


# ── 2. Verify Alpaca equity ───────────────────────────────────────────────────

if alpaca:
    account = alpaca.get_account()
    equity  = float(account.equity)
    print(f"Alpaca account equity: ${equity:,.2f}")
    if equity < 900_000:
        print(f"WARNING: equity ${equity:,.2f} is well below $1M — proceeding anyway")
    else:
        print("  ✓ equity looks good")
else:
    print("Skipping Alpaca equity check — no credentials")


# ── 3. Pause scheduler ────────────────────────────────────────────────────────

print("\nPausing scheduler...")
existing = supabase.table("scheduler_config").select("key").eq("key", "paused").execute()
if existing.data:
    supabase.table("scheduler_config").update({"value": "true"}).eq("key", "paused").execute()
else:
    supabase.table("scheduler_config").insert({"key": "paused", "value": "true"}).execute()
print("  ✓ scheduler paused")


# ── 4. Cancel open Alpaca orders ──────────────────────────────────────────────

if alpaca:
    print("\nCancelling open Alpaca orders...")
    try:
        cancel_statuses = alpaca.cancel_orders()
        print(f"  ✓ cancelled {len(cancel_statuses)} order(s)")
    except Exception as e:
        print(f"  WARNING: cancel_orders failed: {e}")
else:
    print("\nSkipping order cancellation — no Alpaca credentials")


# ── 5. Close open Alpaca positions ────────────────────────────────────────────

if alpaca:
    print("\nClosing open Alpaca positions...")
    try:
        positions = alpaca.get_all_positions()
        if not positions:
            print("  ✓ no open positions to close")
        else:
            close_statuses = alpaca.close_all_positions(cancel_orders=True)
            print(f"  ✓ closed {len(positions)} position(s)")
            time.sleep(3)  # give Alpaca a moment to process
    except Exception as e:
        print(f"  WARNING: close_all_positions failed: {e}")
else:
    print("Skipping position closure — no Alpaca credentials")


# ── 6. Clear simulation tables ────────────────────────────────────────────────

print("\nClearing simulation tables...")
for table, ts_col in CLEAR_TABLES.items():
    try:
        supabase.table(table).delete().gte(ts_col, "2000-01-01").execute()
        print(f"  ✓ {table}")
    except Exception as e:
        print(f"  ERROR: {table} failed: {e}")


# ── 7. Verify tables are empty ────────────────────────────────────────────────

print("\nVerifying tables are empty...")
all_clear = True
for table in CLEAR_TABLES:  # dict iteration gives keys
    try:
        result = supabase.table(table).select("*", count="exact").execute()
        count  = result.count if result.count is not None else len(result.data)
        status = "✓" if count == 0 else f"✗ {count} rows remain"
        print(f"  {status}  {table}")
        if count != 0:
            all_clear = False
    except Exception as e:
        print(f"  ERROR checking {table}: {e}")
        all_clear = False


# ── 8. Reset fund balances ────────────────────────────────────────────────────

print("\nResetting fund balances...")
now = datetime.now(timezone.utc).isoformat()
for trader_id in TRADERS:
    try:
        supabase.table("fund_balance").update({
            "cash": RESET_CASH,
            "last_updated": now,
        }).eq("trader_id", trader_id).execute()
        print(f"  ✓ {trader_id}: ${RESET_CASH:,.2f}")
    except Exception as e:
        print(f"  ERROR resetting {trader_id}: {e}")


# ── 9. Verify fund balances ───────────────────────────────────────────────────

print("\nVerifying fund balances...")
rows = supabase.table("fund_balance").select("trader_id, cash").execute()
for row in rows.data:
    cash   = float(row["cash"])
    ok     = abs(cash - RESET_CASH) < 0.01
    status = "✓" if ok else f"✗ got ${cash:,.2f}"
    print(f"  {status}  {row['trader_id']}: ${cash:,.2f}")


# ── 10. Unpause scheduler ─────────────────────────────────────────────────────

print("\nUnpausing scheduler...")
supabase.table("scheduler_config").update({"value": "false"}).eq("key", "paused").execute()
print("  ✓ scheduler unpaused")

print("\n" + "="*50)
print("Reset complete." if all_clear else "Reset done — check warnings above.")
print("="*50)
