import yfinance as yf
import pandas as pd
from supabase import create_client
from dotenv import load_dotenv
from datetime import datetime, timezone
import os

load_dotenv()

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY")
)

def get_watchlist():
    result = supabase.table("watchlist").select("ticker").execute()
    return [row["ticker"] for row in result.data]

def fetch_and_store_prices(ticker: str):
    """Fetch last 2 days of hourly data and upsert into Supabase."""
    try:
        df = yf.download(ticker, period="2d", interval="1h", progress=False, auto_adjust=True, multi_level_index=False)      
        if df.empty:
            print(f"No data for {ticker}")
            return

        records = []
        for timestamp, row in df.iterrows():
            records.append({
                "ticker": ticker,
                "timestamp": timestamp.isoformat(),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": int(row["Volume"])
            })

        # Upsert: insert new, skip duplicates (based on ticker+timestamp unique constraint)
        supabase.table("prices").upsert(records, on_conflict="ticker,timestamp").execute()
        print(f"✓ {ticker}: {len(records)} records stored")

    except Exception as e:
        print(f"✗ {ticker}: {e}")

if __name__ == "__main__":
    tickers = get_watchlist()
    print(f"Fetching prices for {len(tickers)} tickers...")
    for ticker in tickers:
        fetch_and_store_prices(ticker)
    print("Done.")