# Hedge Fund Sim — Decisions & Concepts Log

> Running log of architectural decisions, why we made them, and what the concepts actually mean in plain language. Update this as the project evolves.

---

## How to use this file

Each entry has three parts: **what we did**, **why we did it** (the trade-off), and **how to explain it** (plain language for interviews). Add a new entry every time you make a non-obvious technical choice.

---

## Architecture decisions

---

### 1. Started with CrewAI, migrated to LangGraph
**Date:** Project start → Migration

**What:** Initially built the agent pipeline using CrewAI, then rewrote it in LangGraph.

**Why CrewAI first:** Good ergonomics for getting something running fast. You define agents with roles and backstories, wire them into a crew, and it handles the orchestration. Low boilerplate, good for prototyping.

**Why we migrated:** Three reasons hit us at the same time:
1. The Portfolio Manager was ignoring JSON formatting instructions — agents in CrewAI communicate via free text strings, so output validation lives inside the LLM's response rather than in your code. Fragile.
2. The VP circuit-breaker we were planning needed a real if/then routing decision in the middle of the pipeline. CrewAI's sequential/hierarchical process is opinionated and fights you on conditional routing.
3. Long-term goal of swapping in open-source models on specific nodes — easier to do when each node is a plain function with typed inputs/outputs.

**The trade-off:** LangGraph is more code. You define every node and every edge explicitly. CrewAI would have been fine if the system stayed simple. We outgrew it fast.

**How to explain it in an interview:**
> "I started with CrewAI because it let me get something working quickly, but I ran into a reliability problem — the agents were passing free text between each other and the one making trade decisions kept formatting its output inconsistently. When I started building the circuit-breaker logic that needed to route differently based on trade size, I realized CrewAI's sequential process couldn't express that cleanly. I migrated to LangGraph, which is more like building a normal data pipeline — you define a shared data object, a series of functions, and explicit routing between them. The AI calls are just one step in a function, not the thing orchestrating everything."

---

### 2. LangGraph state as a typed DTO
**Date:** Migration

**What:** Defined `HedgeFundState` as a `TypedDict` — a Python typed dictionary that flows through every node in the graph.

**Why:** Every node reads from state and writes back to state. No agent queries the database directly (except `fetch_data`). No agent receives unstructured input. If a node expects `state["news_summary"]` to be a dict with keys `summary` and `sentiment`, and it isn't, the error is caught immediately in that node.

**The .NET analogy:** It's a strongly-typed DTO (Data Transfer Object) passed through a service pipeline. Every method in the pipeline knows exactly what it's receiving. Same concept, different syntax.

**Fields:**
```
ticker: str                  # which stock this run is analyzing
trader_id: str               # "alex" | "jordan" | "casey"
watchlist: list[dict]        # from Supabase watchlist table
prices: list[dict]           # from Supabase prices table (48 rows per ticker)
news_items: list[dict]       # from Supabase news_items table (20 rows)
positions: list[dict]        # current portfolio positions, filtered by trader_id
current_prices: dict         # {ticker: price} for PM context
news_summary: dict           # {summary: str, sentiment: bullish|bearish|neutral}
tech_signals: dict           # {rsi: float|null, macd: float, trend: str, signals: str}
trade_proposal: dict         # {ticker, action, shares, reasoning, confidence}
vp_verdict: str              # "execute_trade" | "human_review"
errors: list[str]            # any node can append here — graph continues on error
```

**How to explain it:**
> "LangGraph passes a single typed state object through every node. It's like a DTO in a service pipeline — every function knows exactly what it's receiving and what it's expected to return. This is what fixed the structured output problem: instead of asking the LLM to please format its response as JSON, my code receives whatever the LLM returns, parses and validates it, and decides what to do if it fails. The validation logic lives in my code, not inside the model's output."

---

### 3. Supabase writes belong in node wrappers, not agent functions
**Date:** Migration

**What:** The agent functions (news_analyst, technical_analyst, portfolio_manager) are pure functions — they take data in, call Claude, return structured output. All database writes happen in the node wrapper inside `graph.py`.

**Why:** Separation of concerns. An agent function that both calls an LLM and writes to a database is doing two things, which makes it hard to test and hard to reason about. If a Supabase write fails, you don't want it mixed up with an LLM failure. The node wrapper handles the side effect (DB write) after the pure function succeeds.

**The pattern:**
```
graph.py node wrapper:
  1. pull relevant slice from state
  2. call agent function (pure, no DB)
  3. validate output
  4. write to agent_decisions in Supabase
  5. return state update
```

**How to explain it:**
> "I separated the LLM calls from the database writes. Each agent function is pure — it takes structured input and returns structured output. The node wrapper in the graph handles the side effects. This made each piece independently testable and kept the failure modes clean."

---

### 4. JSON output retry loop instead of formatter fallback
**Date:** Migration

**What:** Replaced the `force_json_from_reasoning()` formatter fallback in `trade_executor.py` with a retry loop inside the Portfolio Manager node.

**Why the old way was fragile:** The formatter tried to scrape JSON out of whatever the model returned using string matching. One unexpected output format and it either silently produced garbage or crashed.

**How the retry loop works:**
1. Call Claude with a strict system prompt: "Return only a JSON object. No preamble. No markdown."
2. Try to parse the response as JSON.
3. If parsing fails, strip markdown fences (```json ... ```) and try again.
4. If it fails a second time, append to `state["errors"]` and set `trade_proposal` to empty dict.
5. Downstream nodes check for empty dict and handle gracefully.

**Why this is better:** The validation logic is explicit and in your code. Max 2 attempts. Failure is visible in the errors field, not silent.

**How to explain it:**
> "LLMs are text generators — sometimes they add 'Sure! Here's the JSON:' before the actual JSON, which breaks your parser. The old approach tried to scrape the JSON out of whatever came back, which was fragile. I replaced it with a retry loop: parse the response, if it fails strip markdown fences and try once more, if it fails again log the error and move on. The system degrades gracefully instead of crashing."

---

### 5. VP circuit-breaker as a conditional edge
**Date:** Migration / multi-trader refactor

**What:** After the Portfolio Manager proposes a trade, a `vp_check_node` computes the notional value and compares it against a threshold (50% of trader capital by default, 30% for Jordan). A `route_after_vp` function returns either `"execute_trade"` or `"human_review"` — this is a LangGraph conditional edge.

**Why not in CrewAI:** CrewAI's sequential process would have required a hacky workaround to branch after a specific agent. In LangGraph, conditional routing is a first-class concept — you register a function that returns the name of the next node, and the graph follows it.

**The .NET analogy:** It's a strategy pattern or a middleware short-circuit — the request gets inspected and routed before it reaches the final handler.

**How to explain it:**
> "After the portfolio manager proposes a trade, a VP check node looks at the notional value relative to the trader's capital. If it's above the threshold, it routes to a human review queue instead of executing. This is expressed as a conditional edge in LangGraph — a function that returns which node to visit next based on the current state. It's the equivalent of a middleware short-circuit in a web API pipeline."

---

### 6. Multi-trader structure via TraderConfig
**Date:** Multi-trader refactor

**What:** Alex, Jordan, and Casey are not three separate codebases. They're one parameterized graph instantiated with different `TraderConfig` objects.

**What varies per trader:**
- `personality` string injected into the PM system prompt → changes how Claude reasons
- `risk_tolerance` → PM is aware of it when sizing positions
- `vp_threshold` → Jordan's is tighter (0.3) than Alex and Casey (0.5)
- `trader_id` → scopes all Supabase reads and writes

**What stays identical:** Graph topology, node logic, Anthropic API calls.

**Why this matters architecturally:** Adding a fourth trader is adding one `TraderConfig` instance and one `fund_balance` row. Nothing else changes.

**How to explain it:**
> "The three traders share the same graph. What differs is a config object injected at runtime — a few sentences of personality description that go into the Portfolio Manager's system prompt, a risk tolerance parameter, and a VP threshold. The same LLM running the same graph produces genuinely different trade decisions because the prompt context is different. Adding a new trader is adding a config object."

---

## Concepts glossary

### Agent
A named LLM call with a specific role and context. In this system: News Analyst, Technical Analyst, Portfolio Manager. Not magic — just a function that calls Claude with a carefully written system prompt.

### Node (LangGraph)
A Python function that accepts the full state dict, does some work, and returns a partial dict of keys to update. LangGraph merges the return value into the running state — only the keys you return get updated, everything else stays as-is.

### Edge (LangGraph)
A connection between two nodes that tells the graph which node to visit next. A **conditional edge** is one where a function decides the destination based on current state, rather than always going to the same place.

### State (LangGraph)
The shared typed dictionary that flows through every node. Think of it as the single source of truth for a graph run — every node reads from it and writes back to it.

### Structured output
Getting an LLM to return data in a specific format (usually JSON) rather than free text. The challenge is that LLMs are text generators — they can always add unexpected text around the format you want. The solution is validation and retry logic in your code, not relying on the model to be perfectly consistent.

### Conditional routing
A branching decision in the graph based on current state. In this system: after the VP check, route to execute or human review based on trade size. Equivalent to an if/then in a normal pipeline.

### System prompt
The instructions you give Claude before the user message. In this system, the system prompt is where trader personality, output format requirements, and context about available data live. It's the most important lever for controlling model behavior.

### RSI (Relative Strength Index)
A momentum indicator that measures whether a stock is overbought or oversold. Ranges 0–100. Above 70 = potentially overbought (price may pull back). Below 30 = potentially oversold (price may recover). Requires 14 data points to compute — with fewer rows, returns null rather than an unreliable value.

### MACD (Moving Average Convergence Divergence)
A trend-following indicator that shows the relationship between two moving averages of price. When MACD crosses above its signal line, it's considered a bullish signal. When it crosses below, bearish. Good for identifying momentum shifts.

### Sharpe Ratio
Risk-adjusted return. Measures how much return you're getting per unit of risk (volatility). A Sharpe above 1.0 is generally considered good. Lets you compare traders fairly — a trader with 20% returns and low volatility beats one with 25% returns and wild swings.

### Lookahead bias
A backtesting mistake where your model accidentally uses future data when making historical predictions — for example, using the closing price of the day you're predicting to compute an indicator. Makes backtest results look much better than reality.

### Survivorship bias
A backtesting mistake where you only test on stocks that still exist today, ignoring companies that went bankrupt or were delisted. Inflates apparent returns because you've removed all the losers from the dataset.

---

## Schema reference

### Supabase tables
| Table | Purpose | Key columns |
|---|---|---|
| `watchlist` | Tickers the system tracks | `ticker`, `name` |
| `prices` | Historical OHLCV data | `ticker`, `close`, `timestamp` |
| `news_items` | Raw news articles | `ticker`, `headline`, `sentiment`, `published_at` |
| `agent_decisions` | Audit trail of every LLM decision | `agent_name`, `ticker`, `action`, `reasoning`, `confidence`, `trader_id` |
| `portfolio_positions` | Current holdings per trader | `ticker`, `shares`, `avg_cost`, `trader_id` |
| `trades` | Executed trades | `ticker`, `action`, `shares`, `price`, `executed_at`, `trader_id` |
| `pending_decisions` | Trades awaiting human approval | `ticker`, `action`, `shares`, `price`, `status`, `trader_id` |
| `fund_balance` | Capital per trader | `trader_id`, `cash`, `last_updated` |

### Known issues / tech debt
- `datetime.now()` was used in places that should be `datetime.now(timezone.utc).isoformat()` — fixed in graph.py, watch for recurrence
- `fund_balance` currently has no `trader_id` column — using single row for now, `trader_id` field in state kept for forward compatibility
- `pending_decisions` rows 1–5 have null shares from before JSON formatter — delete them when convenient

---

## Framework comparison

| | CrewAI | LangGraph | Raw Anthropic API |
|---|---|---|---|
| Best for | Rapid prototyping | Production pipelines with conditional routing | Simple single-agent calls |
| Control flow | Sequential or hierarchical (opinionated) | Explicit nodes and edges (you define everything) | N/A — just a function call |
| Structured output | Agent can ignore instructions | Node validates return value | You validate return value |
| Model swapping | Harder — abstracted away | Easy — each node is a plain function | Trivial |
| Industry use | Prototypes, demos | Production systems | Always used alongside a framework |
| When we use it | Archived | All agent orchestration | VP check, formatter retry |
