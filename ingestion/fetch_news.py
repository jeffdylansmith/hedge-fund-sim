from newsapi import NewsApiClient
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta
from utils.db import supabase
import os
import yfinance as yf

load_dotenv()

newsapi = NewsApiClient(api_key=os.getenv("NEWS_API_KEY"))

_SPAM_PHRASES = [
    "INVESTOR ALERT",
    "DEADLINE:",
    "class action",
    "Pomerantz",
    "Rosen Law",
]

_ticker_names_cache: dict = {}


def get_ticker_names() -> dict:
    global _ticker_names_cache
    if _ticker_names_cache:
        return _ticker_names_cache
    rows = supabase.table("watchlist").select("ticker").execute()
    result = {}
    for row in rows.data:
        ticker = row["ticker"]
        try:
            info = yf.Ticker(ticker).info
            name = info.get("shortName") or info.get("longName") or ticker
        except Exception:
            name = ticker
        result[ticker] = [name, ticker]
    _ticker_names_cache = result
    return result


def _is_spam(headline: str) -> bool:
    low = headline.lower()
    return any(phrase.lower() in low for phrase in _SPAM_PHRASES)


def _is_relevant(ticker: str, headline: str, description: str) -> bool:
    terms = get_ticker_names().get(ticker, [ticker])
    text = f"{headline} {description or ''}".lower()
    return any(term.lower() in text for term in terms)


def fetch_market_news():
    from_dt = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")

    watchlist = supabase.table("watchlist").select("ticker, company_name").execute()
    tickers = watchlist.data

    if not tickers:
        print("fetch_news: watchlist is empty, nothing to fetch")
        return

    # Pre-fetch all known URLs to deduplicate across tickers and against existing rows
    existing_urls = set()
    url_rows = supabase.table("news_items").select("url").execute()
    for row in url_rows.data:
        if row.get("url"):
            existing_urls.add(row["url"])

    total_stored = 0
    total_skipped_spam = 0
    total_skipped_irrelevant = 0
    total_skipped_duplicate = 0
    per_ticker: dict[str, int] = {}
    seen_urls_this_run: set[str] = set()

    for row in tickers:
        ticker = row["ticker"]
        company = row.get("company_name") or ""
        query = f"{ticker} OR {company}" if company else ticker

        try:
            response = newsapi.get_everything(
                q=query,
                language="en",
                sort_by="publishedAt",
                from_param=from_dt,
                page_size=10,
                domains="reuters.com,apnews.com,bloomberg.com,wsj.com,ft.com,cnbc.com,marketwatch.com,finance.yahoo.com,investing.com,seekingalpha.com,barrons.com,thestreet.com",
            )
        except Exception as e:
            print(f"  fetch_news: NewsAPI error for {ticker}: {e}")
            continue

        records = []
        for article in response.get("articles", []):
            headline = article.get("title") or ""
            description = article.get("description") or ""
            url = article.get("url") or ""

            if _is_spam(headline):
                total_skipped_spam += 1
                continue

            if url in existing_urls or url in seen_urls_this_run:
                total_skipped_duplicate += 1
                continue

            if not _is_relevant(ticker, headline, description):
                total_skipped_irrelevant += 1
                continue

            seen_urls_this_run.add(url)
            records.append({
                "ticker": ticker,
                "headline": headline,
                "source": article["source"]["name"],
                "url": url,
                "published_at": article["publishedAt"],
                "sentiment_score": None,
            })

        if records:
            supabase.table("news_items").insert(records).execute()
            total_stored += len(records)
            per_ticker[ticker] = len(records)

    print(
        f"fetch_news: stored={total_stored} "
        f"skipped_duplicate={total_skipped_duplicate} "
        f"skipped_irrelevant={total_skipped_irrelevant} "
        f"skipped_spam={total_skipped_spam} "
        f"tickers_with_news={len(per_ticker)}/{len(tickers)}"
    )
    return per_ticker


if __name__ == "__main__":
    fetch_market_news()
