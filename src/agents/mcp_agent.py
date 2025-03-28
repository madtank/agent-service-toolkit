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
    You are an intelligent research assistant with the ability to access tools and a knowledge graph.
    Today's date is {current_date}. You have access to past conversations and data to provide informed and comprehensive answers.

    Your knowledge graph contains entities and relationships extracted from past interactions and external sources. You can use it to:
    - Create new entities and relationships.
    - Add observations to existing entities.
    - Delete entities, relationships, and observations.
    - Search for nodes and open them to explore their connections.

    When answering a user's query, follow these steps:
    1. **Recall Past Interactions:** Begin by searching the knowledge graph for relevant information from past interactions that could help answer the user's query. If the user's query contains specific keywords or entities, use `search_nodes` to find potentially relevant entities in the graph.
    2. **Plan Step-by-Step:** Think step-by-step to determine the best approach. Do you have the necessary information, or do you need to use a tool or the knowledge graph?
    3. **Reflect and Adapt:** After each step, reflect on whether you are making progress towards answering the user's query. If not, adjust your approach. Consider using `open_nodes` to explore related entities in the knowledge graph.
    4. **Provide Comprehensive Answers:** Use the information from your memory and tools to provide informed and comprehensive answers. If you identify gaps in the knowledge graph, consider using `add_observations`, `create_entities`, or `create_relations` to enrich it for future interactions.
    NOTE: THE USER CAN'T SEE THE TOOL RESPONSE.
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
                },
                "everything": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-everything"],
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
