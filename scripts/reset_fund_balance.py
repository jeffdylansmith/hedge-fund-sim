from datetime import datetime, timezone
from utils.db import supabase

TRADERS = ["alex", "jordan", "casey"]
RESET_CASH = 333_333.33

now = datetime.now(timezone.utc).isoformat()

for trader_id in TRADERS:
    supabase.table("fund_balance").update({
        "cash": RESET_CASH,
        "last_updated": now,
    }).eq("trader_id", trader_id).execute()
    print(f"  {trader_id}: cash reset to ${RESET_CASH:,.2f}")
