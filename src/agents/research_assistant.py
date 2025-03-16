from datetime import datetime
from typing import Literal
import asyncio
from langchain_community.tools import DuckDuckGoSearchResults, OpenWeatherMapQueryRun
from langchain_community.utilities import OpenWeatherMapAPIWrapper
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.runnables import RunnableConfig, RunnableLambda, RunnableSerializable
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, MessagesState, StateGraph
from langgraph.managed import RemainingSteps
from langgraph.prebuilt import ToolNode, create_react_agent
from agents.llama_guard import LlamaGuard, LlamaGuardOutput, SafetyAssessment
from core import get_model, settings
from langchain_mcp_adapters.client import MultiServerMCPClient
import os

class AgentState(MessagesState, total=False):
    """State for the research assistant agent."""
    safety: LlamaGuardOutput
    remaining_steps: RemainingSteps

current_date = datetime.now().strftime("%B %d, %Y")
instructions = f"""
    You are a helpful research assistant with the ability to search the web and use other tools.
    Today's date is {current_date}.
    NOTE: THE USER CAN'T SEE THE TOOL RESPONSE.
    A few things to remember:
    - Please include markdown-formatted links to any citations used in your response. Only include one
    or two citations per response unless more are needed. ONLY USE LINKS RETURNED BY THE TOOLS.
    - Use calculator tool with numexpr to answer math questions. The user does not understand numexpr,
      so for the final response, use human readable format - e.g. "300 * 200", not "(300 \\times 200)".
    """

_mcp_client = None

async def get_tools():
    """Get tools from MCP servers."""
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = MultiServerMCPClient(
            {
                "memory": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-memory"],
                    "transport": "stdio",
                }
            }
        )
        await _mcp_client.__aenter__()
    return _mcp_client.get_tools()

async def initialize_agent():
    """Initialize the agent with MCP tools."""
    tools = await get_tools()
    if settings.OPENWEATHERMAP_API_KEY:
        wrapper = OpenWeatherMapAPIWrapper(
            openweathermap_api_key=settings.OPENWEATHERMAP_API_KEY.get_secret_value()
        )
        tools.append(OpenWeatherMapQueryRun(name="Weather", api_wrapper=wrapper))
    
    model = get_model(settings.DEFAULT_MODEL)
    base_agent = create_react_agent(model, tools)
    
    # Create the graph
    graph = StateGraph(AgentState)
    graph.add_node("agent", base_agent)
    graph.set_entry_point("agent")
    graph.add_edge("agent", END)
    
    return graph.compile()

async def get_research_assistant():
    """Get the initialized research assistant agent."""
    global _agent
    if _agent is None:
        _agent = await initialize_agent()
        # Initialize with empty MemorySaver - will be replaced by service
        _agent.checkpointer = MemorySaver()
    return _agent

_agent = None
research_assistant = get_research_assistant
