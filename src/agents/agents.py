from typing import Awaitable, Callable, Union
from dataclasses import dataclass

from langgraph.graph.state import CompiledStateGraph

from agents.research_assistant import research_assistant
from agents.mcp_agent import get_research_assistant as get_mcp_agent
from schema import AgentInfo

DEFAULT_AGENT = "mcp-agent"

@dataclass
class Agent:
    description: str
    graph: CompiledStateGraph

# Define agent getters - can be either sync (returning the graph directly) or async (returning a promise of a graph)
AGENTS: dict[str, Union[CompiledStateGraph, Callable[[], Awaitable[CompiledStateGraph]]]] = {
    "research-assistant": research_assistant,
    "mcp-agent": get_mcp_agent,
}

async def get_agent(agent_id: str = DEFAULT_AGENT) -> CompiledStateGraph:
    """Get an agent by ID."""
    agent_getter = AGENTS.get(agent_id)
    if not agent_getter:
        raise ValueError(f"Agent {agent_id} not found")
    
    # If agent_getter is already a CompiledStateGraph, return it directly
    if isinstance(agent_getter, CompiledStateGraph):
        return agent_getter
    # Otherwise, call the async function to get the agent
    return await agent_getter()

def get_all_agent_info() -> list[AgentInfo]:
    """Get info about all available agents."""
    return [
        AgentInfo(
            key="research-assistant",
            description="Ask me anything! I can search for information and do calculations."
        ),
        AgentInfo(
            key="mcp-agent",
            description="Research assistant with access to knowledge graphs and MCP tools."
        )
    ]
