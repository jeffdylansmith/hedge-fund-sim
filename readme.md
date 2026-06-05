# Meridian Capital — AI Hedge Fund Simulation

A multi-agent AI system that runs a simulated hedge fund with three competing traders, each making independent buy/sell decisions on a live watchlist using real market data. The interesting part isn't the trading — it's the architecture: a typed LangGraph state machine orchestrates seven specialized agent nodes, each backed by Claude Haiku, with conditional routing at the risk management layer, Alpaca paper trading for execution, and a Supabase persistence layer with atomic RPCs for position management. The traders aren't clones of each other: Alex is a momentum chaser who hates sitting in cash, Jordan is a capital-preservation macro investor who mostly HOLDs, and Casey is a contrarian who fades consensus moves. They react to each other's trades in character, generate EOD journal entries, and adapt their behavior over time through a RAG memory layer that injects recent P&L history into the decision context.

## Live Demo

| | |
|---|---|
| **Frontend** | https://hedge-fund-sim.vercel.app |
| **API** | https://hedge-fund-sim-production.up.railway.app |
| **API Docs** | https://hedge-fund-sim-production.up.railway.app/docs |

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                     Data Ingestion                       │
│  yfinance (OHLCV + fundamentals) · NewsAPI · Alpaca      │
│  Discovery: yfinance screener → watchlist (9am daily)    │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│               LangGraph State Machine                    │
│                                                         │
│  fetch_data ──► fundamental_analyst                     │
│                      │                                  │
│                      ▼                                  │
│               news_analyst                              │
│                      │                                  │
│                      ▼                                  │
│            technical_analyst                            │
│                      │                                  │
│                      ▼                                  │
│           portfolio_manager ◄── RAG memory              │
│                      │                                  │
│                      ▼                                  │
│              risk_manager                               │
│                   /  |  \                               │
│                  /   |   \                              │
│           veto  /  caution \ proceed                    │
│               /       |     \                           │
│        skip_trade  caution_  execute_trade              │
│                    execute                              │
│                       │                                 │
│                       ▼                                 │
│              Alpaca paper execution                     │
│              Supabase persistence                       │
│              Cross-trader reactions                     │
└─────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│                  React Frontend                          │
│  Live feed · Leaderboard · Position detail · Chat UI    │
└─────────────────────────────────────────────────────────┘
```

The graph runs once per hour per trader during market hours. Each run is a full traversal: fresh data fetch, seven agent nodes, conditional execution routing, Alpaca order submission, and DB writes — all within a single typed state object that flows through the graph.

---

## The Three Traders

| Trader | Strategy | Risk Tolerance | VP Threshold | Defining Behavior |
|--------|----------|----------------|--------------|-------------------|
| **Alex** | Aggressive momentum | High — accepts large drawdowns for outsized returns | 0.5 | RSI > 75 + bullish MACD = BUY. Missing a move is worse than being wrong. Hates sitting in cash. |
| **Jordan** | Conservative macro | Low — capital preservation above all | 0.3 | Requires news sentiment, technicals, and fundamentals to align before deploying capital. Default answer is HOLD. |
| **Casey** | Contrarian | Medium — buys fear, sells greed, sizes carefully | 0.5 | Treats strong bullish consensus as a warning sign. Fades momentum extremes. Looks for price/sentiment divergences. |

Each trader runs through the same LangGraph pipeline but with different system prompt personas injected at every node — the news analyst, technical analyst, and portfolio manager all receive trader-specific framing that shapes how they interpret signals.

---

## Agent Pipeline

### fetch_data
Pulls the full state needed for a trading decision in one pass: OHLCV price history (48 hourly candles per ticker) from Supabase, current positions and cash balance for the active trader, the last 20 news headlines, pre-scored fundamental data from the `fundamental_scores` table, and the trader's RAG memory string. All downstream nodes read from this state — no agent makes its own DB calls.

### Fundamental Analyst
Runs as a daily batch job at 9:30am ET rather than inside the per-trader graph. For each watchlist ticker, it fetches `yf.Ticker(ticker).info`, extracts ten fundamental fields (PE, PB, D/E, revenue growth, earnings growth, FCF, profit margin, market cap, sector, forward PE), and computes a 0–10 score in Python using explicit threshold rules — no LLM involved in the scoring. Claude Haiku is then called once with all tickers' pre-computed data to assign verdicts (`strong_buy` through `avoid`) and one-sentence theses. Results are upserted to `fundamental_scores` and injected into the graph state as context the portfolio manager can see.

### News Analyst
Receives the last 20 headlines from NewsAPI (pre-loaded into Supabase by the ingestion layer) and returns a structured JSON object with a market summary and a `bullish|bearish|neutral` sentiment tag. The prompt persona varies per trader — Jordan's news analyst applies a skeptical macro lens; Alex's flags momentum catalysts and ignores litigation noise; Casey's surfaces what the consensus might be getting wrong.

### Technical Analyst
Computes RSI(14), MACD(12/26/9), SMA(10), and SMA(20) in Python using pandas directly on the price rows pulled from Supabase — no external TA library. The pre-computed indicator values are then sent to Claude Haiku for synthesis into a structured JSON object with per-ticker signals and a human-readable summary. This two-step approach (Python math → LLM interpretation) keeps the numbers accurate while letting the model do the natural-language reasoning.

### Portfolio Manager
The decision-making core. Receives the full context — news summary, technical signals, fundamental scores (injected into the news summary as a context block by `fundamental_analyst_node`), current positions, cash balance, and the trader's RAG memory string — and returns a JSON array with one `BUY|SELL|HOLD` decision per watchlist ticker, including share count, one-sentence reasoning, and a confidence float. Position sizing is pre-calculated in Python (`floor(cash * 0.15 / price)`) and included in the system prompt to prevent the LLM from hallucinating nonsensical quantities. Supports three inference backends via `LLM_PROVIDER` env var: Claude Haiku (production), Groq-hosted Llama 3.1 8B (experimentation), and local ollama (Mac Mini or any host reachable via Tailscale).

### Risk Manager
Evaluates the portfolio manager's proposal before any capital is deployed. Pre-computes concentration percentage in Python, then passes the full proposal and portfolio state to Claude Haiku for risk assessment. Returns a score (1–10), specific flags (overbought conditions, insufficient cash reserve, sentiment contradiction), and a three-way routing recommendation. The graph uses conditional edges to route to one of three nodes: `execute_trade` (proceed as proposed), `caution_execute` (cut position sizes in half before executing), or `skip_trade` (veto and log the reasoning). The risk manager is the only place in the pipeline where the graph can branch — it acts as a circuit breaker rather than a rubber stamp.

### Cross-Trader Reactions
After any executed trade, the other two traders are shown what happened and generate in-character one-to-two sentence reactions using their own personality prompts. These are logged to `agent_decisions` with `action = "trader_reaction"` and surface in the live feed — giving the simulation a floor-banter quality that makes the activity log readable.

---

## Key Architectural Decisions

**1. LangGraph over CrewAI**

LangGraph's explicit typed state and conditional edges were the deciding factor. `HedgeFundState` is a `TypedDict` — every node declares what it reads and what it writes, which makes the data flow auditable and prevents the kind of silent context mutation that plagues agent frameworks built on shared mutable dicts. The conditional routing after `risk_manager_node` — three distinct execution paths with different economic outcomes — would require significant workaround in frameworks that only support linear or hierarchical agent chains. LangGraph treats routing as a first-class concern.

**2. Structured output validation**

No agent's output is trusted as-is. Every LLM response goes through a two-attempt parse: raw JSON first, then markdown-fence-stripped retry. If both fail, the node returns a safe fallback and appends to `state["errors"]` — the graph continues rather than crashing. Beyond parsing, the portfolio manager node validates each trade object against a required key set and silently drops any phantom SELLs for tickers the trader doesn't actually hold. The LLM's job is reasoning; the code's job is enforcement.

**3. Risk manager as circuit breaker**

The three-path routing — proceed, caution, veto — changes the economic outcome meaningfully. Caution cuts position sizes by 50% before execution. Veto logs the rationale but executes nothing. This isn't cosmetic: a risk manager that only ever says "proceed" is a liability, not a safety layer. The trader-specific `vp_threshold` (0.3 for Jordan, 0.5 for Alex and Casey) controls how the risk manager calibrates its recommendations for each persona.

**4. Supabase RPC for atomic position management**

Position updates — buying, selling, adjusting share counts, modifying cash balances — use Supabase RPC functions rather than REST API calls. A BUY that decrements cash and increments a position row needs to succeed or fail together; two separate REST calls with a crash in between leaves the fund in an inconsistent state. The reconciliation job (every 30 minutes, 24/7) compares DB positions against Alpaca's truth and corrects drift using the same RPC layer.

**5. RAG memory layer**

Before each market session, `get_trader_memory()` queries the last 30 days of closed trades for the active trader, pairs each SELL with its prior BUY to compute realized P&L, and computes win rate, best/worst tickers, and behavioral patterns (BUY/SELL/HOLD distribution over 14 days). This is formatted into a structured text block and injected into the portfolio manager's system prompt. No vector database, no embeddings — the context window is small enough that a well-formatted string does the job. The practical effect: a trader who has been losing money on tech stocks will see that in their memory and have it available when deciding whether to add to a tech position.

**6. LLM provider abstraction**

A single `LLM_PROVIDER` env var routes the portfolio manager's inference to Claude Haiku (production default), Groq-hosted Llama 3.1 8B (free tier, useful for cost comparison), or a local ollama instance reachable via `OLLAMA_HOST` — which can be a Mac Mini on the same Tailscale network. The abstraction lives in `_call_llm()` and the rest of the pipeline is unaware of which backend is active. This setup also makes the fine-tuning story concrete: export training pairs from `/training/export`, fine-tune with `scripts/finetune.py`, serve the adapter via ollama, point `OLLAMA_HOST` at it.

---

## Scheduling & Automation

All jobs run via APScheduler with `BackgroundScheduler(timezone=ET)`. Market-session jobs gate on `is_market_hours()` which checks weekday, time window (8:30am–4pm ET), and a hardcoded NYSE holiday calendar. A `scheduler_config` table in Supabase provides a runtime pause/unpause toggle without a redeploy.

| Time | Job |
|------|-----|
| 9:00am ET, Mon–Fri | Ticker discovery — yfinance screener (`day_gainers`, `most_actives`) → watchlist |
| 9:30am ET, Mon–Fri | Fundamental analysis — yfinance `.info` fetch + scoring for all watchlist tickers |
| Every 60min, 8:30am–4pm ET | Market session — full LangGraph run for all three traders |
| 3:45pm ET, Mon–Fri | EOD flatten — all positions closed at current prices |
| 4:05pm ET, Mon–Fri | EOD summaries — Claude Haiku generates in-character trading journal entries |
| Every 30min, 24/7 | Reconciliation — Alpaca positions vs DB, corrects drift via RPC |

---

## Data Sources

| Source | Used For |
|--------|----------|
| **yfinance** | OHLCV price history, fundamental data (PE, revenue growth, FCF, etc.), ticker discovery via screener |
| **NewsAPI** | Market news headlines, ingested to Supabase and served to the news analyst |
| **Alpaca** | Paper trading order execution, position truth source for reconciliation |
| **Supabase** | Primary persistence: prices, positions, trades, decisions, fund balances, news, fundamental scores |

---

## ML/AI Engineering Features

**Multi-agent orchestration** — Seven specialized nodes in a directed graph with typed shared state and conditional branching. Each node has a single responsibility; the graph enforces ordering and data flow.

**Three LLM inference backends** — Claude Haiku for production quality, Groq for free-tier Llama 3.1 8B as a cost comparison baseline, and ollama for fully local inference. One env var switches between them with no code changes.

**Training data export** — `GET /training/export` queries all historical `portfolio_review` decisions, parses the JSON reasoning arrays, pairs BUY decisions with subsequent SELL trades to determine outcome (`profitable | unprofitable | open`), and streams the results as JSONL instruction-tuning pairs. Accepts `?outcome=profitable` to export only winning decisions for positive-example fine-tuning.

**LoRA fine-tuning script** — `scripts/finetune.py` loads the exported JSONL, filters by trader if specified, applies the model's chat template, configures LoRA (`r=16, alpha=32, target_modules=["q_proj","v_proj"]`), and trains with `fp16` on an A10G or equivalent. Estimated cost: ~$3–5 on RunPod spot for a few hundred examples. A `--dry-run` flag validates data and prints statistics without requiring a GPU.

**RAG memory** — `agents/memory.py` computes a performance summary from raw trade history (no embeddings, no vector store) and injects it into the portfolio manager's system prompt. Traders with low win rates get a caution nudge; traders on a hot streak are told to stay disciplined. The lessons are rule-based and generated in Python — the LLM receives the output, not the raw data.

**Outcome-supervised training pairs** — Each exported example includes the trader's decision, the reasoning, and the eventual trade outcome. This creates a dataset where the signal isn't just "what did the model say" but "did it work" — enabling outcome-weighted fine-tuning where profitable decisions get more training weight.

---

## Stack

| Layer | Technology |
|-------|-----------|
| **Language** | Python 3.12 (API + agents), JavaScript/React (frontend) |
| **Agent orchestration** | LangGraph |
| **LLM** | Anthropic Claude Haiku (production) · Groq Llama 3.1 8B · ollama |
| **API** | FastAPI + uvicorn |
| **Database** | Supabase (PostgreSQL + RPC functions) |
| **Frontend** | React + Vite, React Router, Recharts |
| **Scheduling** | APScheduler |
| **Paper trading** | Alpaca Markets API |
| **Hosting** | Railway (API) · Vercel (frontend) |
| **Market data** | yfinance · NewsAPI |

---

## Running Locally

**Prerequisites:** Python 3.12+, Node 18+, a Supabase project, Anthropic API key.

```bash
# Clone and set up Python env
git clone https://github.com/your-username/hedge-fund-sim
cd hedge-fund-sim
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Required env vars — copy to .env
ANTHROPIC_API_KEY=...
SUPABASE_URL=...
SUPABASE_KEY=...

# Optional
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
NEWS_API_KEY=...
LLM_PROVIDER=claude          # or groq or ollama
GROQ_API_KEY=...
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3.1:8b

# Start the API (PYTHONPATH needed for agent imports)
PYTHONPATH=. uvicorn api.main:app --reload --port 8000

# Start the frontend
cd frontend
npm install
echo "VITE_API_URL=http://localhost:8000" > .env.local
npm run dev
```

To trigger a single trading session manually without waiting for the scheduler:

```bash
PYTHONPATH=. python agents/graph.py
```

---

## What's Next

- **Test suite** — pytest coverage for agent nodes with mocked Supabase and LLM responses; property-based tests for the position sizing and risk concentration math
- **Portfolio value history** — time-series table tracking total fund value per trader per day, enabling proper drawdown charts rather than synthetic sparklines
- **Reconciliation UI** — surface the reconciliation log in the frontend so drift corrections are visible without querying the API directly
- **Fine-tuned model evaluation** — A/B test the LoRA-adapted Llama 3.1 8B against Claude Haiku on decision quality and P&L outcome, using the existing training export pipeline as the evaluation dataset
