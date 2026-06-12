"""
Audit and correct fund integrity without resetting anything.

Run with:
    export PYTHONPATH=/Users/dylan/hedge-fund-sim
    python scripts/audit_fund.py [--dry-run] [--trader TRADER_ID]

Import run_audit() from application code for background/scheduled use.
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db import supabase as _default_supabase

_audit_log = logging.getLogger("meridian.audit")

TRADERS = ["alex", "jordan", "casey"]
STARTING_CASH = 333_333.33
FUND_TOTAL_TARGET = 1_000_000.00
FUND_SANITY_BAND = 0.05  # 5%

AUDIT_LOG_DDL = """
-- Run this once in your Supabase SQL Editor to enable audit logging:
CREATE TABLE IF NOT EXISTS public.audit_log (
    id          bigserial PRIMARY KEY,
    checked_at  timestamptz NOT NULL DEFAULT now(),
    trader_id   text        NOT NULL,
    check_type  text        NOT NULL,
    description text,
    corrected   boolean     NOT NULL DEFAULT false,
    before_value text,
    after_value  text
);
""".strip()

# CLI-only global state — not used by run_audit()
_audit_buffer: list[dict] = []
_audit_log_available: bool | None = None


def _audit_entry(
    trader_id: str,
    check_type: str,
    description: str,
    corrected: bool,
    before_value: str | None = None,
    after_value: str | None = None,
) -> dict:
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "trader_id": trader_id,
        "check_type": check_type,
        "description": description,
        "corrected": corrected,
        "before_value": before_value,
        "after_value": after_value,
    }


# ── CLI-only audit_log helpers ────────────────────────────────────────────────

def _cli_check_audit_log(db) -> bool:
    global _audit_log_available
    if _audit_log_available is not None:
        return _audit_log_available
    try:
        db.table("audit_log").select("id").limit(1).execute()
        _audit_log_available = True
    except Exception:
        _audit_log_available = False
        print("\n" + "=" * 60)
        print("WARNING: audit_log table not found.")
        print("Audit entries will be buffered and printed at the end.")
        print("To enable persistent logging, create the table:")
        print()
        print(AUDIT_LOG_DDL)
        print("=" * 60 + "\n")
    return _audit_log_available


def _cli_write_audit_log(db, entries: list[dict]) -> None:
    if not entries:
        return
    if not _cli_check_audit_log(db):
        _audit_buffer.extend(entries)
        return
    try:
        db.table("audit_log").insert(entries).execute()
    except Exception as e:
        print(f"  [audit_log write failed: {e}]")
        _audit_buffer.extend(entries)


# ── CHECK 1: Cash integrity ───────────────────────────────────────────────────

def check_cash(
    traders: list[str],
    dry_run: bool,
    db=None,
    silent: bool = False,
) -> list[dict]:
    db = db or _default_supabase
    if not silent:
        print("\n" + "─" * 60)
        print("CHECK 1 — Cash integrity")
        print("─" * 60)

    entries = []
    now = datetime.now(timezone.utc).isoformat()

    for trader_id in traders:
        trade_rows = (
            db.table("trades")
            .select("action, total_value")
            .eq("trader_id", trader_id)
            .execute()
        )
        buys  = sum(float(r["total_value"]) for r in trade_rows.data if r["action"] == "buy")
        sells = sum(float(r["total_value"]) for r in trade_rows.data if r["action"] == "sell")
        correct_cash = STARTING_CASH + sells - buys

        balance_rows = (
            db.table("fund_balance")
            .select("cash")
            .eq("trader_id", trader_id)
            .execute()
        )
        actual_cash = float(balance_rows.data[0]["cash"]) if balance_rows.data else None
        drift = abs(correct_cash - actual_cash) if actual_cash is not None else float("inf")
        needs_fix = drift > 0.01

        if not silent:
            if needs_fix:
                status = "DRIFT (dry-run, no write)" if dry_run else "DRIFT"
            else:
                status = "OK"
            print(
                f"  {trader_id:8s}  actual=${actual_cash:>13,.2f}  "
                f"correct=${correct_cash:>13,.2f}  drift=${drift:>10,.2f}  [{status}]"
            )

        if needs_fix and not dry_run:
            db.table("fund_balance").update(
                {"cash": correct_cash, "last_updated": now}
            ).eq("trader_id", trader_id).execute()

        entries.append(_audit_entry(
            trader_id=trader_id,
            check_type="cash_drift",
            description=(
                f"buys=${buys:,.2f} sells=${sells:,.2f} "
                f"correct=${correct_cash:,.2f} actual=${actual_cash:,.2f}"
            ),
            corrected=needs_fix and not dry_run,
            before_value=f"{actual_cash:.6f}" if actual_cash is not None else None,
            after_value=f"{correct_cash:.6f}" if needs_fix else None,
        ))

    return entries


# ── CHECK 2: Position integrity ───────────────────────────────────────────────

def check_positions(
    traders: list[str],
    dry_run: bool,
    db=None,
    silent: bool = False,
) -> list[dict]:
    db = db or _default_supabase
    if not silent:
        print("\n" + "─" * 60)
        print("CHECK 2 — Position integrity")
        print("─" * 60)

    entries = []
    now = datetime.now(timezone.utc).isoformat()

    for trader_id in traders:
        positions = (
            db.table("portfolio_positions")
            .select("ticker, shares, avg_cost")
            .eq("trader_id", trader_id)
            .execute()
        ).data

        trade_rows = (
            db.table("trades")
            .select("ticker, action, shares")
            .eq("trader_id", trader_id)
            .execute()
        ).data

        net: dict[str, int] = {}
        for row in trade_rows:
            t = row["ticker"]
            s = int(row["shares"])
            net[t] = net.get(t, 0) + (s if row["action"] == "buy" else -s)

        for pos in positions:
            ticker    = pos["ticker"]
            db_shares = int(pos["shares"])
            trade_net = net.get(ticker, 0)

            if trade_net != db_shares:
                tag = "dry-run" if dry_run else "corrected"
                if trade_net <= 0:
                    action_desc = f"delete (net={trade_net})"
                    if not dry_run:
                        db.table("portfolio_positions").delete().eq(
                            "ticker", ticker
                        ).eq("trader_id", trader_id).execute()
                else:
                    action_desc = f"update shares {db_shares} → {trade_net}"
                    if not dry_run:
                        db.table("portfolio_positions").update(
                            {"shares": trade_net, "last_updated": now}
                        ).eq("ticker", ticker).eq("trader_id", trader_id).execute()

                if not silent:
                    print(f"  {trader_id:8s}  {ticker:6s}  db={db_shares}  net={trade_net}  → {action_desc} [{tag}]")

                entries.append(_audit_entry(
                    trader_id=trader_id,
                    check_type="position_mismatch",
                    description=f"{ticker}: db_shares={db_shares} vs trade_net={trade_net}",
                    corrected=not dry_run,
                    before_value=str(db_shares),
                    after_value=str(trade_net),
                ))
            elif not silent:
                print(f"  {trader_id:8s}  {ticker:6s}  shares={db_shares}  [OK]")

    if not entries and not silent:
        print("  All positions match trade history.")

    return entries


# ── CHECK 3: Ghost positions ──────────────────────────────────────────────────

def check_ghost_positions(
    traders: list[str],
    dry_run: bool,
    db=None,
    silent: bool = False,
) -> list[dict]:
    db = db or _default_supabase
    if not silent:
        print("\n" + "─" * 60)
        print("CHECK 3 — Ghost positions (shares <= 0)")
        print("─" * 60)

    entries = []
    found_any = False

    for trader_id in traders:
        ghosts = (
            db.table("portfolio_positions")
            .select("ticker, shares")
            .eq("trader_id", trader_id)
            .lte("shares", 0)
            .execute()
        ).data

        for pos in ghosts:
            found_any = True
            ticker = pos["ticker"]
            shares = pos["shares"]
            tag = "dry-run" if dry_run else "deleted"

            if not silent:
                print(f"  {trader_id:8s}  {ticker:6s}  shares={shares}  [{tag}]")

            if not dry_run:
                db.table("portfolio_positions").delete().eq(
                    "ticker", ticker
                ).eq("trader_id", trader_id).execute()

            entries.append(_audit_entry(
                trader_id=trader_id,
                check_type="ghost_position",
                description=f"{ticker}: shares={shares} <= 0",
                corrected=not dry_run,
                before_value=str(shares),
                after_value="deleted",
            ))

    if not found_any and not silent:
        print("  No ghost positions found.")

    return entries


# ── CHECK 4: Fund total sanity ────────────────────────────────────────────────

def check_fund_sanity(
    traders: list[str],
    dry_run: bool,
    db=None,
    silent: bool = False,
) -> tuple[list[dict], float, bool]:
    """Returns (audit_entries, fund_total, sanity_ok)."""
    db = db or _default_supabase
    if not silent:
        print("\n" + "─" * 60)
        print("CHECK 4 — Fund total sanity")
        print("─" * 60)

    total = 0.0

    for trader_id in traders:
        cash_rows = (
            db.table("fund_balance")
            .select("cash")
            .eq("trader_id", trader_id)
            .execute()
        )
        cash = float(cash_rows.data[0]["cash"]) if cash_rows.data else 0.0

        positions = (
            db.table("portfolio_positions")
            .select("shares, avg_cost")
            .eq("trader_id", trader_id)
            .execute()
        ).data
        position_value = sum(float(p["shares"]) * float(p["avg_cost"]) for p in positions)
        trader_total = cash + position_value

        if not silent:
            print(
                f"  {trader_id:8s}  cash=${cash:>13,.2f}  "
                f"positions@avg_cost=${position_value:>13,.2f}  total=${trader_total:>13,.2f}"
            )
        total += trader_total

    lower = FUND_TOTAL_TARGET * (1 - FUND_SANITY_BAND)
    upper = FUND_TOTAL_TARGET * (1 + FUND_SANITY_BAND)
    within_band = lower <= total <= upper

    if not silent:
        print(f"\n  Fund total: ${total:,.2f}  (target ${FUND_TOTAL_TARGET:,.0f} ± {FUND_SANITY_BAND*100:.0f}%)")
        if within_band:
            print("  ✓ Fund total is within the expected band.")
        else:
            print()
            print("  !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
            print(f"  WARNING: Fund total ${total:,.2f} is OUTSIDE the expected")
            print(f"  band of ${lower:,.0f} – ${upper:,.0f}. Human review required.")
            print("  !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")

    entries = [_audit_entry(
        trader_id="all",
        check_type="fund_sanity",
        description=(
            f"fund_total=${total:,.2f} "
            f"band=[${lower:,.0f}, ${upper:,.0f}] "
            f"within_band={within_band}"
        ),
        corrected=False,
        before_value=f"{total:.6f}",
        after_value=None,
    )]

    return entries, total, within_band


# ── run_audit() — callable from main.py ──────────────────────────────────────

def run_audit(
    db=None,
    traders: list[str] | None = None,
    dry_run: bool = False,
) -> dict:
    """
    Run all four integrity checks silently and return a summary dict.

    Writes results to audit_log (fails gracefully if the table doesn't exist yet).
    Does not print to stdout — callers should use the returned dict or the
    meridian.audit logger.

    Returns:
        {
          ran_at, dry_run, traders,
          cash_corrections, position_corrections,
          fund_total, fund_sanity_ok,
          total_checks, total_corrections,
        }
    """
    _db = db or _default_supabase
    _traders = traders or TRADERS
    ran_at = datetime.now(timezone.utc).isoformat()
    all_entries: list[dict] = []

    try:
        all_entries += check_cash(_traders, dry_run, db=_db, silent=True)
    except Exception as e:
        _audit_log.error(f"audit check_cash failed: {e}")

    try:
        all_entries += check_positions(_traders, dry_run, db=_db, silent=True)
    except Exception as e:
        _audit_log.error(f"audit check_positions failed: {e}")

    try:
        all_entries += check_ghost_positions(_traders, dry_run, db=_db, silent=True)
    except Exception as e:
        _audit_log.error(f"audit check_ghost_positions failed: {e}")

    fund_total = 0.0
    sanity_ok = True
    try:
        sanity_entries, fund_total, sanity_ok = check_fund_sanity(
            _traders, dry_run, db=_db, silent=True
        )
        all_entries += sanity_entries
    except Exception as e:
        _audit_log.error(f"audit check_fund_sanity failed: {e}")

    if not dry_run and all_entries:
        try:
            _db.table("audit_log").insert(all_entries).execute()
        except Exception as e:
            _audit_log.warning(f"audit_log insert failed (table may not exist): {e}")

    corrections = sum(1 for e in all_entries if e["corrected"])
    cash_corrections = sum(
        1 for e in all_entries if e["check_type"] == "cash_drift" and e["corrected"]
    )
    pos_corrections = sum(
        1 for e in all_entries
        if e["check_type"] in ("position_mismatch", "ghost_position") and e["corrected"]
    )

    _audit_log.info(
        f"audit: checks={len(all_entries)} corrections={corrections} "
        f"fund_total=${fund_total:,.2f} sanity_ok={sanity_ok}"
    )

    return {
        "ran_at": ran_at,
        "dry_run": dry_run,
        "traders": _traders,
        "cash_corrections": cash_corrections,
        "position_corrections": pos_corrections,
        "fund_total": round(fund_total, 2),
        "fund_sanity_ok": sanity_ok,
        "total_checks": len(all_entries),
        "total_corrections": corrections,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Audit and correct fund integrity without resetting."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be corrected without writing anything.",
    )
    parser.add_argument(
        "--trader",
        metavar="TRADER_ID",
        help="Run for a single trader only (alex | jordan | casey).",
    )
    args = parser.parse_args()

    traders = [args.trader] if args.trader else TRADERS
    if args.trader and args.trader not in TRADERS:
        print(f"ERROR: Unknown trader {args.trader!r}. Choose from {TRADERS}.")
        sys.exit(1)

    db = _default_supabase
    mode = "DRY-RUN" if args.dry_run else "LIVE"
    print(f"\n{'='*60}")
    print(f"Meridian Capital — Fund Integrity Audit  [{mode}]")
    print(f"Traders: {', '.join(traders)}")
    print(f"{'='*60}")

    _cli_check_audit_log(db)

    all_entries: list[dict] = []

    all_entries += check_cash(traders, args.dry_run, db=db)
    all_entries += check_positions(traders, args.dry_run, db=db)
    all_entries += check_ghost_positions(traders, args.dry_run, db=db)
    sanity_entries, _, _ = check_fund_sanity(traders, args.dry_run, db=db)
    all_entries += sanity_entries

    if not args.dry_run:
        _cli_write_audit_log(db, all_entries)

    if _audit_buffer:
        print("\n" + "─" * 60)
        print("Buffered audit log entries (not persisted — create audit_log first):")
        print("─" * 60)
        for e in _audit_buffer:
            corrected_str = "corrected" if e["corrected"] else "no-op"
            print(
                f"  [{e['check_type']:20s}]  {e['trader_id']:8s}  "
                f"{corrected_str:10s}  {e['description']}"
            )

    corrections = sum(1 for e in all_entries if e["corrected"])
    print(f"\n{'='*60}")
    print(
        f"Audit complete.  checks={len(all_entries)}  "
        f"corrections={'(skipped — dry-run)' if args.dry_run else corrections}"
    )
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
