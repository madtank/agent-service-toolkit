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
