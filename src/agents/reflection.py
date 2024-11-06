from typing import Literal
from langgraph.graph import END, StateGraph
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from agents.models import models

# Define agent state
class ReflectionAgentState:
    messages: list

# Define instructions for the agent
instructions = """
You are a reflective assistant that generates and critiques output iteratively to ensure quality.
"""

# Define model wrapping with reflection
def wrap_model_with_reflection(model):
    preprocessor = RunnableLambda(
        lambda state: [SystemMessage(content=instructions)] + state["messages"],
        name="StateModifier",
    )
    return preprocessor | model

# Generation Node
async def generate_content(state: ReflectionAgentState, config):
    model = wrap_model_with_reflection(models["gpt-4o-mini"])
    response = await model.ainvoke(state["messages"], config)
    return {"messages": state["messages"] + [response]}

# Reflection Node
async def reflect_on_content(state: ReflectionAgentState, config):
    last_message = state["messages"][-1]
    reflection_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "You are tasked with critiquing the following content and suggesting improvements."),
            MessagesPlaceholder(variable_name="messages"),
        ]
    )
    reflection_model = wrap_model_with_reflection(models["gpt-4o-mini"])
    reflection_response = await reflection_model.ainvoke(state["messages"] + [last_message], config)
    return {"messages": state["messages"] + [reflection_response]}

# Define Reflection Graph
reflection_graph = StateGraph(ReflectionAgentState)
reflection_graph.add_node("generate", generate_content)
reflection_graph.add_node("reflect", reflect_on_content)
reflection_graph.set_entry_point("generate")

# Add edges for iteration
reflection_graph.add_edge("generate", "reflect")
reflection_graph.add_edge("reflect", "generate")

# Add stopping condition
def stop_iteration(state: ReflectionAgentState) -> Literal["end", "generate"]:
    return "end" if len(state["messages"]) >= 6 else "generate"

reflection_graph.add_conditional_edges("reflect", stop_iteration, {"end": END, "generate": "generate"})

# Compile the graph
reflection_agent = reflection_graph.compile(
    checkpointer=MemorySaver(),
)