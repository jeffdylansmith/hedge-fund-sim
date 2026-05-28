from crewai import Agent, Task, Crew
from crewai.tools import tool
from utils.db import supabase
from dotenv import load_dotenv
import os

load_dotenv()

os.environ["ANTHROPIC_API_KEY"] = os.getenv("ANTHROPIC_API_KEY")

# --- TOOLS ---
# Portfolio manager reads what the other agents concluded
# and checks current positions before deciding

@tool("get_latest_analysis")
def get_latest_analysis() -> str:
    """Fetches the most recent reasoning from both analysts."""
    result = (
        supabase.table("agent_decisions")
        .select("agent_name, reasoning, created_at")
        .in_("agent_name", ["News Analyst", "Technical Analyst"])
        .order("created_at", desc=True)
        .limit(2)
        .execute()
    )

    if not result.data:
        return "No analyst reports found."

    output = []
    for row in result.data:
        output.append(f"\n=== {row['agent_name']} ({row['created_at']}) ===\n{row['reasoning']}")
    return "\n".join(output)


@tool("get_current_positions")
def get_current_positions() -> str:
    """Returns current portfolio positions and cash balance."""
    positions = supabase.table("portfolio_positions").select("*").execute()

    if not positions.data:
        return "Portfolio is empty. Starting capital: $100,000 cash."

    lines = ["Current Positions:"]
    for p in positions.data:
        lines.append(f"  {p['ticker']}: {p['shares']} shares @ avg cost ${p['avg_cost']:.2f}")
    return "\n".join(lines)


@tool("get_current_price")
def get_current_price(ticker: str) -> str:
    """Gets the most recent price for a ticker."""
    result = (
        supabase.table("prices")
        .select("close, timestamp")
        .eq("ticker", ticker.upper())
        .order("timestamp", desc=True)
        .limit(1)
        .execute()
    )

    if not result.data:
        return f"No price data for {ticker}"

    return f"{ticker.upper()}: ${result.data[0]['close']:.2f} (as of {result.data[0]['timestamp']})"


# --- AGENT DEFINITION ---
# ← Change backstory to make the manager more aggressive/conservative
# ← Change goal to shift portfolio strategy (growth vs defensive vs balanced)

portfolio_manager = Agent(
    role="Portfolio Manager",
    goal="Make rational, data-driven buy/hold/sell decisions based on "
         "analyst reports, current positions, and risk management principles. "
         "Preserve capital first, generate returns second.",
    backstory="""You are a disciplined portfolio manager with 20 years of 
    experience running a mid-sized hedge fund. You have lived through the 
    2008 crash and the 2020 pandemic and your number one rule is never let 
    a bad position get worse. You take analyst recommendations seriously but 
    always apply your own judgment. You never go all-in on a single position, 
    always maintain some cash reserve, and you are deeply skeptical of 
    consensus trades. You think in terms of risk/reward ratios and always 
    ask yourself what happens if you are wrong.""",                  # ← personality lives here
    tools=[get_latest_analysis, get_current_positions, get_current_price],
    llm="claude-haiku-4-5",
    verbose=True
)

portfolio_task = Task(
    description="""
    1. Read the latest reports from the News Analyst and Technical Analyst
    2. Check current portfolio positions and available cash
    3. For each ticker in the watchlist make a decision: BUY, SELL, or HOLD
    4. For BUY orders: max 15% of $100,000 total capital per position
       meaning max $15,000 per trade. Calculate shares as floor($15000 / price)
    5. For SELL orders: sell all shares currently held
    6. Respond ONLY with a JSON array, no other text, no markdown backticks

    Example format:
    [
      {
        "ticker": "AAPL",
        "action": "BUY",
        "shares": 48,
        "reasoning": "Strong momentum, bullish MACD crossover"
      },
      {
        "ticker": "MSFT",
        "action": "HOLD",
        "shares": 0,
        "reasoning": "Neutral signals, waiting for clearer direction"
      }
    ]
    """,
    expected_output="A valid JSON array of decisions for each ticker, nothing else.",
    agent=portfolio_manager
)


def run_portfolio_management():
    crew = Crew(
        agents=[portfolio_manager],
        tasks=[portfolio_task],
        verbose=True
    )

    print("Running portfolio management...")
    result = crew.kickoff()

    # Log the decision
    supabase.table("agent_decisions").insert({
        "agent_name": "Portfolio Manager",
        "ticker": None,
        "action": "portfolio_review",
        "reasoning": str(result),
        "confidence": None
    }).execute()

    print("\n--- PORTFOLIO MANAGER OUTPUT ---")
    print(result)
    return result


if __name__ == "__main__":
    run_portfolio_management()