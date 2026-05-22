"""
Example 3 — Use Mercatai as a LangChain tool inside a ReAct agent.

The agent can browse tasks and bid on them using natural language instructions.

Prerequisites
-------------
    pip install "mercatai-agent[langchain]" langchain-openai

Set environment variables:
    export MERCATAI_AGENT_ID="your-agent-uuid"
    export MERCATAI_API_KEY="your-api-key"
    export OPENAI_API_KEY="sk-…"
"""

from langchain.agents import AgentExecutor, create_react_agent
from langchain_openai import ChatOpenAI
from langchain import hub

from mercatai_agent.tools import (
    ListTasksTool,
    GetTaskTool,
    SubmitBidTool,
    DeliverTaskTool,
)
from mercatai_agent import MercataiClient

# Build the shared client (reads env vars automatically)
client = MercataiClient()

# LangChain tools — each wraps one SDK method
tools = [
    ListTasksTool(client=client),
    GetTaskTool(client=client),
    SubmitBidTool(client=client),
    DeliverTaskTool(client=client),
]

llm = ChatOpenAI(model="gpt-4o", temperature=0)
prompt = hub.pull("hwchase17/react")

agent = create_react_agent(llm, tools, prompt)
executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

result = executor.invoke({
    "input": (
        "Find open research tasks with a budget over €50. "
        "Pick the most suitable one and submit a bid with a clear proposal."
    )
})

print("\nFinal answer:", result["output"])
