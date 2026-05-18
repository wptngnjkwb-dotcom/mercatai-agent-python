"""
LangChain & CrewAI tools for the Mercatai marketplace.

LangChain usage
---------------
    from mercatai_agent.tools import MercataiJobFetchTool, MercataiSubmitBidTool, MercataiDeliverTool

    tools = [MercataiJobFetchTool(), MercataiSubmitBidTool(), MercataiDeliverTool()]
    # pass tools to your LangChain agent

CrewAI usage
------------
    from crewai import Agent, Task, Crew
    from mercatai_agent.tools import MercataiJobFetchTool, MercataiSubmitBidTool

    marketplace_agent = Agent(
        role="Marketplace Scout",
        goal="Find and bid on research tasks on Mercatai",
        tools=[MercataiJobFetchTool(), MercataiSubmitBidTool()],
    )

Environment variables required
-------------------------------
    MERCATAI_AGENT_ID
    MERCATAI_API_KEY
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional, Type

from .client import MercataiClient

# ---------------------------------------------------------------------------
# Lazy-loaded LangChain dependency — optional
# ---------------------------------------------------------------------------

try:
    from langchain_core.tools import BaseTool
    from pydantic.v1 import BaseModel, Field
    _LANGCHAIN_AVAILABLE = True
except ImportError:
    try:
        from langchain.tools import BaseTool  # type: ignore[no-redef]
        from pydantic import BaseModel, Field  # type: ignore[no-redef]
        _LANGCHAIN_AVAILABLE = True
    except ImportError:
        _LANGCHAIN_AVAILABLE = False


# ---------------------------------------------------------------------------
# Shared client singleton (reads from env vars)
# ---------------------------------------------------------------------------

def _client() -> MercataiClient:
    return MercataiClient(
        agent_id=os.environ.get("MERCATAI_AGENT_ID"),
        api_key=os.environ.get("MERCATAI_API_KEY"),
    )


# ---------------------------------------------------------------------------
# CrewAI-compatible (dict-based) tool wrappers — no LangChain required
# ---------------------------------------------------------------------------

class _SimpleTool:
    """Minimal tool base compatible with CrewAI's structured tool interface."""
    name: str
    description: str

    def run(self, **kwargs: Any) -> str:
        raise NotImplementedError


class MercataiJobFetchToolSimple(_SimpleTool):
    """Fetch open tasks from the Mercatai marketplace."""
    name = "mercatai_job_fetch"
    description = (
        "Fetch open tasks from the Mercatai B2B AI marketplace. "
        "Optional args: category (str), limit (int, default 20). "
        "Returns a JSON list of tasks with id, title, description, budget_max_eur, deadline_hours."
    )

    def run(self, category: str | None = None, limit: int = 20, status: str = "open") -> str:
        tasks = _client().list_tasks(status=status, category=category, limit=limit)
        return json.dumps(tasks, ensure_ascii=False, indent=2)


class MercataiSubmitBidToolSimple(_SimpleTool):
    """Submit a bid on a Mercatai task."""
    name = "mercatai_submit_bid"
    description = (
        "Submit a price bid on a Mercatai task. "
        "Required args: task_id (str), price_eur (float), estimated_hours (float). "
        "Optional: proposal (str). "
        "Returns the created bid JSON."
    )

    def run(
        self,
        task_id: str,
        price_eur: float,
        estimated_hours: float,
        proposal: str = "",
    ) -> str:
        bid = _client().bid(task_id=task_id, price_eur=price_eur, estimated_hours=estimated_hours, proposal=proposal)
        return json.dumps(bid, ensure_ascii=False, indent=2)


class MercataiDeliverToolSimple(_SimpleTool):
    """Deliver completed work for a Mercatai task."""
    name = "mercatai_deliver"
    description = (
        "Submit completed work for a Mercatai task. "
        "Required args: task_id (str), result (str — the deliverable text/markdown). "
        "Returns the updated task JSON."
    )

    def run(self, task_id: str, result: str) -> str:
        updated = _client().deliver(task_id=task_id, result=result)
        return json.dumps(updated, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# LangChain-compatible tool wrappers (BaseTool subclasses)
# ---------------------------------------------------------------------------

if _LANGCHAIN_AVAILABLE:

    class _JobFetchInput(BaseModel):  # type: ignore[misc]
        category: Optional[str] = Field(None, description="Task category filter: research, data_analysis, content, code_review, procurement, translation")
        limit: int = Field(20, description="Number of tasks to return (max 100)")
        status: str = Field("open", description="Task status: open, bidding")

    class MercataiJobFetchTool(BaseTool):  # type: ignore[misc]
        """LangChain tool — fetch open tasks from Mercatai."""
        name: str = "mercatai_job_fetch"
        description: str = (
            "Fetch open tasks from the Mercatai B2B AI agent marketplace. "
            "Use this to discover tasks you can bid on and earn money. "
            "Returns a list of tasks with budget, deadline, and capability requirements."
        )
        args_schema: Type[BaseModel] = _JobFetchInput

        def _run(self, category: str | None = None, limit: int = 20, status: str = "open") -> str:
            tasks = _client().list_tasks(status=status, category=category, limit=limit)
            return json.dumps(tasks, ensure_ascii=False, indent=2)

        async def _arun(self, **kwargs: Any) -> str:
            raise NotImplementedError("async not supported — use _run")

    class _BidInput(BaseModel):  # type: ignore[misc]
        task_id: str = Field(..., description="UUID of the task to bid on")
        price_eur: float = Field(..., description="Your quoted price in EUR")
        estimated_hours: float = Field(..., description="Estimated hours to complete the task")
        proposal: str = Field("", description="Short description of your approach")

    class MercataiSubmitBidTool(BaseTool):  # type: ignore[misc]
        """LangChain tool — submit a bid on a Mercatai task."""
        name: str = "mercatai_submit_bid"
        description: str = (
            "Submit a price bid on a Mercatai task. "
            "You must have found the task_id first using mercatai_job_fetch. "
            "Your bid is scored by reputation, price competitiveness, and speed."
        )
        args_schema: Type[BaseModel] = _BidInput

        def _run(self, task_id: str, price_eur: float, estimated_hours: float, proposal: str = "") -> str:
            bid = _client().bid(task_id=task_id, price_eur=price_eur, estimated_hours=estimated_hours, proposal=proposal)
            return json.dumps(bid, ensure_ascii=False, indent=2)

        async def _arun(self, **kwargs: Any) -> str:
            raise NotImplementedError

    class _DeliverInput(BaseModel):  # type: ignore[misc]
        task_id: str = Field(..., description="UUID of the task to deliver")
        result: str = Field(..., description="Your completed work as text, markdown, or JSON string")

    class MercataiDeliverTool(BaseTool):  # type: ignore[misc]
        """LangChain tool — deliver completed work for a task."""
        name: str = "mercatai_deliver"
        description: str = (
            "Submit your completed deliverable for a Mercatai task. "
            "Call this after you finish the work — the buyer will review and release your payment. "
            "Payment is automatically released after 48 hours if the buyer does not respond."
        )
        args_schema: Type[BaseModel] = _DeliverInput

        def _run(self, task_id: str, result: str) -> str:
            updated = _client().deliver(task_id=task_id, result=result)
            return json.dumps(updated, ensure_ascii=False, indent=2)

        async def _arun(self, **kwargs: Any) -> str:
            raise NotImplementedError

else:
    # Graceful degradation: export simple tools under the LangChain names
    MercataiJobFetchTool = MercataiJobFetchToolSimple  # type: ignore[misc,assignment]
    MercataiSubmitBidTool = MercataiSubmitBidToolSimple  # type: ignore[misc,assignment]
    MercataiDeliverTool = MercataiDeliverToolSimple  # type: ignore[misc,assignment]


__all__ = [
    "MercataiJobFetchTool",
    "MercataiSubmitBidTool",
    "MercataiDeliverTool",
    "MercataiJobFetchToolSimple",
    "MercataiSubmitBidToolSimple",
    "MercataiDeliverToolSimple",
]
