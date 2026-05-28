# Hedge Fund Sim

A multi-agent AI system that simulates a hedge fund with three competing traders, real market data, and a full audit trail of every decision. Built to explore multi-agent orchestration, structured LLM output, and human-in-the-loop approval patterns.

**Live demo:** _coming soon_  
**Stack:** Python 3.11 · LangGraph · Anthropic Claude · FastAPI · Supabase · Railway

---

## What it does

Three AI traders — Alex, Jordan, and Casey — each manage a $33,333 portfolio with distinct investment personalities. Every market session, each trader runs an independent analysis pipeline: news sentiment is evaluated, technical indicators are computed, and a trade proposal is generated. Proposals above a risk threshold are flagged for human review before execution. Every decision, reasoning chain, and outcome is stored for audit and analysis.

The system is designed to be observable — not just a black box that buys and sells, but a pipeline where every step can be inspected, replayed, and explained.

---

## The traders

| Trader | Style | Risk tolerance | VP threshold |
|---|---|---|---|
| **Alex** | Aggressive momentum — acts quickly on price signals, comfortable with concentration | High | 50% of capital |
| **Jordan** | Conservative macro — prioritizes capital preservation, requires strong conviction | Low | 30% of capital |
| **Casey** | Contrarian — fades crowded trades, looks for overcrowded positions to short | Medium | 50% of capital |

Each trader runs the same analysis pipeline with different personality context injected into the Portfolio Manager's prompt. The same market data produces genuinely different trade proposals because the reasoning context differs.

---

## System architecture

```
Market Data (yfinance)  ──┐
                           ├──▶  Supabase Postgres  ──▶  LangGraph Pipeline
News Data (NewsAPI)     ──┘                                      │
                                                                  ▼
                                                    ┌─────────────────────────┐
                                                    │      fetch_data         │
                                                    │  reads prices, news,    │
                                                    │  positions from DB      │
                                                    └────────────┬────────────┘
                                                                 │
                                              ┌──────────────────┴──────────────────┐
                                              ▼                                     ▼
                                   ┌──────────────────┐                  ┌──────────────────┐
                                   │   news_analyst   │                  │  tech_analyst    │
                                   │ sentiment + JSON │                  │ RSI, MACD, trend │
                                   └────────┬─────────┘                  └────────┬─────────┘
                                            │                                     │
                                            └──────────────┬──────────────────────┘
                                                           ▼
                                                ┌──────────────────┐
                                                │ portfolio_manager│
                                                │ trade proposal   │
                                                │ + retry loop     │
                                                └────────┬─────────┘
                                                         │
                                                         ▼
                                                ┌──────────────────┐
                                                │    vp_check      │
                                                │ notional vs cap  │
                                                └────────┬─────────┘
                                                         │
                                          ┌──────────────┴──────────────┐
                                          ▼                             ▼
                                 ┌────────────────┐          ┌──────────────────┐
                                 │ execute_trade  │          │  human_review    │
                                 │ writes trades  │          │ pending_decisions│
                                 │ + positions    │          │ awaits approval  │
                                 └────────────────┘          └──────────────────┘
```

Each trader runs this pipeline independently. All nodes share a typed state object (`HedgeFundState`) — no agent queries the database directly except `fetch_data`.

---

## Agent pipeline

### How agents work

Each "agent" is a direct call to the Anthropic Claude API (claude-haiku-4-5) with a carefully constructed system prompt. There is no agent magic — an agent is a function that takes structured input, calls Claude, validates the output, and returns structured data. The orchestration between agents is handled by LangGraph, not by the agents themselves.

This is an important distinction: the LLM handles *reasoning*, while the application code handles *routing, validation, and side effects*.

### Node 1 — `fetch_data`
Reads from Supabase: the watchlist, 48 price rows per ticker, 20 recent news items, current portfolio positions (scoped by `trader_id`), and current prices. Populates the shared state object. This is the only node that reads raw market data — all downstream nodes receive data through state.

### Node 2 — `news_analyst`
Receives `state["news_items"]`. Calls Claude with a system prompt instructing it to return a JSON object with two keys: `summary` (a concise narrative of the news landscape) and `sentiment` (one of: `bullish`, `bearish`, `neutral`). The node validates both keys are present before writing to state. On failure, appends to `state["errors"]` and sets `news_summary` to an empty dict so downstream nodes can handle it gracefully.

Output shape:
```json
{
  "summary": "TSLA reported stronger than expected delivery numbers...",
  "sentiment": "bullish"
}
```

### Node 3 — `technical_analyst`
Receives `state["prices"]`. Uses pandas to compute RSI(14), MACD, and trend direction for each ticker. RSI returns `null` when fewer than 14 price rows exist — the system is honest about insufficient data rather than extrapolating. Returns a structured dict of signals per ticker.

Output shape:
```json
{
  "TSLA": {
    "rsi": 62.4,
    "macd": 3.21,
    "trend": "uptrend",
    "signals": "Momentum positive, RSI approaching overbought"
  }
}
```

### Node 4 — `portfolio_manager`
Receives `state["news_summary"]`, `state["tech_signals"]`, `state["positions"]`, and `state["current_prices"]`. The system prompt includes the trader's personality and risk tolerance, injected from `TraderConfig`. Claude is instructed to return only a JSON object with no preamble or markdown.

A retry loop handles output validation: if JSON parsing fails, markdown fences are stripped and parsing is attempted once more. If both attempts fail, the error is logged and the proposal is set to an empty dict. This replaces an earlier formatter fallback that used string scraping — validation logic now lives in application code, not in the LLM's output.

Output shape:
```json
{
  "ticker": "TSLA",
  "action": "BUY",
  "shares": 10,
  "reasoning": "Positive sentiment combined with upward momentum and RSI below overbought...",
  "confidence": 0.74
}
```

### Node 5 — `vp_check`
Computes the notional value of the proposed trade (`shares × current_price`) and compares it against `trader.vp_threshold × trader_capital`. Sets `state["vp_verdict"]` to either `"execute_trade"` or `"human_review"`. If `trade_proposal` is empty (upstream failure), defaults to `"human_review"`.

### Node 6a — `execute_trade`
Writes to the `trades` table and upserts `portfolio_positions` with the new share count and updated average cost. All writes are scoped by `trader_id`.

### Node 6b — `human_review`
Writes to `pending_decisions` with `status = "pending"`. A human operator reviews via the FastAPI backend and approves or rejects. Approval triggers the same writes as `execute_trade`. This node fires for trades above the VP threshold and for any run where upstream nodes encountered errors.

---

## Structured output and reliability

Getting LLMs to return consistent JSON is a non-trivial engineering problem. LLMs are text generators — they can always prepend "Sure! Here's the JSON:" or wrap output in markdown code fences, breaking downstream parsers.

This system handles it in three layers:

1. **Strict system prompts** — every agent prompt ends with an explicit instruction: "Return only a JSON object. No preamble. No markdown fences. No explanation after the JSON."
2. **Retry loop** — if JSON parsing fails, strip fences and retry once. Max 2 attempts.
3. **Graceful degradation** — if both attempts fail, log to `state["errors"]` and set the output field to an empty dict. Downstream nodes check for empty dict and route to human review rather than crashing.

---

## Why LangGraph

The system was originally built with CrewAI, a higher-level agent framework. CrewAI was migrated away from for three reasons:

**Control flow.** CrewAI's process is sequential or hierarchical — opinionated about how agents connect. The VP circuit-breaker requires a conditional routing decision in the middle of the pipeline (execute vs. human review based on trade size). LangGraph expresses this as a first-class concept: a function that returns the name of the next node based on current state.

**Typed state.** In CrewAI, agents communicated via free text strings passed through a crew context. In LangGraph, a single typed `HedgeFundState` dict flows through every node. Each node reads only the fields it needs and writes back only the fields it updates. LangGraph merges return values into the running state — output validation lives in application code.

**Model swappability.** Each LangGraph node is a plain Python function. Swapping Claude for a local open-source model on a specific node is a one-line change. This matters for the long-term goal of fine-tuning a model on the system's own decision data.

---

## Data layer

### Supabase Postgres

All persistent state lives in Supabase. Tables:

| Table | Purpose |
|---|---|
| `watchlist` | Tickers the system monitors |
| `prices` | OHLCV price history (48 rows per ticker, updated on each ingestion run) |
| `news_items` | Raw news articles with headline, source, and published timestamp |
| `agent_decisions` | Full audit trail — every LLM decision with reasoning, confidence, and trader_id |
| `portfolio_positions` | Current holdings per trader. Unique constraint on `(trader_id, ticker)` |
| `trades` | Executed trades with price, shares, timestamp, and trader_id |
| `pending_decisions` | Trades awaiting human approval |
| `fund_balance` | Current cash balance per trader |

### Data ingestion

- **`ingestion/fetch_prices.py`** — pulls OHLCV data via yfinance for all tickers in the watchlist, upserts to `prices`
- **`ingestion/fetch_news.py`** — pulls recent articles via NewsAPI for each ticker, inserts new items to `news_items`

Ingestion runs are independent of the agent pipeline and are designed to be scheduled separately.

---

## API

FastAPI backend at `api/main.py`. Key endpoints:

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/pending` | List trades awaiting human approval |
| `POST` | `/approve/{decision_id}` | Approve a pending trade — executes immediately |
| `POST` | `/reject/{decision_id}` | Reject a pending trade |
| `GET` | `/positions` | Current portfolio positions per trader |
| `GET` | `/decisions` | Recent agent decisions with reasoning |

Human approval is intentional — trades above the VP threshold require a human to review the agent's reasoning before execution. This is a design choice, not a limitation.

---

## External services

| Service | Purpose | Notes |
|---|---|---|
| **Anthropic API** | Powers all three analyst agents and the portfolio manager | Using `claude-haiku-4-5-20251001` — fast and cost-effective for structured output tasks |
| **yfinance** | Historical and current price data | Free, no API key required |
| **NewsAPI** | News articles by ticker | Free tier: 100 requests/day |
| **Supabase** | Postgres database + REST API | Free tier sufficient for development |
| **Railway** | Deployment target for FastAPI backend | Environment variables managed via Railway dashboard |

---

## Project structure

```
hedge-fund-sim/
├── agents/
│   ├── graph.py              # LangGraph graph — nodes, edges, state schema
│   ├── news_analyst.py       # News sentiment agent (plain function, no framework)
│   ├── technical_analyst.py  # Technical indicator computation + LLM summary
│   ├── portfolio_manager.py  # Trade proposal agent with retry loop
│   ├── trader_config.py      # TraderConfig dataclass + Alex, Jordan, Casey instances
│   └── _archive/             # CrewAI originals (crew.py, trade_executor.py)
├── ingestion/
│   ├── fetch_prices.py       # yfinance → Supabase prices
│   └── fetch_news.py         # NewsAPI → Supabase news_items
├── api/
│   └── main.py               # FastAPI backend
├── utils/
│   └── db.py                 # Supabase client
├── DECISIONS.md              # Architectural decisions log
└── README.md                 # This file
```

---

## Running locally

```bash
# 1. Clone and install dependencies
git clone https://github.com/dylan/hedge-fund-sim
cd hedge-fund-sim
pip install -r requirements.txt

# 2. Set environment variables
cp .env.example .env
# Fill in: ANTHROPIC_API_KEY, SUPABASE_URL, SUPABASE_KEY, NEWS_API_KEY

# 3. Required before running any script
export PYTHONPATH=/Users/dylan/hedge-fund-sim

# 4. Run data ingestion
python ingestion/fetch_prices.py
python ingestion/fetch_news.py

# 5. Run the full multi-trader analysis
python agents/graph.py

# 6. Start the API
uvicorn api.main:app --reload
```

---

## Design decisions worth noting

**Human-in-the-loop is intentional.** The VP threshold isn't just a safety mechanism — it's an architectural statement about where human judgment belongs in an automated system. Large position changes get reviewed. Small ones execute automatically. This mirrors how real trading desks operate.

**Audit trail by default.** Every LLM decision is written to `agent_decisions` with the full reasoning chain, confidence score, and trader identity. The system is designed to be explainable after the fact, not just functional in the moment.

**Graceful degradation over crashes.** Every node appends to `state["errors"]` rather than raising exceptions. A failed news fetch doesn't kill a technical analysis that succeeded. A bad LLM output routes to human review rather than executing garbage. The graph completes every run.

**Trader personalities are prompt engineering.** Alex, Jordan, and Casey are not separate codebases — they're one parameterized graph with different system prompt context. Adding a fourth trader is adding a `TraderConfig` instance and a `fund_balance` row. This is intentional: the interesting behavior emerges from prompt design, not from architectural complexity.

---

## Planned

- **Scheduler** — APScheduler running the pipeline 2–3× daily with a DB-based pause switch
- **Discovery Agent** — scans news for new tickers, validates and adds to watchlist automatically  
- **Leaderboard + dashboard** — public-facing UI showing trader P&L, decisions feed, and "meet the team" page with each trader's personality and track record
- **RAG layer** — store agent reasoning as embeddings so agents can reference past decisions when making new ones
- **Pattern recognition** — Random Forest on price + sentiment features, backtesting with Sharpe ratio
- **Open-source model experimentation** — fine-tune a smaller model (Mistral 7B / Llama 3) on the system's own `agent_decisions` data, swap it into one trader's node and compare performance against Claude-powered traders
