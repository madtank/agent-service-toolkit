from datetime import datetime
import logging
import os
from typing import Literal, Optional
import asyncio

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.runnables import RunnableConfig, RunnableLambda, RunnableSerializable
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, MessagesState, StateGraph
from langgraph.managed import RemainingSteps
from langgraph.prebuilt import ToolNode, create_react_agent

from core import get_model, settings
from langchain_mcp_adapters.client import MultiServerMCPClient

logger = logging.getLogger(__name__)

class AgentState(MessagesState, total=False):
    """State for the MCP agent."""
    remaining_steps: RemainingSteps

_agent = None
_mcp_client: Optional[MultiServerMCPClient] = None
_tools = None

def get_mcp_config():
    """Get MCP server configuration based on environment."""
    # Check if we're running in Docker
    if os.environ.get("DOCKER_CONTAINER"):
        return {
            "memory": {
                "url": "http://memory:5001/sse",
                "transport": "sse",
            },
            "sequentialthinking": {
                "url": "http://sequentialthinking:5002/sse",
                "transport": "sse",
            }
        }
    # Local development
    return {
        "memory": {
            "url": "http://localhost:5001/sse",
            "transport": "sse",
        },
        "sequentialthinking": {
            "url": "http://localhost:5002/sse",
            "transport": "sse",
        }
    }

async def get_tools():
    """Get tools from external MCP servers."""
    global _mcp_client, _tools
    if _tools is None:
        try:
            _mcp_client = MultiServerMCPClient(get_mcp_config())
            await _mcp_client.__aenter__()
            _tools = _mcp_client.get_tools()
            if not _tools:
                logger.warning("No tools were returned from MCP servers")
            else:
                logger.info(f"Successfully loaded {len(_tools)} tools from MCP servers")
            return _tools
        except Exception as e:
            logger.warning(f"Failed to connect to MCP servers: {e}")
            _tools = []
    return _tools

current_date = datetime.now().strftime("%B %d, %Y")
instructions = f"""
    You are an advanced assistant with access to a knowledge graph-based memory system and sequential thinking tools.
    Today's date is {current_date}.

    You have access to two powerful systems:
    1. A knowledge graph memory system that can store and retrieve information about entities and their relationships
    2. A sequential thinking system that helps break down and solve complex problems step by step
    
    Use these tools when they're available to:
    - Build and maintain a knowledge graph of important information
    - Break down complex problems into manageable steps
    - Track and recall information across conversations
    
    If the tools are not available, continue functioning as a helpful assistant.
"""

async def initialize_agent():
    """Initialize the MCP agent with tools from external servers."""
    tools = await get_tools()
    
    model = get_model(settings.DEFAULT_MODEL)
    base_agent = create_react_agent(model, tools)
    
    graph = StateGraph(AgentState)
    graph.add_node("agent", base_agent)
    graph.set_entry_point("agent")
    graph.add_edge("agent", END)
    
    compiled = graph.compile()
    compiled.checkpointer = MemorySaver()
    return compiled

async def get_mcp_agent():
    """Get the initialized MCP agent."""
    global _agent
    if _agent is None:
        _agent = await initialize_agent()
    return _agent

# This will be imported by __init__.py
get_agent = get_mcp_agent