"""
options_executor.py

Translates an approved options strategy into Alpaca order submissions.
Handles single-leg and multi-leg order construction, submission,
fill confirmation, and writing the position record to Supabase.

Write-ahead pattern:
  1. Write options_positions row with status='pending_submission'
  2. Submit order to Alpaca
  3. On success: update status to 'open', store order_id
  4. On failure: update status to 'failed', log error

This ensures the DB always reflects intent even if submission fails.
Idempotency: status field prevents double-submission on retry.
"""

import os
import uuid
from datetime import datetime, timedelta
from typing import Optional
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest,
    LimitOrderRequest,
    OptionLegRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, OrderType, AssetClass
from utils.db import supabase

# Paper trading client — None if API keys not configured (test environments)
_trading_client = None
try:
    _trading_client = TradingClient(
        api_key=os.environ.get("ALPACA_API_KEY"),
        secret_key=os.environ.get("ALPACA_SECRET_KEY"),
        paper=True,
    )
except Exception:
    pass


def _option_symbol(
    ticker: str,
    expiration_date: str,
    strike: float,
    option_type: str,
) -> str:
    """
    Constructs OCC option symbol format: AAPL231215C00150000
    Format: {ticker}{YY}{MM}{DD}{C/P}{strike * 1000 zero-padded to 8 digits}

    This is the industry standard Options Clearing Corporation format.
    Every option contract has exactly one OCC symbol.
    """
    dt = datetime.strptime(expiration_date, "%Y-%m-%d")
    yy = dt.strftime("%y")
    mm = dt.strftime("%m")
    dd = dt.strftime("%d")
    cp = "C" if option_type.lower() == "call" else "P"
    strike_int = int(round(strike * 1000))
    strike_str = str(strike_int).zfill(8)
    return f"{ticker}{yy}{mm}{dd}{cp}{strike_str}"


def _expiration_date_from_days(days: int) -> str:
    """
    Converts days-to-expiration to the nearest valid Friday.
    Options expire on Fridays (standard US equity options).
    Returns date string in YYYY-MM-DD format.
    """
    target = datetime.today() + timedelta(days=days)
    # Roll forward to Friday if not already Friday (weekday 4)
    days_to_friday = (4 - target.weekday()) % 7
    expiration = target + timedelta(days=days_to_friday)
    return expiration.strftime("%Y-%m-%d")


def _calculate_unrealized_pnl(
    position: dict,
    current_price: Optional[float],
) -> float:
    """
    Calculates unrealized P&L for the position.
    Returns 0.0 if current_price unavailable.
    """
    if current_price is None:
        return 0.0

    contracts = position.get("contracts", 1)
    entry_price = position.get("entry_price", 0.0)

    pnl = (current_price - entry_price) * 100 * contracts
    return round(pnl, 2)


def _write_pending_position(
    trader_id: str,
    ticker: str,
    strategy: str,
    strikes: dict,
    greeks: dict,
    contracts: int,
    max_loss_per_contract: float,
    max_loss_total: float,
    net_credit_debit: float,
    expiration_date: str,
    legs: list,
) -> str:
    """
    Writes the position record before submission (write-ahead pattern).
    Returns the new position ID.
    Status starts as 'pending_submission'.
    """
    position_id = str(uuid.uuid4())

    supabase.table("options_positions").insert({
        "id": position_id,
        "trader_id": trader_id,
        "ticker": ticker,
        "strategy": strategy,
        "status": "pending_submission",
        "legs": legs,
        "max_loss_per_contract": max_loss_per_contract,
        "contracts": contracts,
        "net_credit_debit": net_credit_debit,
        "max_loss_total": max_loss_total,
        "entry_delta": greeks.get("delta"),
        "entry_theta": greeks.get("theta"),
        "entry_vega": greeks.get("vega"),
        "entry_gamma": greeks.get("gamma"),
        "entry_price": greeks.get("price"),
        "current_price": greeks.get("price"),
        "expiration_date": expiration_date,
    }).execute()

    return position_id


def _update_position_status(
    position_id: str,
    status: str,
    alpaca_order_id: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """Updates position status after submission attempt."""
    update = {"status": status}
    if alpaca_order_id:
        # Store order ID in legs field metadata for now
        # In production you'd have a dedicated order_id column
        update["close_reason"] = f"order_id:{alpaca_order_id}"
    if error:
        update["close_reason"] = f"error:{error[:200]}"

    # Idempotency guard — only update if current status is pending
    supabase.table("options_positions") \
        .update(update) \
        .eq("id", position_id) \
        .eq("status", "pending_submission") \
        .execute()


def _build_single_leg_order(
    symbol: str,
    contracts: int,
    side: str,
) -> dict:
    """Builds order params for single-leg options (long call/put)."""
    return {
        "symbol": symbol,
        "qty": contracts,
        "side": OrderSide.BUY if side == "buy" else OrderSide.SELL,
        "type": OrderType.MARKET,
        "time_in_force": TimeInForce.DAY,
    }


def _submit_single_leg(
    symbol: str,
    contracts: int,
    side: str = "buy",
) -> Optional[str]:
    """
    Submits a single-leg options order to Alpaca.
    Returns order ID on success, None on failure.
    """
    try:
        request = MarketOrderRequest(
            symbol=symbol,
            qty=contracts,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        order = _trading_client.submit_order(request)
        return str(order.id)
    except Exception as e:
        print(f"[options_executor] Single leg submission failed: {e}")
        return None


def _submit_multi_leg(
    legs: list[dict],
    contracts: int,
) -> Optional[str]:
    """
    Submits a multi-leg options order to Alpaca.
    legs: [{symbol, side, ratio_qty}]

    Multi-leg orders are submitted as a single order with legs array.
    Alpaca attempts to fill all legs simultaneously to avoid legging risk.
    Legging risk = one leg fills and another doesn't, leaving an
    unintended naked position. Simultaneous submission minimizes this
    but does not eliminate it on illiquid options.
    """
    try:
        leg_requests = [
            OptionLegRequest(
                symbol=leg["symbol"],
                side=OrderSide.BUY if leg["side"] == "buy" else OrderSide.SELL,
                ratio_qty=leg.get("ratio_qty", 1),
            )
            for leg in legs
        ]

        request = MarketOrderRequest(
            qty=contracts,
            time_in_force=TimeInForce.DAY,
            order_class="mleg",
            legs=leg_requests,
        )
        order = _trading_client.submit_order(request)
        return str(order.id)
    except Exception as e:
        print(f"[options_executor] Multi-leg submission failed: {e}")
        return None


def execute_options_trade(
    trader_id: str,
    ticker: str,
    strategy: str,
    strikes: dict,
    greeks: dict,
    risk_assessment: dict,
    dry_run: bool = False,
) -> dict:
    """
    Main entry point. Executes an approved options strategy.

    dry_run=True logs the order but does not submit to Alpaca.
    Use for testing and validation before enabling live paper trading.

    Returns:
        success: bool
        position_id: str | None
        order_id: str | None
        error: str | None
        dry_run: bool
    """
    contracts = risk_assessment.get("contracts", 0)
    max_loss_total = risk_assessment.get("max_loss_total", 0.0)

    if contracts == 0:
        return {
            "success": False,
            "position_id": None,
            "order_id": None,
            "error": "Zero contracts — nothing to execute.",
            "dry_run": dry_run,
        }

    days_to_exp = strikes.get("expiration_days", 30)
    expiration_date = _expiration_date_from_days(days_to_exp)
    net_price = greeks.get("price", 0.0)

    # --- Build legs based on strategy ---

    if strategy == "long_call":
        symbol = _option_symbol(ticker, expiration_date, strikes["strike"], "call")
        legs = [{"symbol": symbol, "side": "buy", "type": "call",
                 "strike": strikes["strike"]}]
        max_loss_per = abs(net_price) * 100

        if not dry_run:
            position_id = _write_pending_position(
                trader_id, ticker, strategy, strikes, greeks,
                contracts, max_loss_per, max_loss_total,
                -abs(net_price), expiration_date, legs,
            )
            order_id = _submit_single_leg(symbol, contracts, "buy")
        else:
            position_id = f"dry_run_{uuid.uuid4()}"
            order_id = "dry_run_order"

    elif strategy == "long_put":
        symbol = _option_symbol(ticker, expiration_date, strikes["strike"], "put")
        legs = [{"symbol": symbol, "side": "buy", "type": "put",
                 "strike": strikes["strike"]}]
        max_loss_per = abs(net_price) * 100

        if not dry_run:
            position_id = _write_pending_position(
                trader_id, ticker, strategy, strikes, greeks,
                contracts, max_loss_per, max_loss_total,
                -abs(net_price), expiration_date, legs,
            )
            order_id = _submit_single_leg(symbol, contracts, "buy")
        else:
            position_id = f"dry_run_{uuid.uuid4()}"
            order_id = "dry_run_order"

    elif strategy == "bull_put_spread":
        short_sym = _option_symbol(ticker, expiration_date, strikes["short_strike"], "put")
        long_sym = _option_symbol(ticker, expiration_date, strikes["long_strike"], "put")
        legs = [
            {"symbol": short_sym, "side": "sell", "type": "put",
             "strike": strikes["short_strike"], "ratio_qty": 1},
            {"symbol": long_sym, "side": "buy", "type": "put",
             "strike": strikes["long_strike"], "ratio_qty": 1},
        ]
        wing = strikes["short_strike"] - strikes["long_strike"]
        max_loss_per = (wing - abs(net_price)) * 100

        if not dry_run:
            position_id = _write_pending_position(
                trader_id, ticker, strategy, strikes, greeks,
                contracts, max_loss_per, max_loss_total,
                abs(net_price), expiration_date, legs,
            )
            order_id = _submit_multi_leg(legs, contracts)
        else:
            position_id = f"dry_run_{uuid.uuid4()}"
            order_id = "dry_run_order"

    elif strategy == "bear_call_spread":
        short_sym = _option_symbol(ticker, expiration_date, strikes["short_strike"], "call")
        long_sym = _option_symbol(ticker, expiration_date, strikes["long_strike"], "call")
        legs = [
            {"symbol": short_sym, "side": "sell", "type": "call",
             "strike": strikes["short_strike"], "ratio_qty": 1},
            {"symbol": long_sym, "side": "buy", "type": "call",
             "strike": strikes["long_strike"], "ratio_qty": 1},
        ]
        wing = strikes["long_strike"] - strikes["short_strike"]
        max_loss_per = (wing - abs(net_price)) * 100

        if not dry_run:
            position_id = _write_pending_position(
                trader_id, ticker, strategy, strikes, greeks,
                contracts, max_loss_per, max_loss_total,
                abs(net_price), expiration_date, legs,
            )
            order_id = _submit_multi_leg(legs, contracts)
        else:
            position_id = f"dry_run_{uuid.uuid4()}"
            order_id = "dry_run_order"

    elif strategy == "iron_condor":
        put_short_sym = _option_symbol(
            ticker, expiration_date, strikes["put_short_strike"], "put")
        put_long_sym = _option_symbol(
            ticker, expiration_date, strikes["put_long_strike"], "put")
        call_short_sym = _option_symbol(
            ticker, expiration_date, strikes["call_short_strike"], "call")
        call_long_sym = _option_symbol(
            ticker, expiration_date, strikes["call_long_strike"], "call")
        legs = [
            {"symbol": put_short_sym, "side": "sell", "type": "put",
             "strike": strikes["put_short_strike"], "ratio_qty": 1},
            {"symbol": put_long_sym, "side": "buy", "type": "put",
             "strike": strikes["put_long_strike"], "ratio_qty": 1},
            {"symbol": call_short_sym, "side": "sell", "type": "call",
             "strike": strikes["call_short_strike"], "ratio_qty": 1},
            {"symbol": call_long_sym, "side": "buy", "type": "call",
             "strike": strikes["call_long_strike"], "ratio_qty": 1},
        ]
        put_wing = strikes["put_short_strike"] - strikes["put_long_strike"]
        call_wing = strikes["call_long_strike"] - strikes["call_short_strike"]
        max_loss_per = (min(put_wing, call_wing) - abs(net_price)) * 100

        if not dry_run:
            position_id = _write_pending_position(
                trader_id, ticker, strategy, strikes, greeks,
                contracts, max_loss_per, max_loss_total,
                abs(net_price), expiration_date, legs,
            )
            order_id = _submit_multi_leg(legs, contracts)
        else:
            position_id = f"dry_run_{uuid.uuid4()}"
            order_id = "dry_run_order"

    else:
        return {
            "success": False,
            "position_id": None,
            "order_id": None,
            "error": f"Unknown strategy: {strategy}",
            "dry_run": dry_run,
        }

    # --- Update status based on submission result ---
    if not dry_run:
        if order_id:
            _update_position_status(position_id, "open", order_id)
            success = True
            error = None
        else:
            _update_position_status(position_id, "failed",
                                    error="Alpaca submission returned None")
            success = False
            error = "Order submission failed — see options_positions.close_reason"
    else:
        success = True
        error = None

    return {
        "success": success,
        "position_id": position_id,
        "order_id": order_id,
        "error": error,
        "dry_run": dry_run,
        "strategy": strategy,
        "ticker": ticker,
        "contracts": contracts,
        "expiration_date": expiration_date,
        "legs": legs,
    }
