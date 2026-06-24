"""
options_routes.py

FastAPI routes for options analysis and position tracking.
Mounted into main.py as a router with prefix /options.

Routes:
  POST /options/analyze          — run options pipeline for ticker + trader
  GET  /options/positions        — all open options positions
  GET  /options/positions/{trader_id}  — open positions for one trader
  GET  /options/exposure/{trader_id}   — current options risk exposure summary
"""

import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from agents.options.options_graph import run_options_pipeline
from agents.trader_config import TRADERS, TraderConfig
from utils.db import supabase

router = APIRouter(prefix="/options", tags=["options"])


def get_trader(trader_id: str) -> TraderConfig:
    match = next((t for t in TRADERS if t.trader_id == trader_id), None)
    if not match:
        raise HTTPException(status_code=404, detail=f"Trader '{trader_id}' not found")
    return match


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    ticker: str
    trader_id: str
    news_summary: Optional[dict] = None
    tech_signals: Optional[dict] = None


class AnalyzeResponse(BaseModel):
    ticker: str
    trader_id: str
    decision: str
    strategy: Optional[str]
    contracts: int
    max_loss_total: float
    volatility_summary: Optional[dict]
    thesis_summary: Optional[dict]
    explanation: str
    errors: list


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze_options(request: AnalyzeRequest):
    """
    Runs the full options pipeline for a ticker + trader.
    Returns the complete decision with supporting analysis.
    Does NOT execute any trade — analysis only.
    """
    trader = get_trader(request.trader_id)

    # Get portfolio value for this trader from Supabase
    try:
        balance = supabase.table("fund_balance") \
            .select("balance") \
            .eq("trader_id", request.trader_id) \
            .single() \
            .execute()
        portfolio_value = balance.data["balance"]
    except Exception:
        portfolio_value = 1_000_000.0  # fallback

    # Get existing options exposure
    try:
        positions = supabase.table("options_positions") \
            .select("max_loss_total") \
            .eq("trader_id", request.trader_id) \
            .eq("status", "open") \
            .execute()
        existing_exposure = sum(p["max_loss_total"] for p in positions.data)
    except Exception:
        existing_exposure = 0.0

    result = run_options_pipeline(
        ticker=request.ticker.upper(),
        trader_config=trader,
        portfolio_value=portfolio_value,
        news_summary=request.news_summary or {},
        tech_signals=request.tech_signals or {},
        existing_options_exposure=existing_exposure,
        persona=trader.news_analyst_persona,
    )

    risk = result.get("risk_assessment") or {}
    strategy = result.get("strategy") or {}
    volatility = result.get("volatility") or {}
    thesis = result.get("thesis") or {}

    return AnalyzeResponse(
        ticker=request.ticker.upper(),
        trader_id=request.trader_id,
        decision=risk.get("decision", "REJECT"),
        strategy=strategy.get("strategy"),
        contracts=risk.get("contracts", 0),
        max_loss_total=risk.get("max_loss_total", 0.0),
        volatility_summary={
            "iv_rank": volatility.get("iv_rank"),
            "iv_environment": volatility.get("iv_environment"),
            "expected_move_30d": volatility.get("expected_move_30d"),
            "recommendation": volatility.get("recommendation"),
        } if volatility else None,
        thesis_summary={
            "direction": thesis.get("direction"),
            "conviction": thesis.get("conviction"),
            "catalyst": thesis.get("catalyst"),
        } if thesis else None,
        explanation=risk.get("explanation", "No explanation available."),
        errors=result.get("errors", []),
    )


@router.get("/positions")
async def get_all_options_positions():
    """Returns all open options positions across all traders."""
    try:
        result = supabase.table("options_positions") \
            .select("*") \
            .eq("status", "open") \
            .order("opened_at", desc=True) \
            .execute()
        return {"positions": result.data, "count": len(result.data)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/positions/{trader_id}")
async def get_trader_options_positions(trader_id: str):
    """Returns open options positions for a specific trader."""
    get_trader(trader_id)
    try:
        result = supabase.table("options_positions") \
            .select("*") \
            .eq("trader_id", trader_id) \
            .eq("status", "open") \
            .order("opened_at", desc=True) \
            .execute()
        return {"trader_id": trader_id, "positions": result.data, "count": len(result.data)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/exposure/{trader_id}")
async def get_options_exposure(trader_id: str):
    """
    Returns current options risk exposure summary for a trader.
    Shows how much of their options_risk_cap_pct is utilized.
    """
    trader = get_trader(trader_id)

    try:
        balance = supabase.table("fund_balance") \
            .select("balance") \
            .eq("trader_id", trader_id) \
            .single() \
            .execute()
        portfolio_value = balance.data["balance"]
    except Exception:
        portfolio_value = 1_000_000.0

    try:
        positions = supabase.table("options_positions") \
            .select("max_loss_total, strategy, ticker") \
            .eq("trader_id", trader_id) \
            .eq("status", "open") \
            .execute()

        total_exposure = sum(p["max_loss_total"] for p in positions.data)
        cap = portfolio_value * trader.options_risk_cap_pct
        utilization = total_exposure / cap if cap > 0 else 0.0

        return {
            "trader_id": trader_id,
            "portfolio_value": portfolio_value,
            "options_risk_cap_pct": trader.options_risk_cap_pct,
            "options_risk_cap_dollars": round(cap, 2),
            "current_exposure": round(total_exposure, 2),
            "utilization_pct": round(utilization * 100, 1),
            "remaining_capacity": round(cap - total_exposure, 2),
            "open_positions": len(positions.data),
            "positions_by_strategy": {
                p["strategy"]: p["ticker"] for p in positions.data
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
