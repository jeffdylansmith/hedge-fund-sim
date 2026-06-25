# Meridian Capital

A simulated hedge fund where three AI traders with distinct personalities analyze live market data and execute paper trades autonomously throughout the trading day. It demonstrates multi-agent LLM orchestration, LangGraph conditional routing, RAG memory, and options pricing — all wired together as a production system. Live at [hedge-fund-sim.vercel.app](https://hedge-fund-sim.vercel.app).

## Live Links

- **Frontend:** https://hedge-fund-sim.vercel.app
- **API Docs:** https://hedge-fund-sim-production.up.railway.app/docs

## Stack

| Backend | Frontend / Infra |
|---|---|
| Python 3.11 | React + Vite + Recharts |
| FastAPI | Tailwind CSS |
| LangGraph | Vercel |
| Anthropic Claude Haiku | Railway |
| Supabase Postgres + pgvector | |
| APScheduler | |
| Alpaca paper trading | |
| OpenAI text-embedding-3-small | |
| yfinance + NewsAPI | |

## Quick Start

**Prerequisites:** Python 3.11, Node 18+

```bash
git clone https://github.com/jeffdylansmith/hedge-fund-sim
cd hedge-fund-sim
```

**Environment variables** (create `.env` in project root):

```
ANTHROPIC_API_KEY
SUPABASE_URL
SUPABASE_KEY
NEWS_API_KEY
ALPACA_API_KEY
ALPACA_SECRET_KEY
OPENAI_API_KEY
RESEND_API_KEY
ALERT_EMAIL_ADDRESS
```

**Backend:**

```bash
pip install -r requirements.txt
export PYTHONPATH=$(pwd)
uvicorn api.main:app --reload
```

**Frontend:**

```bash
cd frontend
npm install
npm run dev
```

---

Architecture, design decisions, and mathematical foundations: see ARCHITECTURE.md
