from crewai import Agent, Task, Crew
from crewai.tools import tool
from utils.db import supabase
from dotenv import load_dotenv
import os

load_dotenv()

os.environ["ANTHROPIC_API_KEY"] = os.getenv("ANTHROPIC_API_KEY")

# --- TOOLS ---
# These are the functions your agent can call to get data
# Add new tools here when you want to give the agent access to new data sources

@tool("get_recent_news")
def get_recent_news(limit: int = 20) -> str:
    """Fetches the most recent market news headlines from the database."""
    result = (
        supabase.table("news_items")
        .select("headline, source, published_at")
        .order("published_at", desc=True)
        .limit(limit)
        .execute()
    )
    if not result.data:
        return "No news found."
    
    lines = []
    for item in result.data:
        lines.append(f"[{item['published_at']}] {item['headline']} (Source: {item['source']})")
    return "\n".join(lines)


# --- AGENT DEFINITION ---
# This is where the agent's personality lives
# Change role/goal/backstory to reshape how it thinks and what it prioritizes

news_analyst = Agent(
    role="Market News Analyst",                                         # ← job title
    goal="Analyze financial news headlines and identify market sentiment, "
         "key themes, and potential impact on specific stocks or sectors.",
    backstory="""You are a seasoned financial news analyst with 15 years 
    of experience at major investment banks. You have a talent for cutting 
    through noise and identifying which headlines actually move markets. 
    You are skeptical of hype, attentive to Fed language, and always 
    consider second-order effects of news events. You score sentiment 
    on a scale of -1.0 (very bearish) to 1.0 (very bullish).""",       # ← personality lives here
    tools=[get_recent_news],
    llm="claude-haiku-4-5",                                            # ← swap model here
    verbose=True
)


# --- TASK DEFINITION ---
# This defines what you're asking the agent to actually do right now
# The expected_output shapes how the LLM structures its response

news_task = Task(
    description="""
    1. Fetch the most recent market news headlines
    2. Identify the top 3 most market-moving themes you see
    3. For each theme, name any tickers likely affected and assign a 
       sentiment score between -1.0 and 1.0
    4. Give an overall market sentiment score for today
    5. Flag any headlines that seem particularly significant or unusual
    """,
    expected_output="""
    A structured analysis with:
    - Top 3 themes with affected tickers and sentiment scores
    - Overall market sentiment score (-1.0 to 1.0)
    - Any flagged headlines worth special attention
    - A 2-3 sentence overall market summary
    """,
    agent=news_analyst
)


# --- CREW ---
# Orchestrates the agents - right now just one, we'll add more in the next step

crew = Crew(
    agents=[news_analyst],
    tasks=[news_task],
    verbose=True
)


def run_news_analysis():
    print("Running news analysis...")
    result = crew.kickoff()
    
    # Store the full reasoning in agent_decisions
    supabase.table("agent_decisions").insert({
        "agent_name": "News Analyst",
        "ticker": None,
        "action": "analyze",
        "reasoning": str(result),
        "confidence": None
    }).execute()
    
    print("\n--- ANALYST OUTPUT ---")
    print(result)
    return result


if __name__ == "__main__":
    run_news_analysis()