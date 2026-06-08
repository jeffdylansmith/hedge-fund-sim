import yfinance as yf
from datetime import datetime, timezone, timedelta
from utils.db import supabase

CORE_TICKERS = {
    "AAPL", "MSFT", "GOOGL", "TSLA", "SPY",
    "NVDA", "JPM", "AMZN", "QQQ",
}


def run_discovery():
    try:
        now = datetime.now(timezone.utc)

        # 1. EXPIRE stale tickers
        stale = (
            supabase.table("discovered_opportunities")
            .select("id, ticker")
            .eq("status", "active")
            .lt("expires_at", now.isoformat())
            .execute()
        )
        expired_tickers = [r["ticker"] for r in stale.data]
        if stale.data:
            stale_ids = [r["id"] for r in stale.data]
            for oid in stale_ids:
                supabase.table("discovered_opportunities").update(
                    {"status": "expired"}
                ).eq("id", oid).execute()

        # 2. FETCH candidates
        raw_candidates = {}
        for screen_name in ("day_gainers", "most_actives"):
            try:
                result = yf.screen(screen_name)
                quotes = result.get("quotes", []) if result else []
                for q in quotes:
                    ticker = q.get("symbol", "").upper()
                    if ticker and ticker not in raw_candidates:
                        raw_candidates[ticker] = {"quote": q, "source": screen_name}
            except Exception as e:
                print(f"  discover: yf.screen({screen_name!r}) failed: {e}")

        # 3. FILTER by market cap >= $2B and avg volume >= 1,000,000
        filtered = {}
        for ticker, data in raw_candidates.items():
            q = data["quote"]
            market_cap = q.get("marketCap") or 0
            avg_volume = q.get("averageDailyVolume3Month") or q.get("averageDailyVolume10Day") or 0
            if market_cap >= 2_000_000_000 and avg_volume >= 1_000_000:
                filtered[ticker] = data

        # 4. DEDUPLICATE against watchlist and active discovered_opportunities
        watchlist_rows = supabase.table("watchlist").select("ticker").execute()
        active_rows = (
            supabase.table("discovered_opportunities")
            .select("ticker")
            .eq("status", "active")
            .execute()
        )
        existing = {r["ticker"] for r in watchlist_rows.data} | {r["ticker"] for r in active_rows.data}
        net_new = {t: d for t, d in filtered.items() if t not in existing}

        # 5. LIMIT to 5, ranked by % change descending
        def pct_change(item):
            return item[1]["quote"].get("regularMarketChangePercent", 0) or 0

        top5 = sorted(net_new.items(), key=pct_change, reverse=True)[:5]

        # 6. BOOTSTRAP prices for each net-new ticker
        for ticker, data in top5:
            try:
                df = yf.download(
                    ticker, period="30d", interval="1d",
                    progress=False, auto_adjust=True, multi_level_index=False,
                )
                if df.empty:
                    print(f"  discover: no price data for {ticker}")
                    continue
                records = []
                for ts, row in df.iterrows():
                    records.append({
                        "ticker": ticker,
                        "timestamp": ts.isoformat(),
                        "open":   float(row["Open"]),
                        "high":   float(row["High"]),
                        "low":    float(row["Low"]),
                        "close":  float(row["Close"]),
                        "volume": int(row["Volume"]),
                    })
                supabase.table("prices").upsert(records, on_conflict="ticker,timestamp").execute()
            except Exception as e:
                print(f"  discover: price bootstrap failed for {ticker}: {e}")

        # 6.5. CAP watchlist at 25 tickers — evict oldest with no open positions
        current_wl = supabase.table("watchlist").select("ticker, added_at").execute()
        current_count = len(current_wl.data)
        removed_count = 0

        if current_count + len(top5) > 25:
            open_pos = supabase.table("portfolio_positions").select("ticker").execute()
            held = {r["ticker"] for r in open_pos.data}
            evictable = sorted(
                [
                    r for r in current_wl.data
                    if r["ticker"] not in held and r["ticker"] not in CORE_TICKERS
                ],
                key=lambda r: r.get("added_at") or "",
            )
            slots_needed = current_count + len(top5) - 25
            for r in evictable[:slots_needed]:
                try:
                    supabase.table("watchlist").delete().eq("ticker", r["ticker"]).execute()
                    removed_count += 1
                except Exception as e:
                    print(f"  discover: watchlist evict failed for {r['ticker']}: {e}")

        # 7. ADD to watchlist and discovered_opportunities
        expires_at = (now + timedelta(days=14)).isoformat()
        for ticker, data in top5:
            q = data["quote"]
            name = q.get("shortName") or q.get("longName") or ticker
            source = data["source"]
            pct = q.get("regularMarketChangePercent", 0) or 0
            reason = f"{pct:+.1f}% move via {source}"

            try:
                supabase.table("watchlist").insert(
                    {"ticker": ticker, "company_name": name}
                ).execute()
            except Exception as e:
                print(f"  discover: watchlist insert failed for {ticker}: {e}")

            try:
                supabase.table("discovered_opportunities").insert({
                    "ticker":     ticker,
                    "name":       name,
                    "source":     source,
                    "reason":     reason,
                    "expires_at": expires_at,
                    "status":     "active",
                }).execute()
            except Exception as e:
                print(f"  discover: opportunities insert failed for {ticker}: {e}")

        # 8. LOG summary
        print(
            f"discovery: expired={len(expired_tickers)} "
            f"candidates={len(raw_candidates)} "
            f"passed_filter={len(filtered)} "
            f"net_new={len(top5)} "
            f"added={[t for t, _ in top5]}"
        )
        print(
            f"discovery: watchlist at {current_count + len(top5) - removed_count} tickers, "
            f"removed {removed_count}, added {len(top5)}"
        )

    except Exception as e:
        print(f"discover: run_discovery failed: {e}")


if __name__ == "__main__":
    run_discovery()
