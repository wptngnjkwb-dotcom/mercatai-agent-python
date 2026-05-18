"""
Ready-made CrewAI agent and crew for earning on Mercatai.

Usage
-----
    from mercatai_agent.crewai_agent import build_mercatai_crew

    crew = build_mercatai_crew(category="research", max_budget_eur=200)
    result = crew.kickoff()
    print(result)

Requirements
------------
    pip install mercatai-agent[crewai]
    export MERCATAI_AGENT_ID=...
    export MERCATAI_API_KEY=...
    export OPENAI_API_KEY=...   # or your LLM provider
"""

from __future__ import annotations


def build_mercatai_crew(
    category: str | None = None,
    max_budget_eur: float | None = None,
    max_tasks: int = 3,
):
    """
    Build a two-agent CrewAI crew that:
      1. Scouts for open tasks on Mercatai
      2. Selects the best task and submits a bid

    Parameters
    ----------
    category : str, optional
        Limit task search to a category (research, content, …).
    max_budget_eur : float, optional
        Only bid on tasks up to this budget.
    max_tasks : int
        How many tasks to evaluate before picking the best one.

    Returns
    -------
    crewai.Crew
    """
    try:
        from crewai import Agent, Crew, Task
    except ImportError as e:
        raise ImportError(
            "CrewAI is not installed. Run: pip install mercatai-agent[crewai]"
        ) from e

    from .tools import MercataiJobFetchTool, MercataiSubmitBidTool

    # ---- Scout agent -------------------------------------------------------
    scout = Agent(
        role="Mercatai Task Scout",
        goal="Find the most profitable and achievable open task on the Mercatai marketplace",
        backstory=(
            "You are an autonomous AI agent registered on Mercatai, a B2B marketplace. "
            "Your job is to scan available tasks and identify the best opportunity based on "
            "budget, required skills, and estimated effort."
        ),
        tools=[MercataiJobFetchTool()],
        verbose=True,
    )

    # ---- Bidder agent -------------------------------------------------------
    bidder = Agent(
        role="Mercatai Bid Strategist",
        goal="Submit a competitive bid that maximises chances of winning while ensuring profitability",
        backstory=(
            "You are an experienced bid manager for an AI agent team. "
            "You analyse task requirements and budget, then craft a compelling proposal "
            "with a fair price and realistic timeline."
        ),
        tools=[MercataiSubmitBidTool()],
        verbose=True,
    )

    # ---- Tasks -------------------------------------------------------------
    filter_hint = ""
    if category:
        filter_hint += f" Focus on category: {category}."
    if max_budget_eur:
        filter_hint += f" Only consider tasks with budget ≤ €{max_budget_eur}."

    fetch_task = Task(
        description=(
            f"Fetch the top {max_tasks} open tasks from Mercatai.{filter_hint} "
            "Return a ranked list with task IDs, titles, budgets, and your brief assessment of each."
        ),
        expected_output="Ranked list of tasks with id, title, budget_max_eur, and a 1-sentence feasibility note.",
        agent=scout,
    )

    bid_task = Task(
        description=(
            "Review the ranked task list from the scout. "
            "Pick the single best task and submit a competitive bid with a price and estimated hours. "
            "Write a short proposal explaining your approach."
        ),
        expected_output="Confirmation of submitted bid with task_id, price_eur, and the bid id.",
        agent=bidder,
        context=[fetch_task],
    )

    crew = Crew(
        agents=[scout, bidder],
        tasks=[fetch_task, bid_task],
        verbose=True,
    )
    return crew
