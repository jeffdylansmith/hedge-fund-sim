from datetime import datetime, timezone, timedelta


def get_trader_memory(trader_id: str, db_client) -> str:
    try:
        now = datetime.now(timezone.utc)
        cutoff_30d = (now - timedelta(days=30)).isoformat()
        cutoff_14d = (now - timedelta(days=14)).isoformat()

        # ── Fetch recent trades ──────────────────────────────────────────────
        trades_rows = (
            db_client.table("trades")
            .select("ticker, action, price, shares, executed_at")
            .eq("trader_id", trader_id)
            .gte("executed_at", cutoff_30d)
            .order("executed_at")
            .execute()
        )
        trades = trades_rows.data or []

        if not trades:
            return ""

        # Group by ticker: collect buys and sells in order
        buys_by_ticker: dict = {}
        sells_by_ticker: dict = {}
        for t in trades:
            ticker = t["ticker"]
            if t["action"] == "buy":
                buys_by_ticker.setdefault(ticker, []).append(t)
            elif t["action"] == "sell":
                sells_by_ticker.setdefault(ticker, []).append(t)

        # Match each sell to the most recent prior buy for P&L
        pnl_by_ticker: dict = {}
        wins = 0
        losses = 0

        for ticker, sells in sells_by_ticker.items():
            ticker_buys = buys_by_ticker.get(ticker, [])
            for sell in sells:
                # Find the most recent buy before this sell
                prior_buy = None
                for buy in reversed(ticker_buys):
                    if buy["executed_at"] <= sell["executed_at"]:
                        prior_buy = buy
                        break
                if prior_buy is None:
                    continue

                shares = float(sell["shares"])
                buy_price = float(prior_buy["price"])
                sell_price = float(sell["price"])
                pnl_dollars = (sell_price - buy_price) * shares
                pnl_pct = (sell_price - buy_price) / buy_price * 100 if buy_price else 0.0

                pnl_by_ticker.setdefault(ticker, 0.0)
                pnl_by_ticker[ticker] += pnl_dollars

                if pnl_dollars > 0:
                    wins += 1
                else:
                    losses += 1

        total_closed = wins + losses
        win_rate = (wins / total_closed * 100) if total_closed > 0 else 0.0

        sorted_pnl = sorted(pnl_by_ticker.items(), key=lambda x: x[1], reverse=True)
        best_tickers = [f"{t}(${p:+,.0f})" for t, p in sorted_pnl[:3] if p > 0]
        worst_tickers = [f"{t}(${p:+,.0f})" for t, p in sorted_pnl[-3:] if p < 0]

        # ── Behavioral patterns (14-day decisions) ───────────────────────────
        decisions_rows = (
            db_client.table("agent_decisions")
            .select("action, ticker, reasoning")
            .eq("trader_id", trader_id)
            .gte("created_at", cutoff_14d)
            .in_("action", ["BUY", "SELL", "HOLD"])
            .execute()
        )
        decisions = decisions_rows.data or []

        buy_count = sum(1 for d in decisions if d["action"] == "BUY")
        sell_count = sum(1 for d in decisions if d["action"] == "SELL")
        hold_count = sum(1 for d in decisions if d["action"] == "HOLD")
        total_decisions = buy_count + sell_count + hold_count

        ticker_activity: dict = {}
        for d in decisions:
            if d.get("ticker"):
                ticker_activity[d["ticker"]] = ticker_activity.get(d["ticker"], 0) + 1
        most_active = sorted(ticker_activity.items(), key=lambda x: x[1], reverse=True)[:3]
        most_active_str = ", ".join(f"{t}({c})" for t, c in most_active) or "none"

        # ── Format output ────────────────────────────────────────────────────
        lines = ["PERFORMANCE MEMORY (last 30 days):"]

        if total_closed > 0:
            lines.append(f"Win rate: {win_rate:.0f}% ({wins} wins, {losses} losses)")
        else:
            lines.append("Win rate: no closed trades yet")

        lines.append(f"Best performing tickers: {', '.join(best_tickers) or 'none'}")
        lines.append(f"Worst performing tickers: {', '.join(worst_tickers) or 'none'}")

        lines.append("\nBEHAVIORAL PATTERNS:")
        if total_decisions > 0:
            lines.append(
                f"Decision distribution: {buy_count/total_decisions*100:.0f}% BUY, "
                f"{sell_count/total_decisions*100:.0f}% SELL, "
                f"{hold_count/total_decisions*100:.0f}% HOLD"
            )
        else:
            lines.append("Decision distribution: no recent decisions")
        lines.append(f"Most active tickers: {most_active_str}")

        lines.append("\nRECENT LESSONS:")
        lessons_written = 0

        if total_closed > 0 and win_rate < 40:
            lines.append("Your recent trades have underperformed — consider being more selective.")
            lessons_written += 1

        if total_decisions > 0 and hold_count / total_decisions > 0.70:
            lines.append("You've been overly cautious — market may be offering opportunities you're missing.")
            lessons_written += 1

        if total_closed > 0 and win_rate > 70:
            lines.append("Your recent strategy is working — stay disciplined.")
            lessons_written += 1

        # Flag tickers that appear in both best and worst (traded multiple rounds)
        best_set = {t for t, _ in sorted_pnl[:3]}
        worst_set = {t for t, _ in sorted_pnl[-3:] if pnl_by_ticker.get(t, 0) < 0}
        for ticker in best_set & worst_set:
            lines.append(f"{ticker} has been volatile for you — trade carefully.")
            lessons_written += 1

        if lessons_written == 0:
            lines.append("Not enough history for pattern-based lessons yet.")

        return "\n".join(lines)

    except Exception:
        return ""
