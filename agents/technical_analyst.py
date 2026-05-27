from crewai import Agent, Task
from crewai.tools import tool
from utils.db import supabase
from dotenv import load_dotenv
import pandas as pd
import os

load_dotenv()

os.environ["ANTHROPIC_API_KEY"] = os.getenv("ANTHROPIC_API_KEY")

# --- TOOLS ---
# These functions do the quant math before handing results to the LLM
# Add new indicators here as you learn more quant concepts

@tool("get_price_data")
def get_price_data(ticker: str) -> str:
    """Fetches recent hourly price data and computes technical indicators for a ticker."""
    result = (
        supabase.table("prices")
        .select("*")
        .eq("ticker", ticker.upper())
        .order("timestamp", desc=True)
        .limit(48)
        .execute()
    )

    if not result.data:
        return f"No price data found for {ticker}"

    # Build a dataframe from our stored prices
    df = pd.DataFrame(result.data)
    df = df.sort_values("timestamp")
    df["close"] = pd.to_numeric(df["close"])
    df["volume"] = pd.to_numeric(df["volume"])

    # --- INDICATORS ---
    # This is where quant math lives - add new indicators here
    # SMA: Simple Moving Average - smooths out price noise
    df["sma_10"] = df["close"].rolling(window=10).mean()
    df["sma_20"] = df["close"].rolling(window=20).mean()

    # RSI: Relative Strength Index - measures momentum
    # Above 70 = overbought, below 30 = oversold
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(window=14).mean()
    loss = -delta.clip(upper=0).rolling(window=14).mean()
    rs = gain / loss
    df["rsi"] = 100 - (100 / (1 + rs))

    # MACD: Moving Average Convergence Divergence - trend signal
    # Positive = bullish momentum, negative = bearish
    exp12 = df["close"].ewm(span=12, adjust=False).mean()
    exp26 = df["close"].ewm(span=26, adjust=False).mean()
    df["macd"] = exp12 - exp26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()

    # Get the most recent row for the summary
    latest = df.iloc[-1]
    prev = df.iloc[-2]

    price_change = ((latest["close"] - prev["close"]) / prev["close"]) * 100

    summary = f"""
    Ticker: {ticker.upper()}
    Current Price: ${latest['close']:.2f}
    1-Period Change: {price_change:+.2f}%
    Volume: {latest['volume']:,}

    --- Indicators ---
    SMA 10: ${latest['sma_10']:.2f}  ({'Price ABOVE' if latest['close'] > latest['sma_10'] else 'Price BELOW'} SMA10)
    SMA 20: ${latest['sma_20']:.2f}  ({'Price ABOVE' if latest['close'] > latest['sma_20'] else 'Price BELOW'} SMA20)
    RSI(14): {latest['rsi']:.1f}     ({'Overbought' if latest['rsi'] > 70 else 'Oversold' if latest['rsi'] < 30 else 'Neutral'})
    MACD: {latest['macd']:.4f}
    MACD Signal: {latest['macd_signal']:.4f}
    MACD Cross: {'Bullish' if latest['macd'] > latest['macd_signal'] else 'Bearish'}
    """
    return summary


@tool("get_watchlist")
def get_watchlist() -> str:
    """Returns the list of tickers we are tracking."""
    result = supabase.table("watchlist").select("ticker, company_name").execute()
    return ", ".join([f"{r['ticker']} ({r['company_name']})" for r in result.data])


# --- AGENT DEFINITION ---
# ← Change backstory to shift analytical style
# e.g. make them more aggressive, more conservative, focused on specific patterns

technical_analyst = Agent(
    role="Technical Analyst",
    goal="Analyze price data and technical indicators to identify "
         "trading signals and trend direction for each ticker.",
    backstory="""You are a quantitative technical analyst with deep experience 
    in price action and momentum indicators. You rely on data, not emotion. 
    You understand that no single indicator tells the whole story and always 
    look for confluence — multiple indicators agreeing before calling a signal. 
    You are particularly focused on RSI divergence, MACD crossovers, and 
    price relative to moving averages. You express conviction levels clearly 
    and always note when data is insufficient to draw conclusions.""",   # ← personality lives here
    tools=[get_watchlist, get_price_data],
    llm="claude-haiku-4-5",
    verbose=True
)

technical_task = Task(
    description="""
    1. Get the full watchlist of tickers
    2. For each ticker, fetch price data and technical indicators
    3. Identify which tickers show the strongest bullish signals
    4. Identify which tickers show the strongest bearish signals
    5. Flag any tickers showing unusual volume or price action
    6. Rate your conviction level (low/medium/high) for each signal
    """,
    expected_output="""
    A structured technical analysis with:
    - Bullish signals: ticker, key indicators supporting the call, conviction level
    - Bearish signals: ticker, key indicators supporting the call, conviction level  
    - Any unusual activity worth flagging
    - A brief overall market technical picture (2-3 sentences)
    """,
    agent=technical_analyst
)


def run_technical_analysis():
    from crewai import Crew
    crew = Crew(
        agents=[technical_analyst],
        tasks=[technical_task],
        verbose=True
    )

    print("Running technical analysis...")
    result = crew.kickoff()

    supabase.table("agent_decisions").insert({
        "agent_name": "Technical Analyst",
        "ticker": None,
        "action": "analyze",
        "reasoning": str(result),
        "confidence": None
    }).execute()

    print("\n--- TECHNICAL ANALYST OUTPUT ---")
    print(result)
    return result


if __name__ == "__main__":
    run_technical_analysis()