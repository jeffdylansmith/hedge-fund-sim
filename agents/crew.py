from crewai import Crew, Process
from agents.news_analyst import news_analyst, news_task
from agents.technical_analyst import technical_analyst, technical_task
from agents.portfolio_manager import portfolio_manager, portfolio_task
from utils.db import supabase
from dotenv import load_dotenv
from datetime import datetime
import os

load_dotenv()

os.environ["ANTHROPIC_API_KEY"] = os.getenv("ANTHROPIC_API_KEY")

def run_full_analysis():
    print(f"\n{'='*50}")
    print(f"HEDGE FUND SIM - Full Run")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}\n")

    crew = Crew(
        agents=[news_analyst, technical_analyst, portfolio_manager],
        tasks=[news_task, technical_task, portfolio_task],
        process=Process.sequential,
        verbose=True
    )

    result = crew.kickoff()

    # Store master crew result
    supabase.table("agent_decisions").insert({
        "agent_name": "Master Crew",
        "ticker": None,
        "action": "full_run",
        "reasoning": str(result),
        "confidence": None
    }).execute()

    # Also store portfolio manager output separately so trade executor can find it
    supabase.table("agent_decisions").insert({
        "agent_name": "Portfolio Manager",
        "ticker": None,
        "action": "portfolio_review",
        "reasoning": str(result),  # final output is the PM's decision
        "confidence": None
    }).execute()

    print(f"\n{'='*50}")
    print(f"Run complete: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}\n")

    return result

if __name__ == "__main__":
    run_full_analysis()