from newsapi import NewsApiClient
from supabase import create_client
from dotenv import load_dotenv
from datetime import datetime, timedelta
import os

load_dotenv()

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
newsapi = NewsApiClient(api_key=os.getenv("NEWS_API_KEY"))

def fetch_market_news():
    """Pull general finance/market headlines from the last 24 hours."""
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
    
    response = newsapi.get_everything(
        q="stock market OR earnings OR Federal Reserve OR inflation",
        language="en",
        sort_by="publishedAt",
        from_param=yesterday,
        page_size=50
    )

    records = []
    for article in response.get("articles", []):
        records.append({
            "ticker": None,       # general market news, not ticker-specific
            "headline": article["title"],
            "source": article["source"]["name"],
            "url": article["url"],
            "published_at": article["publishedAt"],
            "sentiment_score": None   # agents will fill this in Phase 2
        })

    if records:
        supabase.table("news_items").insert(records).execute()
        print(f"✓ Stored {len(records)} news items")

if __name__ == "__main__":
    fetch_market_news()