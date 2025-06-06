import json
import logging
import warnings
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from langchain_core._api import LangChainBetaWarning
from langchain_core.messages import AIMessage, AIMessageChunk, AnyMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, Interrupt
from langsmith import Client as LangsmithClient

from agents import DEFAULT_AGENT, get_agent, get_all_agent_info
from agents.mcp_agent import initialize_agent
from core import settings
from memory import initialize_database
from schema import (
    ChatHistory,
    ChatHistoryInput,
    ChatMessage,
    Feedback,
    FeedbackResponse,
    ServiceMetadata,
    StreamInput,
    UserInput,
)
from service.utils import (
    convert_message_content_to_string,
    langchain_to_chat_message,
    remove_tool_calls,
)

# Cache for storing compiled graphs for different models
_agent_cache: dict[str, CompiledStateGraph] = {}
# Global reference to the checkpointer/saver
_saver = None

async def get_agent_with_model(agent_id: str, model_name: str | None = None) -> CompiledStateGraph:
    """Get an agent with a specific model.
    
    If agent_id is "mcp-agent" and model_name is provided, creates or retrieves a model-specific
    instance of the agent. Otherwise, falls back to the default agent.
    """
    global _saver
    
    if agent_id == "mcp-agent" and model_name:
        cache_key = f"{agent_id}:{model_name}"
        if cache_key not in _agent_cache:
            print(f"Creating new agent instance for model: {model_name}")
            agent = await initialize_agent(model_name)
            
            # Make sure new agents get a checkpointer assigned
            if _saver:
                agent.checkpointer = _saver
                print(f"Set checkpointer for model: {model_name}")
            else:
                print("WARNING: No checkpointer available to assign to the new agent!")
                
            _agent_cache[cache_key] = agent
            
        return _agent_cache[cache_key]
    
    # Fall back to standard agent retrieval for other agent types or when model isn't specified
    return await get_agent(agent_id)

# Set up logging and warnings
warnings.filterwarnings("ignore", category=LangChainBetaWarning)
logger = logging.getLogger(__name__)


def verify_bearer(
    http_auth: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(HTTPBearer(description="Please provide AUTH_SECRET api key.", auto_error=False)),
    ],
) -> None:
    if not settings.AUTH_SECRET:
        return
    auth_secret = settings.AUTH_SECRET.get_secret_value()
    if not http_auth or http_auth.credentials != auth_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Configurable lifespan that initializes the appropriate database checkpointer based on settings.
    """
    global _saver
    try:
        async with initialize_database() as saver:
            await saver.setup()
            agents = get_all_agent_info()
            for a in agents:
                agent = await get_agent(a.key)  # Await the async get_agent call
                agent.checkpointer = saver
            _saver = saver  # Store the saver/checkpointer in the global variable
            yield
    except Exception as e:
        logger.error(f"Error during database initialization: {e}")
        raise


app = FastAPI(lifespan=lifespan)
router = APIRouter(dependencies=[Depends(verify_bearer)])


@router.get("/info")
async def info() -> ServiceMetadata:
    models = list(settings.AVAILABLE_MODELS)
    models.sort()
    return ServiceMetadata(
        agents=get_all_agent_info(),
        models=models,
        default_agent=DEFAULT_AGENT,
        default_model=settings.DEFAULT_MODEL,
    )


async def _handle_input(
    user_input: UserInput, agent: CompiledStateGraph
) -> tuple[dict[str, Any], UUID]:
    """
    Parse user input and handle any required interrupt resumption.
    Returns kwargs for agent invocation and the run_id.
    """
    run_id = uuid4()
    thread_id = user_input.thread_id or str(uuid4())

    configurable = {"thread_id": thread_id, "model": user_input.model}

    if user_input.agent_config:
        if overlap := configurable.keys() & user_input.agent_config.keys():
            raise HTTPException(
                status_code=422,
                detail=f"agent_config contains reserved keys: {overlap}",
            )
        configurable.update(user_input.agent_config)

    config = RunnableConfig(
        configurable=configurable,
        run_id=run_id,
    )

    # Check for interrupts that need to be resumed
    state = await agent.aget_state(config=config)
    interrupted_tasks = [
        task for task in state.tasks if hasattr(task, "interrupts") and task.interrupts
    ]

    if interrupted_tasks:
        # assume user input is response to resume agent execution from interrupt
        input = Command(resume=user_input.message)
    else:
        input = {"messages": [HumanMessage(content=user_input.message)]}

    kwargs = {
        "input": input,
        "config": config,
    }

    return kwargs, run_id


@router.post("/{agent_id}/invoke")
@router.post("/invoke")
async def invoke(user_input: UserInput, agent_id: str = DEFAULT_AGENT) -> ChatMessage:
    """
    Invoke an agent with user input to retrieve a final response.

    If agent_id is not provided, the default agent will be used.
    Use thread_id to persist and continue a multi-turn conversation. run_id kwarg
    is also attached to messages for recording feedback.
    """
    agent: CompiledStateGraph = await get_agent_with_model(agent_id, user_input.model)
    kwargs, run_id = await _handle_input(user_input, agent)
    try:
        response_events = await agent.ainvoke(**kwargs, stream_mode=["updates", "values"])
        response_type, response = response_events[-1]
        if response_type == "values":
            # Normal response, the agent completed successfully
            output = langchain_to_chat_message(response["messages"][-1])
        elif response_type == "updates" and "__interrupt__" in response:
            # The last thing to occur was an interrupt
            # Return the value of the first interrupt as an AIMessage
            output = langchain_to_chat_message(
                AIMessage(content=response["__interrupt__"][0].value)
            )
        else:
            raise ValueError(f"Unexpected response type: {response_type}")

        output.run_id = str(run_id)
        return output
    except Exception as e:
        logger.error(f"An exception occurred: {e}")
        raise HTTPException(status_code=500, detail="Unexpected error")


async def message_generator(
    user_input: StreamInput, agent_id: str = DEFAULT_AGENT
) -> AsyncGenerator[str, None]:
    """
    Generate a stream of messages from the agent.

    This is the workhorse method for the /stream endpoint.
    """
    agent: CompiledStateGraph = await get_agent_with_model(agent_id, user_input.model)
    kwargs, run_id = await _handle_input(user_input, agent)
    
    # Keep track of final AI message to avoid duplication
    final_ai_message_sent = False
    is_last_update = False
    
    # Process streamed events from the graph and yield messages over the SSE stream.
    async for stream_event in agent.astream(
        **kwargs, stream_mode=["updates", "messages", "custom"]
    ):
        if not isinstance(stream_event, tuple):
            continue
        stream_mode, event = stream_event
        
        # Check if this is likely the last update
        if stream_mode == "updates":
            # Check if this is the last message in the sequence
            if event and isinstance(event, dict) and "messages" in list(event.values())[0]:
                is_last_update = True
                
        new_messages = []
        if stream_mode == "updates":
            for node, updates in event.items():
                # A simple approach to handle agent interrupts.
                # In a more sophisticated implementation, we could add
                # some structured ChatMessage type to return the interrupt value.
                if node == "__interrupt__":
                    interrupt: Interrupt
                    for interrupt in updates:
                        new_messages.append(AIMessage(content=interrupt.value))
                    continue
                update_messages = updates.get("messages", [])
                # special cases for using langgraph-supervisor library
                if node == "supervisor":
                    # Get only the last AIMessage since supervisor includes all previous messages
                    ai_messages = [msg for msg in update_messages if isinstance(msg, AIMessage)]
                    if ai_messages:
                        update_messages = [ai_messages[-1]]
                if node in ("research_expert", "math_expert"):
                    # By default the sub-agent output is returned as an AIMessage.
                    # Convert it to a ToolMessage so it displays in the UI as a tool response.
                    msg = ToolMessage(
                        content=update_messages[0].content,
                        name=node,
                        tool_call_id="",  # Assign a proper ID here
                    )
                    update_messages = [msg]
                new_messages.extend(update_messages)
        if stream_mode == "custom":
            new_messages = [event]
            
        for message in new_messages:
            try:
                chat_message = langchain_to_chat_message(message)
                chat_message.run_id = str(run_id)
                
                # Skip final AI message if we're using token streaming and it's the last update
                if (user_input.stream_tokens and 
                    is_last_update and 
                    isinstance(message, AIMessage) and 
                    not message.tool_calls and
                    not final_ai_message_sent):
                    # Mark that we're skipping the final message because we've streamed it
                    final_ai_message_sent = True
                    # Add skip_stream tag to this message
                    if not hasattr(message, "metadata"):
                        message.metadata = {}
                    if "tags" not in message.metadata:
                        message.metadata["tags"] = []
                    message.metadata["tags"].append("skip_stream")
            except Exception as e:
                logger.error(f"Error parsing message: {e}")
                yield f"data: {json.dumps({'type': 'error', 'content': 'Unexpected error'})}\n\n"
                continue
                
            # LangGraph re-sends the input message, which feels weird, so drop it
            if chat_message.type == "human" and chat_message.content == user_input.message:
                continue
                
            # If it's a tool message with empty tool_call_id, set a temporary ID
            if chat_message.type == "tool" and not chat_message.tool_call_id:
                chat_message.tool_call_id = f"temp_tool_{uuid4()}"
                logger.warning(f"Found empty tool_call_id, assigned temporary ID: {chat_message.tool_call_id}")
                
            yield f"data: {json.dumps({'type': 'message', 'content': chat_message.model_dump()})}\n\n"
            
        if stream_mode == "messages":
            if not user_input.stream_tokens:
                continue
            msg, metadata = event
            if "skip_stream" in metadata.get("tags", []):
                continue
            # For some reason, astream("messages") causes non-LLM nodes to send extra messages.
            # Drop them.
            if not isinstance(msg, AIMessageChunk):
                continue
            content = remove_tool_calls(msg.content)
            if content:
                # Empty content in the context of OpenAI usually means
                # that the model is asking for a tool to be invoked.
                # So we only print non-empty content.
                yield f"data: {json.dumps({'type': 'token', 'content': convert_message_content_to_string(content)})}\n\n"
                
    yield "data: [DONE]\n\n"


def _sse_response_example() -> dict[int, Any]:
    return {
        status.HTTP_200_OK: {
            "description": "Server Sent Event Response",
            "content": {
                "text/event-stream": {
                    "example": "data: {'type': 'token', 'content': 'Hello'}\n\ndata: {'type': 'token', 'content': ' World'}\n\ndata: [DONE]\n\n",
                    "schema": {"type": "string"},
                }
            },
        }
    }


@router.post(
    "/{agent_id}/stream",
    response_class=StreamingResponse,
    responses=_sse_response_example(),
)
@router.post("/stream", response_class=StreamingResponse, responses=_sse_response_example())
async def stream(user_input: StreamInput, agent_id: str = DEFAULT_AGENT) -> StreamingResponse:
    """
    Stream an agent's response to a user input, including intermediate messages and tokens.

    If agent_id is not provided, the default agent will be used.
    Use thread_id to persist and continue a multi-turn conversation. run_id kwarg
    is also attached to all messages for recording feedback.

    Set `stream_tokens=false` to return intermediate messages but not token-by-token.
    """
    return StreamingResponse(
        message_generator(user_input, agent_id),
        media_type="text/event-stream",
    )


@router.post("/feedback")
async def feedback(feedback: Feedback) -> FeedbackResponse:
    """
    Record feedback for a run to LangSmith.

    This is a simple wrapper for the LangSmith create_feedback API, so the
    credentials can be stored and managed in the service rather than the client.
    See: https://api.smith.langchain.com/redoc#tag/feedback/operation/create_feedback_api_v1_feedback_post
    """
    client = LangsmithClient()
    kwargs = feedback.kwargs or {}
    client.create_feedback(
        run_id=feedback.run_id,
        key=feedback.key,
        score=feedback.score,
        **kwargs,
    )
    return FeedbackResponse()


@router.post("/history")
async def history(input: ChatHistoryInput) -> ChatHistory:  # Make async
    """
    Get chat history.
    """
    # TODO: Hard-coding DEFAULT_AGENT here is wonky
    agent: CompiledStateGraph = await get_agent(DEFAULT_AGENT)  # Add await here
    try:
        state_snapshot = agent.get_state(
            config=RunnableConfig(
                configurable={
                    "thread_id": input.thread_id,
                }
            )
        )
        messages: list[AnyMessage] = state_snapshot.values["messages"]
        chat_messages: list[ChatMessage] = [langchain_to_chat_message(m) for m in messages]
        return ChatHistory(messages=chat_messages)
    except Exception as e:
        logger.error(f"An exception occurred: {e}")
        raise HTTPException(status_code=500, detail="Unexpected error")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}


app.include_router(router)
