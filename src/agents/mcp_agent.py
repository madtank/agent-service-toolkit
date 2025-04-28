from datetime import datetime
from typing import Literal, Dict, Any
import json
import os
import os.path
import re
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, MessagesState, StateGraph
from langgraph.managed import RemainingSteps
from langgraph.prebuilt import create_react_agent
from agents.llama_guard import LlamaGuard, LlamaGuardOutput, SafetyAssessment
from core import get_model, settings
from pydantic import SecretStr
from langchain_mcp_adapters.client import MultiServerMCPClient

# Ensure directories exist
os.makedirs(settings.MEMORY_DIR_PATH, exist_ok=True)
os.makedirs(settings.DATA_DIR_PATH, exist_ok=True)
os.makedirs(settings.CONFIG_DIR_PATH, exist_ok=True)

# Load MCP server configuration from JSON file
def load_mcp_config() -> Dict[str, Any]:
    try:
        # Read the config file as text
        with open(settings.MCP_CONFIG_FILE_PATH, 'r') as f:
            config_text = f.read()
        
        # Find all ${VAR_NAME} patterns in the config
        var_pattern = r'\${([A-Za-z0-9_]+)}'
        matches = re.findall(var_pattern, config_text)
        
        # Replace each variable with its value from settings
        for var_name in matches:
            value = ""
            if hasattr(settings, var_name):
                attr = getattr(settings, var_name)
                # Handle SecretStr values
                if isinstance(attr, SecretStr):
                    value = attr.get_secret_value()
                else:
                    value = str(attr)
            
            config_text = config_text.replace(f"${{{var_name}}}", value)
        
        # Parse the processed text as JSON
        config = json.loads(config_text)
        return config.get('servers', {})
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Error loading MCP config from {settings.MCP_CONFIG_FILE_PATH}: {e}")
        # Return a default empty config
        return {}

# Get the MCP server configuration
SERVERS_CONFIG = load_mcp_config()

class AgentState(MessagesState, total=False):
    """State for the research assistant agent."""
    safety: LlamaGuardOutput
    remaining_steps: RemainingSteps

current_date = datetime.now().strftime("%B %d, %Y")
instructions = f"""
## === System Prompt for Memory-Powered Assistant =================================
You are an intelligent research assistant with access to:
- A **knowledge graph memory server** (MCP "memory") that stores entities, relations, & observations.
- A **web search tool** ("Perplexity/Sonar") for fresh or missing information.

Today's date: {current_date}.

--------------------------- 0. On Every Turn --------------------------------------
Do **all** of the following *before* crafting your visible reply:

    A. ***Memory scan***  
         1. Use **search_nodes** with salient keywords from the new user message  
                - also search for the user's entity (default_user) if not already loaded.  
         2. If results look useful, call **open_nodes** to pull full details.  
         3. Briefly reflect: "Does any of this materially help me answer?"  
                - If yes, weave it into your reasoning.  
                - If no, continue; do NOT invent or hallucinate.

    B. ***External check***  
         - If the question needs up-to-date facts (news, pricing, sports, etc.) **and** memory didn't answer it,  
             use the Perplexity/Sonar web-search tool.

--------------------------- 1. Reply Construction ----------------------------------
Think step-by-step, but expose only the *useful* reasoning to the user.  
Be clear when something is uncertain ("Based on my best current understanding…")  
Offer concise, actionable takeaways first; deeper detail can follow.

--------------------------- 2. Memory-Write Policy ---------------------------------
After writing your reply, evaluate whether to update memory:

    - **Store** only if the information is:
            - Stable for >1 week (identity, long-term goals, recurring preferences, key relationships)  
            - Helpful in future chats (e.g., user's tech stack, favourite learning style)  
            - Atomic (one fact per observation)  
    - **Do NOT store**:
            - Ephemeral emotions ("I'm tired today") or single-use logistics  
            - Information the user explicitly asks you to forget  
    - Implementation:
            - New person/org/concept → **create_entities** (name, type, first observation)  
            - New stable fact about existing node → **add_observations**  
            - Link two nodes logically → **create_relations**

If nothing meets the criteria, skip the write.

--------------------------- 3. Memory Hygiene --------------------------------------
Periodically prune with **delete_observations / delete_entities** if data becomes stale, wrong, or redundant.

▶ *Remember*: Users never see raw tool outputs. Keep tool calls silent and surface only the insights.
## ================================================================================

## TOOL REGISTRY (active in this container)
- Perplexity/Sonar - powerful web search for current events, facts, and information not in memory.
- Knowledge Graph - create/update/search entities, relations, and observations in persistent memory.

NOTE: THE USER CAN'T SEE THE TOOL RESPONSE.
"""

_mcp_client = None

async def get_tools():
    """Get tools from MCP servers."""
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = MultiServerMCPClient(SERVERS_CONFIG)
        try:
            await _mcp_client.__aenter__()
        except Exception as e:
            print(f"Error initializing MCP client: {e}")
            return []
    return _mcp_client.get_tools()

async def initialize_agent(model_name: str | None = None):
    """Initialize the agent with MCP tools.
    
    Args:
        model_name: Optional model name to use. If None, uses settings.DEFAULT_MODEL.
    """
    tools = await get_tools()
    
    # Use the specified model or fall back to default
    actual_model_name = model_name or settings.DEFAULT_MODEL
    print(f"MODEL SELECTION: {actual_model_name}")
    
    model = get_model(actual_model_name)
    print(f"Using model: {actual_model_name} (no fallback)")
    
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
