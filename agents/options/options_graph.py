"""
options_graph.py

LangGraph subgraph for the options analysis pipeline.
Runs four agents in sequence for a single ticker + trader combination:

  volatility_node → thesis_node → strategy_node → risk_gate_node

State flows forward — each node reads prior node outputs from state
and adds its own. Final state contains the complete options decision.

This subgraph is called from the main graph after risk_manager_node
when equity signal strength warrants options analysis.
It does NOT replace equity execution — it runs alongside it.
"""

import os
from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END

from agents.options.volatility_agent import assess_volatility
from agents.options.thesis_agent import generate_thesis
from agents.options.strategy_agent import select_strategy
from agents.options.greeks_risk_agent import assess_options_risk
from agents.trader_config import TraderConfig


# ---------------------------------------------------------------------------
# State definition
# ---------------------------------------------------------------------------

class OptionsState(TypedDict):
    # Inputs (set before graph runs)
    ticker: str
    trader_config: TraderConfig
    portfolio_value: float
    existing_options_exposure: float
    news_summary: dict
    tech_signals: dict
    persona: str

    # Outputs (populated by each node)
    volatility: Optional[dict]
    thesis: Optional[dict]
    strategy: Optional[dict]
    risk_assessment: Optional[dict]

    # Control
    errors: list[str]
    should_skip: bool


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def volatility_node(state: OptionsState) -> dict:
    """
    Assesses IV environment for the ticker.
    Sets should_skip=True if avoid_earnings — no point running
    downstream agents if we know we won't trade.
    """
    try:
        result = assess_volatility(
            ticker=state["ticker"],
            persona=state.get("persona", ""),
        )
        skip = result.get("recommendation") == "avoid_earnings"
        return {"volatility": result, "should_skip": skip}
    except Exception as e:
        return {
            "volatility": None,
            "should_skip": True,
            "errors": state.get("errors", []) + [f"volatility_node: {str(e)}"],
        }


def thesis_node(state: OptionsState) -> dict:
    """
    Generates directional thesis from news + technical signals.
    Skipped if should_skip is True from volatility_node.
    """
    if state.get("should_skip"):
        return {"thesis": None}

    try:
        result = generate_thesis(
            ticker=state["ticker"],
            news_summary=state.get("news_summary", {}),
            tech_signals=state.get("tech_signals", {}),
            trader_config=state["trader_config"],
            persona=state.get("persona", ""),
        )
        return {"thesis": result}
    except Exception as e:
        return {
            "thesis": None,
            "should_skip": True,
            "errors": state.get("errors", []) + [f"thesis_node: {str(e)}"],
        }


def strategy_node(state: OptionsState) -> dict:
    """
    Selects options strategy from thesis + volatility assessment.
    """
    if state.get("should_skip") or not state.get("thesis") or not state.get("volatility"):
        return {"strategy": None}

    try:
        # Get current price from volatility assessment context
        # Fall back to a reasonable default — strategy agent handles it
        import yfinance as yf
        try:
            price = yf.Ticker(state["ticker"]).fast_info.last_price
        except Exception:
            price = 100.0

        result = select_strategy(
            ticker=state["ticker"],
            thesis=state["thesis"],
            volatility=state["volatility"],
            current_price=price,
            trader_config=state["trader_config"],
            persona=state.get("persona", ""),
        )
        # If strategy is skip, mark should_skip for risk gate
        if result.get("strategy") == "skip":
            return {"strategy": result, "should_skip": True}
        return {"strategy": result}
    except Exception as e:
        return {
            "strategy": None,
            "should_skip": True,
            "errors": state.get("errors", []) + [f"strategy_node: {str(e)}"],
        }


def risk_gate_node(state: OptionsState) -> dict:
    """
    Final gate. Approves or rejects the position.
    If approved, result contains contracts and max loss —
    enough for the execution layer to submit the order.
    """
    if state.get("should_skip") or not state.get("strategy"):
        return {
            "risk_assessment": {
                "decision": "REJECT",
                "contracts": 0,
                "explanation": "Pipeline skipped upstream.",
            }
        }

    strategy_result = state["strategy"]

    try:
        result = assess_options_risk(
            ticker=state["ticker"],
            strategy=strategy_result.get("strategy", "skip"),
            strikes=strategy_result.get("strikes", {}),
            greeks=strategy_result.get("greeks", {}),
            thesis=state["thesis"],
            trader_config=state["trader_config"],
            portfolio_value=state["portfolio_value"],
            existing_options_exposure=state.get("existing_options_exposure", 0.0),
            days_to_expiration=strategy_result.get("strikes", {}).get("expiration_days", 30),
            persona=state.get("persona", ""),
        )
        return {"risk_assessment": result}
    except Exception as e:
        return {
            "risk_assessment": {
                "decision": "REJECT",
                "contracts": 0,
                "explanation": f"Risk gate error: {str(e)}",
            },
            "errors": state.get("errors", []) + [f"risk_gate_node: {str(e)}"],
        }


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def should_continue_after_volatility(state: OptionsState) -> str:
    """Skip straight to END if earnings override triggered."""
    if state.get("should_skip"):
        return "skip"
    return "continue"


def should_continue_after_thesis(state: OptionsState) -> str:
    if state.get("should_skip") or not state.get("thesis"):
        return "skip"
    return "continue"


def should_continue_after_strategy(state: OptionsState) -> str:
    if state.get("should_skip") or not state.get("strategy"):
        return "skip"
    return "continue"


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def build_options_graph():
    """
    Builds and compiles the options analysis subgraph.
    Returns a compiled LangGraph graph ready for .invoke().
    """
    graph = StateGraph(OptionsState)

    graph.add_node("volatility_node", volatility_node)
    graph.add_node("thesis_node", thesis_node)
    graph.add_node("strategy_node", strategy_node)
    graph.add_node("risk_gate_node", risk_gate_node)

    graph.set_entry_point("volatility_node")

    graph.add_conditional_edges(
        "volatility_node",
        should_continue_after_volatility,
        {"continue": "thesis_node", "skip": "risk_gate_node"},
    )
    graph.add_conditional_edges(
        "thesis_node",
        should_continue_after_thesis,
        {"continue": "strategy_node", "skip": "risk_gate_node"},
    )
    graph.add_conditional_edges(
        "strategy_node",
        should_continue_after_strategy,
        {"continue": "risk_gate_node", "skip": "risk_gate_node"},
    )

    graph.add_edge("risk_gate_node", END)

    return graph.compile()


# Singleton — compiled once at import time
options_pipeline = build_options_graph()


def run_options_pipeline(
    ticker: str,
    trader_config: TraderConfig,
    portfolio_value: float,
    news_summary: dict,
    tech_signals: dict,
    existing_options_exposure: float = 0.0,
    persona: str = "",
) -> dict:
    """
    Public entry point. Runs the full options pipeline for one
    ticker + trader combination. Returns the final state dict.

    Callers should check:
        result["risk_assessment"]["decision"] == "APPROVE"
    before submitting any order.
    """
    initial_state: OptionsState = {
        "ticker": ticker,
        "trader_config": trader_config,
        "portfolio_value": portfolio_value,
        "existing_options_exposure": existing_options_exposure,
        "news_summary": news_summary,
        "tech_signals": tech_signals,
        "persona": persona,
        "volatility": None,
        "thesis": None,
        "strategy": None,
        "risk_assessment": None,
        "errors": [],
        "should_skip": False,
    }

    return options_pipeline.invoke(initial_state)
