from typing import Callable, Awaitable
from dataclasses import dataclass

from langgraph.graph.state import CompiledStateGraph

from agents.research_assistant import get_research_assistant
from agents.mcp_agent import get_mcp_agent  # Updated import
from schema import AgentInfo

DEFAULT_AGENT = "research-assistant"

@dataclass
class Agent:
    description: str
    graph: CompiledStateGraph

# Define agent getters
AGENTS: dict[str, Callable[[], Awaitable[CompiledStateGraph]]] = {
    "research-assistant": get_research_assistant,
    "mcp": get_mcp_agent,  # Using the getter directly
}

async def get_agent(agent_id: str = DEFAULT_AGENT) -> CompiledStateGraph:
    """Get an agent by ID."""
    agent_getter = AGENTS.get(agent_id)
    if not agent_getter:
        raise ValueError(f"Agent {agent_id} not found")
    return await agent_getter()

def get_all_agent_info() -> list[AgentInfo]:
    """Get info about all available agents."""
    return [
        AgentInfo(
            key="research-assistant",
            description="Ask me anything! I can search for information and do calculations."
        ),
        AgentInfo(
            key="mcp",
            description="An agent with knowledge graph memory and sequential thinking capabilities"
        )
    ]
