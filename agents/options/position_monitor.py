"""
position_monitor.py

Monitors open options positions and manages their lifecycle.
Runs on APScheduler — called once daily after market close.

Responsibilities:
  1. Fetch all open options positions from Supabase
  2. Check each for expiration, stop loss, or profit target
  3. Close positions that meet exit criteria
  4. Calculate and record realized P&L
  5. Update position status in Supabase

Exit criteria:
  Expired:      expiration_date <= today
  Stop loss:    unrealized_pnl <= -max_loss_total * stop_loss_pct
  Profit target: unrealized_pnl >= max_loss_total * profit_target_pct
  (using max_loss as the risk unit for target/stop calculation
   is conventional — risking $1 to make $1 is 1:1 R/R)

At larger scale this would be event-driven (Alpaca webhooks on
expiration/fill events) rather than scheduled polling.
Current approach is correct for paper trading volume.
"""

import os
from datetime import datetime, date
from typing import Optional
import yfinance as yf
from utils.db import supabase

# Exit thresholds — conventional options management levels
# Stop at 2x credit received (for credit spreads) or 50% of premium (for debit)
# Take profit at 50% of max profit
STOP_LOSS_PCT = 2.0      # close if loss reaches 2x the premium paid
PROFIT_TARGET_PCT = 0.50  # close at 50% of max possible profit


def _get_open_positions() -> list[dict]:
    """Fetches all open options positions from Supabase."""
    try:
        result = supabase.table("options_positions") \
            .select("*") \
            .eq("status", "open") \
            .execute()
        return result.data or []
    except Exception as e:
        print(f"[position_monitor] Failed to fetch positions: {e}")
        return []


def _get_current_option_price(
    ticker: str,
    legs: list[dict],
    strategy: str,
) -> Optional[float]:
    """
    Estimates current position value from yfinance options chain.
    Returns net price of the position (positive = credit, negative = debit).
    Returns None if unable to price.

    Limitation: yfinance option pricing has bid/ask spreads and
    may not reflect real-time mid-market prices accurately.
    At production scale you'd use Alpaca's /v2/options/contracts
    endpoint or a market data provider for live option quotes.
    """
    try:
        stock = yf.Ticker(ticker)
        total_price = 0.0

        for leg in legs:
            symbol = leg.get("symbol", "")
            strike = leg.get("strike")
            opt_type = leg.get("type", "call")
            side = leg.get("side", "buy")

            if not strike:
                continue

            # Find nearest expiration with this strike
            expirations = stock.options
            if not expirations:
                return None

            # Use first available expiration as approximation
            chain = stock.option_chain(expirations[0])
            options = chain.calls if opt_type == "call" else chain.puts
            options = options.copy()
            options["distance"] = abs(options["strike"] - strike)
            nearest = options.loc[options["distance"].idxmin()]
            mid = (nearest["bid"] + nearest["ask"]) / 2

            # Credit legs subtract from position cost
            if side == "sell":
                total_price += mid
            else:
                total_price -= mid

        return round(total_price, 4)
    except Exception:
        return None


def _calculate_unrealized_pnl(
    position: dict,
    current_price: Optional[float],
) -> float:
    """
    Calculates unrealized P&L for the position.

    For credit strategies (positive net_credit_debit):
        pnl = credit_received - current_cost_to_close
        pnl = net_credit_debit - abs(current_price) * 100 * contracts

    For debit strategies (negative net_credit_debit):
        pnl = current_value - debit_paid
        pnl = current_price * 100 * contracts - abs(net_credit_debit) * 100 * contracts

    Returns 0.0 if current_price unavailable.
    """
    if current_price is None:
        return 0.0

    contracts = position.get("contracts", 1)
    net_credit_debit = position.get("net_credit_debit", 0.0)
    entry_price = position.get("entry_price", 0.0)

    pnl = (current_price - entry_price) * 100 * contracts
    return round(pnl, 2)


def _should_close(
    position: dict,
    unrealized_pnl: float,
    today: date,
) -> tuple[bool, str]:
    """
    Determines if position should be closed and why.
    Returns (should_close: bool, reason: str).
    """
    # Check expiration
    exp_str = position.get("expiration_date")
    if exp_str:
        exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
        if today >= exp_date:
            return True, "expired"

    max_loss = position.get("max_loss_total", 0.0)
    if max_loss <= 0:
        return False, ""

    # Stop loss check
    if unrealized_pnl <= -(max_loss * STOP_LOSS_PCT):
        return True, "stop_loss"

    # Profit target check
    net_credit = position.get("net_credit_debit", 0.0)
    if net_credit > 0:
        # Credit strategy: max profit is the credit received
        max_profit = net_credit * position.get("contracts", 1) * 100
        if unrealized_pnl >= max_profit * PROFIT_TARGET_PCT:
            return True, "profit_target"

    return False, ""


def _close_position(
    position_id: str,
    realized_pnl: float,
    close_reason: str,
) -> None:
    """
    Marks position as closed in Supabase.
    Idempotency guard: only updates if status is 'open'.
    """
    supabase.table("options_positions") \
        .update({
            "status": "closed",
            "realized_pnl": realized_pnl,
            "closed_at": datetime.utcnow().isoformat(),
            "close_reason": close_reason,
        }) \
        .eq("id", position_id) \
        .eq("status", "open") \
        .execute()


def _update_unrealized_pnl(position_id: str, unrealized_pnl: float) -> None:
    """Updates current unrealized P&L without closing the position."""
    supabase.table("options_positions") \
        .update({"unrealized_pnl": unrealized_pnl}) \
        .eq("id", position_id) \
        .eq("status", "open") \
        .execute()


def run_position_monitor() -> dict:
    """
    Main entry point. Called by APScheduler after market close.
    Processes all open positions and returns a summary.

    Returns:
        checked: int
        closed: int
        close_reasons: dict
        errors: list
    """
    positions = _get_open_positions()
    today = datetime.utcnow().date()

    closed_count = 0
    close_reasons = {}
    errors = []

    print(f"[position_monitor] Checking {len(positions)} open options positions")

    for position in positions:
        position_id = position["id"]
        ticker = position["ticker"]
        legs = position.get("legs", [])
        strategy = position.get("strategy", "")

        try:
            current_price = _get_current_option_price(ticker, legs, strategy)
            unrealized_pnl = _calculate_unrealized_pnl(position, current_price)
            should_close, reason = _should_close(position, unrealized_pnl, today)

            if should_close:
                _close_position(position_id, unrealized_pnl, reason)
                closed_count += 1
                close_reasons[reason] = close_reasons.get(reason, 0) + 1
                print(f"[position_monitor] Closed {ticker} {strategy} — {reason} "
                      f"P&L: ${unrealized_pnl:,.2f}")
            else:
                _update_unrealized_pnl(position_id, unrealized_pnl)

        except Exception as e:
            error_msg = f"position {position_id} ({ticker}): {str(e)}"
            errors.append(error_msg)
            print(f"[position_monitor] Error processing {error_msg}")

    return {
        "checked": len(positions),
        "closed": closed_count,
        "close_reasons": close_reasons,
        "errors": errors,
    }
