# Meridian Capital — Architecture & Design Decisions

A technical reference for the system architecture, agent design, mathematical foundations,
and engineering decisions behind Meridian Capital. Written for engineers evaluating the project.

---

## What This System Does

Meridian Capital is a multi-agent AI hedge fund simulation that runs three distinct
trader personas (Alex, Jordan, Casey) across a watched universe of tickers. Three times
daily, each trader runs a full analysis pipeline — news sentiment, technical signals,
fundamental scoring, portfolio management, and risk assessment — and executes paper trades
via Alpaca's paper trading API.

On top of the equity pipeline, an options analysis system evaluates each ticker for
options opportunities using a four-agent subgraph: volatility assessment, directional
thesis generation, strategy selection, and a mathematical risk gate. Closed positions
feed a RAG (Retrieval-Augmented Generation) pipeline that gives future agents memory
of past decisions and their outcomes.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Frontend (Vercel)                        │
│              React + Vite + Recharts + Tailwind                  │
│     Home · Portfolio · Traders · Chat · Options Dashboard        │
└────────────────────────┬────────────────────────────────────────┘
                         │ REST
┌────────────────────────▼────────────────────────────────────────┐
│                      FastAPI (Railway)                           │
│         /run · /portfolio · /chat · /options/*                   │
│                    APScheduler (3x daily)                        │
└──────┬──────────────────────────────────────────┬───────────────┘
       │                                          │
┌──────▼──────────┐                    ┌──────────▼──────────────┐
│  Equity Pipeline │                    │   Options Pipeline       │
│   (LangGraph)    │                    │    (LangGraph subgraph)  │
│                  │                    │                          │
│ fetch_data       │                    │ volatility_node          │
│ fundamental      │                    │ thesis_node              │
│ news_analyst     │                    │ strategy_node            │
│ technical        │                    │ risk_gate_node           │
│ portfolio_mgr    │                    └──────────┬───────────────┘
│ risk_manager     │                               │
│ vp_check         │                    ┌──────────▼───────────────┐
│ execute_trade    │                    │   options_executor.py     │
└──────┬───────────┘                    │   position_monitor.py     │
       │                                │   episode_synthesizer.py  │
┌──────▼─────────────────────────────────────────────────────────┐
│                    External Services                             │
│  Alpaca Paper API · yfinance · NewsAPI · Anthropic · OpenAI     │
└──────┬─────────────────────────────────────────────────────────┘
       │
┌──────▼─────────────────────────────────────────────────────────┐
│                   Supabase (Postgres + pgvector)                 │
│  prices · news · agent_decisions · trades · fund_balance        │
│  portfolio_positions · options_positions · rag_documents         │
└────────────────────────────────────────────────────────────────┘
```

---

## Stack Rationale

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.11 | Ecosystem dominance in AI/ML |
| Agent orchestration | LangGraph | Stateful graphs, conditional routing, production-grade |
| LLM (production) | Anthropic Haiku | Fast, cheap, sufficient for structured outputs |
| LLM (experimentation) | Groq / Llama 3.1 8B | Zero cost, local fallback |
| Embeddings | OpenAI text-embedding-3-small | Best cost/quality ratio; Anthropic has no embedding API |
| API | FastAPI | Async, automatic docs, Pydantic validation |
| Database | Supabase Postgres | Managed, pgvector support, no separate vector DB needed |
| Scheduling | APScheduler | Embedded in FastAPI process, sufficient at this scale |
| Paper trading | Alpaca alpaca-py | Full options API, realistic paper environment |
| Frontend | React + Vite + Recharts | Fast iteration, composable charts |
| Hosting | Vercel (frontend) + Railway (backend) | Zero-config deploys from main |

**Key decision — pgvector over a dedicated vector database:**
At this scale, adding Pinecone or Weaviate would introduce a new managed service,
new credentials, new failure modes, and additional cost — for no meaningful performance
benefit over pgvector with an ivfflat index. Keeping vectors in Supabase means one
connection pool, one backup strategy, and SQL joins between vector search results
and relational data. The upgrade path to a dedicated vector store exists if the
corpus grows beyond ~1M documents.

---

## Agent Design Philosophy

### Separation of Concerns
Each agent answers exactly one question. This is not cosmetic — it has direct
engineering consequences:

- **Testability:** Agents with narrow responsibilities have clear input/output contracts.
  Every agent in this system has a corresponding test file that mocks all external I/O
  and tests the agent's logic independently.
- **Debuggability:** When a trade decision is wrong, you know which agent made which
  sub-decision. The full decision chain is logged to Supabase as `agent_decisions` rows.
- **Cost efficiency:** Smaller context windows per agent = fewer tokens per run.
  The options pipeline uses four focused prompts rather than one enormous prompt
  trying to do everything simultaneously.

### Deterministic vs LLM-Driven Logic

The most important architectural decision in the options pipeline:
**risk management logic contains zero LLM calls.**

```python
# This function has no LLM calls — it is pure logic
def _select_strategy(direction, conviction, iv_environment, recommendation):
    if recommendation == "avoid_earnings":
        return "skip"
    if direction == "neutral" and iv_environment == "low":
        return "skip"
    if direction == "bullish" and iv_environment == "high":
        return "bull_put_spread"
    # ...
```

An LLM that could be prompted into approving a position that violates risk thresholds
is not a risk manager. Hard limits — Greeks thresholds, position sizing caps,
options exposure limits — are enforced in Python, not in a prompt.
LLMs are used for judgment (thesis direction, rationale narrative) and never
for decisions that must be auditable and consistent.

### Graceful Degradation
Every agent node catches exceptions and returns a safe default rather than raising.
A yfinance timeout in the volatility node doesn't crash the trading graph — it
sets `should_skip=True` and the pipeline short-circuits to REJECT at the risk gate.

```python
def volatility_node(state):
    try:
        result = assess_volatility(ticker=state["ticker"])
        return {"volatility": result, "should_skip": False}
    except Exception as e:
        return {
            "volatility": None,
            "should_skip": True,
            "errors": state.get("errors", []) + [f"volatility_node: {str(e)}"],
        }
```

This pattern — fail locally, degrade gracefully, never propagate — is applied
consistently across all four options agents and the main equity pipeline.

---

## Options Pipeline — Mathematical Foundations

### Black-Scholes Pricing Model

Options are priced using the Black-Scholes model, implemented from scratch in
`utils/options_math.py`. Five inputs produce a fair value:

```
S     = current stock price
K     = strike price
T     = time to expiration in years
r     = risk-free interest rate
σ     = implied volatility (annualized)
```

The model assumes stock returns are log-normally distributed. The price formula
for a call option is:

```
Call = S·N(d₁) − K·e^(−rT)·N(d₂)

where:
  d₁ = [ln(S/K) + (r + σ²/2)·T] / (σ·√T)
  d₂ = d₁ − σ·√T
  N() = cumulative normal distribution function
```

N(d₂) approximates the probability the option expires in-the-money.
The formula prices the option as the probability-weighted expected payoff,
discounted to present value.

**Put-call parity** is verified in the test suite as a mathematical identity:
```
Call − Put = S − K·e^(−rT)
```
If this relationship breaks, the pricing model is wrong. It's one of the most
important tests in the codebase — a no-arbitrage constraint that must hold.

### The Greeks

Each Greek is a partial derivative of the Black-Scholes price with respect to
one input variable — the rate of change of option value as one input changes,
holding all others constant.

| Greek | Formula | Meaning | Sign Convention |
|---|---|---|---|
| Delta (Δ) | ∂Price/∂S | P&L per $1 stock move | Call: [0,1], Put: [-1,0] |
| Gamma (Γ) | ∂Δ/∂S | Rate of change of delta | Always positive for long options |
| Theta (Θ) | ∂Price/∂t ÷ 365 | Daily time decay | Negative for long options |
| Vega (ν) | ∂Price/∂σ ÷ 100 | P&L per 1% IV move | Positive for long options |

**Gamma is the second derivative** of option price with respect to stock price.
If delta is velocity, gamma is acceleration. High gamma near expiration is why
short-dated options are dangerous — a small stock move produces a large delta change,
rapidly altering the position's risk profile.

**Theta/gamma tradeoff** is the central tension in options trading. Long options
have positive gamma (accelerating gains, decelerating losses) but negative theta
(paying time decay daily). Short options are the inverse. You cannot have both
working in your favor simultaneously.

### Implied Volatility — Numerical Inversion

Black-Scholes has no closed-form solution for σ given a market price.
Implied volatility is computed via Newton-Raphson iteration:

```python
sigma = 0.30  # initial guess
for _ in range(max_iterations):
    price = black_scholes_price(S, K, T, r, sigma, option_type)
    v = vega(S, K, T, r, sigma) * 100  # gradient
    sigma_new = sigma - (price - market_price) / v
    if abs(sigma_new - sigma) < precision:
        return sigma_new
    sigma = sigma_new
```

Vega is the gradient — it measures how much the price changes per unit of sigma,
allowing the algorithm to adjust sigma proportionally to close the error.
Converges in approximately 10 iterations for typical inputs.

### Expected Move

The one-standard-deviation expected price range over N days:

```
Expected Move = S × σ × √(days/365)
```

Derived from the normal distribution: a one-standard-deviation move encompasses
68% of outcomes. Used for strike selection — iron condor short strikes are placed
outside the expected move, selling the 68th-to-100th percentile of probability.

### Strategy Selection Matrix

Strategy selection is deterministic, not LLM-driven:

| Direction | IV Environment | Strategy | Rationale |
|---|---|---|---|
| Bullish | Low | Long Call | Cheap to buy, directional bet |
| Bullish | Moderate | Long Call | Moderate cost, clear direction |
| Bullish | High | Bull Put Spread | Sell expensive puts, defined risk |
| Bearish | Low | Long Put | Cheap to buy, directional bet |
| Bearish | Moderate | Long Put | Moderate cost, clear direction |
| Bearish | High | Bear Call Spread | Sell expensive calls, defined risk |
| Neutral | High/Moderate | Iron Condor | Collect premium, range-bound profit |
| Neutral | Low | Skip | No edge — nothing cheap to buy, nothing rich to sell |
| Any | Avoid Earnings | Skip | IV crush and gap risk override all |

**Why high IV changes bullish strategy from call to spread:**
When IV is elevated, calls are expensive (high vega cost). Selling puts below
current price collects inflated premium while maintaining a bullish-to-neutral
stance. Same directional view, better risk/reward given the volatility environment.

### Position Sizing

```
available_risk  = portfolio_value × options_risk_cap_pct − existing_exposure
position_budget = available_risk × max(0.30, conviction)
contracts       = floor(position_budget / max_loss_per_contract)
contracts       = clamp(contracts, 0, 10)
```

Max loss per contract varies by strategy:
- **Long option:** premium × 100 (one contract = 100 shares, always)
- **Spread:** (wing_width − net_credit) × 100
- **Iron condor:** (narrower_wing − total_credit) × 100

Options risk caps per trader: Alex 15%, Jordan 10%, Casey 20% of allocated capital.
Measured by max loss, not notional value — a defined-risk spread's max loss is
always known at entry.

---

## RAG Pipeline

### Architecture

```
News/Market Data (ingest)
    → embed at ingest time (OpenAI text-embedding-3-small)
    → store in rag_documents (pgvector)

Closed Options Position
    → episode_synthesizer.py builds narrative document
    → embed narrative
    → store with metadata (strategy, P&L, Greeks, close reason)

Agent runs (inference time)
    → retrieve_and_format(query, ticker, top_k)
    → pgvector cosine similarity search
    → top-K episodes injected into agent prompt
    → LLM reasons over current data + historical context
```

### Why Narrative, Not Structured Data

Episodes are stored as natural language narratives, not JSON field dumps.
Embedding models convert text to vectors based on semantic meaning.
"entry_delta: 0.45, theta: -0.04" embeds differently — and retrieves poorly —
compared to "moderate bullish directional exposure with manageable daily time decay."

The narrative form retrieves better because it uses the same semantic space
as agent queries. When an agent asks about "bearish high-IV environments on AAPL,"
it finds episodes written in similar language about similar situations.

The test `test_no_raw_json_field_names` enforces this — if field names appear in
the narrative, the test fails. The constraint is architectural, not cosmetic.

### Cosine Similarity Search

```sql
SELECT content, metadata,
       1 - (embedding <=> query_embedding::vector) AS similarity
FROM rag_documents
WHERE source_type = 'options_episode'
  AND embedding IS NOT NULL
ORDER BY embedding <=> query_embedding::vector
LIMIT 10;
```

The `<=>` operator is pgvector's cosine distance. Values range from 0 (identical)
to 2 (opposite). Ordering ascending returns most similar first.
A similarity threshold of 0.70 filters out low-relevance results.

### Graceful Degradation
The retrieval layer returns an empty list on any failure — embedding failure,
database error, or empty corpus. Agents handle empty retrieval by omitting
the context block entirely, running identically to their pre-RAG behavior.
The RAG layer never blocks an agent from making a decision.

---

## Data Model

### Core Tables

**`fund_balance`** — Per-trader cash balance. Source of truth for available capital.
Updated after each trade execution and reconciled against Alpaca daily.

**`portfolio_positions`** — Open equity positions. Ticker, shares, average cost,
current value. Separate from options positions by design — different fields,
different lifecycle, different P&L mechanics.

**`options_positions`** — Open and closed options positions. Includes full leg
structure as JSONB, Greeks at entry, lifecycle timestamps, and realized P&L.
Status field: `pending_submission → open → closed/failed/expired`.

**`rag_documents`** — Vector document store. Content (narrative text),
embedding (vector(1536)), metadata (JSONB), source tracking, event date.
The ivfflat index enables approximate nearest neighbor search at scale.

**`agent_decisions`** — Full audit log of every agent run. Ticker, trader,
decision type, reasoning, confidence. The complete decision chain is queryable
for any trade.

### Write-Ahead Pattern

Options positions are written to Supabase before submission to Alpaca:

```
1. Write row: status = 'pending_submission'
2. Submit to Alpaca
3. Success → status = 'open', store order_id
4. Failure → status = 'failed', log error
```

If the process crashes between steps 2 and 3, the `pending_submission` row
survives and can be reconciled against Alpaca's order API on restart.
No position is silently lost.

### Idempotency Guards

All state transitions use conditional updates:

```sql
UPDATE options_positions
SET status = 'closed', realized_pnl = ?, closed_at = now()
WHERE id = ? AND status = 'open'
```

The `AND status = 'open'` guard ensures running the close operation twice
has the same effect as running it once. Safe to retry on any failure.

---

## Testing Strategy

235 tests across 10 test files. Zero failures at any commit.

### Layered Approach

**Pure function tests** — Black-Scholes math, Greeks calculations, decision matrix.
Deterministic inputs, exact expected outputs. These tests verify mathematical
correctness, not just that functions run.

```python
def test_put_call_parity(self):
    call = black_scholes_price(S, K, T, r, sigma, "call")
    put = black_scholes_price(S, K, T, r, sigma, "put")
    parity = S - K * math.exp(-r * T)
    assert abs((call - put) - parity) < 0.01
```

**Contract tests** — Agent output shape and value constraints. Assert that
`risk_score` is between 1-10, that `recommendation` is in the valid set,
that all required keys are present. These tests lock the interface, not
the exact values.

**Mocked I/O tests** — All external calls (LLM, yfinance, Alpaca, Supabase)
are mocked in tests. Agent logic is tested independently of external services.
No test makes a network call or requires credentials.

**Decision matrix tests** — Every path through `_select_strategy()` is tested
with exact assertions. When the matrix changes, tests break intentionally —
forcing explicit acknowledgment that the behavior changed.

### The Patching Problem

`utils/db.py` calls `supabase.create_client()` at module import time.
Any file importing `db.py` attempts a live database connection immediately.
The conftest patches this before any test module is imported:

```python
_supabase_patcher = mock.patch(
    "supabase.create_client", return_value=MagicMock()
)
_supabase_patcher.start()
```

This pattern — patch at import time, not at test time — is required for
any module that performs side effects on import. The same applies to the
Alpaca `TradingClient` and OpenAI client, both wrapped in try/except at
module level so `None` in test environments never causes import failures.

---

## Scheduling and Operational Flow

APScheduler runs embedded in the FastAPI process on Railway.
Three runs per day during market hours. Each run:

1. Discovers active tickers (from `discover_tickers.py`)
2. Ingests fresh prices (yfinance → Supabase)
3. Ingests fresh news (NewsAPI → Supabase)
4. For each trader × ticker: runs the full equity pipeline
5. For each approved signal: optionally runs the options pipeline
6. Position monitor runs after market close: checks expiry, stop loss, profit target
7. Closed positions trigger the episode synthesizer → RAG corpus grows

---

## LLM Cost Management

Every LLM call in the system is intentionally minimal:

- **Haiku over Sonnet/Opus:** Options rationale and thesis generation use Haiku.
  Structured JSON outputs at 200 tokens max. Haiku is sufficient for this format.
- **No LLM for math:** Greeks, position sizing, strategy selection, risk gate —
  all pure Python. The most frequent operations in the system have zero LLM cost.
- **Embeddings billed separately:** OpenAI text-embedding-3-small at $0.02/M tokens.
  An episode narrative is ~300 tokens. 1,000 episodes costs approximately $0.006.
- **max_tokens discipline:** Every LLM call specifies a tight `max_tokens` limit
  proportional to the expected output. Rationale sentences: 80 tokens. Thesis: 200.
  No open-ended generation that could run long and inflate cost.

---

## What This Would Look Like at Scale

The current architecture is intentionally simple — three traders, a handful of tickers,
APScheduler for orchestration. Each component is designed so the upgrade path is clear:

| Current | At Scale |
|---|---|
| APScheduler (poll-based) | Alpaca webhooks (event-driven) |
| Supabase Postgres | Postgres + TimescaleDB for tick data |
| pgvector (single table) | Databricks Vector Search + Delta Lake |
| Python scripts | Apache Spark / Databricks Jobs |
| Single-process FastAPI | Kubernetes + horizontal scaling |
| OpenAI embeddings (online) | Batch embedding API (50% cost reduction) |
| Poll-based position monitor | Event-driven expiration handler |
| Manual regime tagging | Feature Store (Databricks) with automated regime classification |

The business logic — agent reasoning, strategy selection, risk management,
episode synthesis — is identical at any scale. The infrastructure layer changes;
the domain layer doesn't. This separation is intentional and is the correct
way to design for scale from the start.

---

## Project Structure

```
hedge-fund-sim/
├── agents/
│   ├── graph.py                  # Main LangGraph graph, HedgeFundState
│   ├── news_analyst.py           # News sentiment agent
│   ├── technical_analyst.py      # RSI, MACD, SMA + LLM summary
│   ├── portfolio_manager.py      # Trade decisions, per-trader batching
│   ├── risk_manager.py           # Risk scoring, veto/caution/proceed
│   ├── fundamental_analyst.py    # yfinance fundamentals + LLM verdicts
│   ├── memory.py                 # RAG memory — trader P&L history
│   ├── trader_config.py          # TraderConfig dataclass, ALEX/JORDAN/CASEY
│   └── options/
│       ├── volatility_agent.py   # IV rank, percentile, environment
│       ├── thesis_agent.py       # Directional signal from news + technicals
│       ├── strategy_agent.py     # Deterministic selection matrix + Greeks
│       ├── greeks_risk_agent.py  # Mathematical risk gate, position sizing
│       ├── options_graph.py      # LangGraph subgraph, conditional routing
│       ├── options_executor.py   # OCC symbols, write-ahead, Alpaca submission
│       ├── position_monitor.py   # Daily lifecycle: expiry, stop, target
│       └── episode_synthesizer.py # Closed position → narrative → embedding
├── ingestion/
│   ├── fetch_prices.py           # yfinance → Supabase
│   ├── fetch_news.py             # NewsAPI → Supabase
│   └── discover_tickers.py       # Dynamic ticker list management
├── utils/
│   ├── db.py                     # Supabase client singleton
│   ├── options_math.py           # Black-Scholes, Greeks, IV, expected move
│   └── retrieval.py              # pgvector cosine search, episode formatting
├── api/
│   ├── main.py                   # FastAPI app, APScheduler, all routes
│   └── options_routes.py         # /options/* endpoints
├── frontend/
│   └── src/
│       ├── pages/
│       │   ├── Home.jsx          # Portfolio overview, fund P&L
│       │   ├── Options.jsx       # Options dashboard, payoff diagrams
│       │   └── ...
│       └── components/
├── tests/
│   ├── conftest.py               # Fixtures: mock_supabase, mock_llm
│   ├── unit/                     # Pure function tests
│   └── agents/options/           # Agent contract and integration tests
└── scripts/
    ├── migrate_options_positions.sql
    ├── migrate_rag_documents.sql
    └── reset_fund_balance.py
```

---

## Key Design Decisions — Summary

**Deterministic risk management over LLM-driven risk management.**
The risk gate uses pure Python math. An LLM cannot override position limits.

**Write-ahead pattern for all order submissions.**
Database always reflects intent, even when external calls fail.

**Narrative episodes over structured data in the RAG corpus.**
Embedding quality depends on semantic density. Natural language retrieves better
than field names and numbers.

**pgvector over a dedicated vector database.**
No additional service, no additional failure mode. Upgrade path exists when needed.

**Narrow agents over monolithic prompts.**
One question per agent. Testable, debuggable, cost-efficient.

**Graceful degradation everywhere.**
No single component failure cascades to a full system failure.
Every external call has a safe fallback path.

---

*Built as a learning project demonstrating multi-agent AI systems, quantitative
finance concepts, and production data engineering patterns.*
*Stack: Python · LangGraph · FastAPI · Supabase · React · Alpaca · Anthropic · OpenAI*
