from fastapi import FastAPI
from supabase import create_client
from dotenv import load_dotenv
import os

load_dotenv()

app = FastAPI(title="Hedge Fund Sim API")

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

@app.get("/")
def root():
    return {"status": "running", "fund": "Anthropic Capital (Simulated)"}

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