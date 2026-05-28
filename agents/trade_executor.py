from utils.db import supabase
from dotenv import load_dotenv
from datetime import datetime
import re
import json
import anthropic
import os

load_dotenv()

def get_latest_portfolio_decision() -> str:
    """Fetches the most recent portfolio manager reasoning."""
    result = (
        supabase.table("agent_decisions")
        .select("reasoning, created_at")
        .eq("agent_name", "Portfolio Manager")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )

    if not result.data:
        return None

    return result.data[0]["reasoning"]


def get_current_price(ticker: str) -> float:
    """Gets most recent close price for a ticker."""
    result = (
        supabase.table("prices")
        .select("close")
        .eq("ticker", ticker.upper())
        .order("timestamp", desc=True)
        .limit(1)
        .execute()
    )

    if not result.data:
        return None

    return float(result.data[0]["close"])


def get_watchlist() -> list:
    """Returns all tickers we are tracking."""
    result = supabase.table("watchlist").select("ticker").execute()
    return [row["ticker"] for row in result.data]


def parse_decisions_from_reasoning(reasoning: str, watchlist: list) -> list:
    """
    Parses structured JSON output from the portfolio manager.
    Falls back to empty list if parsing fails.
    """
    try:
        # Strip any accidental markdown the LLM might add
        clean = reasoning.strip()
        clean = re.sub(r'^```json|^```|```$', '', clean, flags=re.MULTILINE).strip()

        decisions = json.loads(clean)

        # Validate structure
        valid = []
        for d in decisions:
            if all(k in d for k in ["ticker", "action", "shares"]):
                valid.append({
                    "ticker": d["ticker"].upper(),
                    "action": d["action"].upper(),
                    "shares": int(d["shares"]) if d["shares"] else None,
                    "reasoning": d.get("reasoning", "")
                })
        return valid

    except Exception as e:
        print(f"Failed to parse JSON decisions: {e}")
        print(f"Raw output was:\n{reasoning[:500]}")
        return []


def submit_pending_decisions(decisions: list, reasoning: str):
    if not decisions:
        print("No decisions to submit.")
        return

    submitted = 0
    for decision in decisions:
        if decision["action"] == "HOLD":
            continue

        price = get_current_price(decision["ticker"])

        record = {
            "agent_name": "Portfolio Manager",
            "ticker": decision["ticker"],
            "action": decision["action"],
            "shares": decision["shares"],
            "price_at_decision": price,
            "reasoning": decision.get("reasoning", "")[:500],  # per-trade reasoning now
            "status": "pending"
        }

        supabase.table("pending_decisions").insert(record).execute()
        print(f"  → Pending: {decision['action']} {decision['shares'] or '?'} "
              f"shares of {decision['ticker']} @ ${price or 'unknown'}")
        submitted += 1

    print(f"\n{submitted} decision(s) submitted for approval.")

def approve_decision(decision_id: int):
    """
    Approves a pending decision and executes the trade.
    Call this manually or hook it to your dashboard approval button later.
    """
    # Fetch the pending decision
    result = (
        supabase.table("pending_decisions")
        .select("*")
        .eq("id", decision_id)
        .eq("status", "pending")
        .execute()
    )

    if not result.data:
        print(f"No pending decision found with id {decision_id}")
        return

    decision = result.data[0]
    ticker = decision["ticker"]
    action = decision["action"]
    shares = decision["shares"]
    price = decision["price_at_decision"]

    if not shares or not price:
        print(f"Missing shares or price for decision {decision_id} - cannot execute")
        return

    total_value = shares * price

    # Write to trades table
    supabase.table("trades").insert({
        "ticker": ticker,
        "action": action.lower(),
        "shares": shares,
        "price": price,
        "total_value": total_value,
        "executed_at": datetime.now().isoformat()
    }).execute()

    # Update or insert portfolio position
    existing = (
        supabase.table("portfolio_positions")
        .select("*")
        .eq("ticker", ticker)
        .execute()
    )

    if existing.data:
        current = existing.data[0]
        if action == "BUY":
            new_shares = current["shares"] + shares
            new_avg_cost = (
                (current["shares"] * current["avg_cost"]) + total_value
            ) / new_shares
            supabase.table("portfolio_positions").update({
                "shares": new_shares,
                "avg_cost": new_avg_cost,
                "last_updated": datetime.now().isoformat()
            }).eq("ticker", ticker).execute()
        elif action == "SELL":
            new_shares = current["shares"] - shares
            if new_shares <= 0:
                supabase.table("portfolio_positions").delete().eq("ticker", ticker).execute()
            else:
                supabase.table("portfolio_positions").update({
                    "shares": new_shares,
                    "last_updated": datetime.now().isoformat()
                }).eq("ticker", ticker).execute()
    else:
        if action == "BUY":
            supabase.table("portfolio_positions").insert({
                "ticker": ticker,
                "shares": shares,
                "avg_cost": price,
                "last_updated": datetime.now().isoformat()
            }).execute()

    # Mark decision as approved
    supabase.table("pending_decisions").update({
        "status": "approved",
        "reviewed_at": datetime.now().isoformat()
    }).eq("id", decision_id).execute()

    print(f"✓ Executed: {action} {shares} shares of {ticker} @ ${price:.2f} "
          f"(Total: ${total_value:,.2f})")


def reject_decision(decision_id: int):
    """Rejects a pending decision without executing."""
    supabase.table("pending_decisions").update({
        "status": "rejected",
        "reviewed_at": datetime.now().isoformat()
    }).eq("id", decision_id).execute()
    print(f"✗ Rejected decision {decision_id}")

def force_json_from_reasoning(reasoning: str, watchlist: list) -> list:
    """
    Takes free-text portfolio manager output and uses a direct
    Anthropic API call to reformat it as clean JSON.
    This is a cheap formatting call, not a reasoning call.
    """
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    tickers = ", ".join(watchlist)

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1000,
        messages=[
            {
                "role": "user",
                "content": f"""Extract the trading decisions from this portfolio manager report 
and return ONLY a JSON array. No markdown, no explanation, just the array.

Tickers to include: {tickers}
If a ticker is not mentioned, mark it as HOLD with 0 shares.

Format exactly like this:
[{{"ticker":"AAPL","action":"BUY","shares":48,"reasoning":"brief reason"}},
 {{"ticker":"MSFT","action":"HOLD","shares":0,"reasoning":"brief reason"}}]

Portfolio manager report:
{reasoning[:2000]}

Return only the JSON array starting with [ and ending with ]"""
            }
        ]
    )

    raw = message.content[0].text.strip()
    clean = re.sub(r'^```json|^```|```$', '', raw, flags=re.MULTILINE).strip()
    return json.loads(clean)

def run_trade_parser():
    print("Parsing latest portfolio manager decisions...")

    reasoning = get_latest_portfolio_decision()
    if not reasoning:
        print("No portfolio manager decisions found.")
        return

    watchlist = get_watchlist()

    # Try clean JSON parse first
    decisions = parse_decisions_from_reasoning(reasoning, watchlist)

    # If that fails, use the formatter
    if not decisions:
        print("Clean parse failed - using AI formatter...")
        try:
            decisions = force_json_from_reasoning(reasoning, watchlist)
            print(f"Formatter extracted {len(decisions)} decisions")
        except Exception as e:
            print(f"Formatter also failed: {e}")
            return

    print(f"\nFound {len(decisions)} ticker decisions:")
    for d in decisions:
        print(f"  {d['ticker']}: {d['action']} "
              f"({'?' if not d['shares'] else d['shares']} shares)")

    print("\nSubmitting non-HOLD decisions for approval...")
    submit_pending_decisions(decisions, reasoning)

    print("\nTo approve a decision run:")
    print("  from agents.trade_executor import approve_decision")
    print("  approve_decision(decision_id)")


if __name__ == "__main__":
    run_trade_parser()