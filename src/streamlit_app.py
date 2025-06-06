import asyncio
import os
import uuid
from collections.abc import AsyncGenerator

import streamlit as st
from dotenv import load_dotenv
from pydantic import ValidationError
from streamlit.runtime.scriptrunner import get_script_run_ctx

from client import AgentClient, AgentClientError
from schema import ChatHistory, ChatMessage
from schema.task_data import TaskData, TaskDataStatus

# Handle import for get_tools with fallback options
try:
    from agents.mcp_agent import get_tools
except ModuleNotFoundError:
    # Try alternative import paths
    try:
        from src.agents.mcp_agent import get_tools
    except ModuleNotFoundError:
        print("Could not import get_tools from agents.mcp_agent")
        # Define a placeholder function to avoid breaking the app
        async def get_tools():
            return []


APP_TITLE = "MCP Agent"
APP_ICON = "🧰"

# Optional: map model names to custom avatar images.
# Replace the URLs with your own images or local file paths as desired.
MODEL_AVATARS = {
    "gpt-4o": "https://raw.githubusercontent.com/madtank/assets/main/avatars/gpt-4o.png",
    "claude-3-haiku": "https://raw.githubusercontent.com/madtank/assets/main/avatars/claude-haiku.png",
    # Add more entries here …
}

def model_avatar(model_name: str) -> str | None:
    """Return an avatar image for a given model name, if one is configured."""
    return MODEL_AVATARS.get(model_name)

async def main() -> None:
    st.set_page_config(
        page_title=APP_TITLE,
        page_icon=APP_ICON,
        menu_items={},
        initial_sidebar_state="collapsed",
    )

    if "agent_client" not in st.session_state:
        load_dotenv()
        agent_url = os.getenv("AGENT_URL")
        if not agent_url:
            host = os.getenv("HOST", "0.0.0.0")
            port = os.getenv("PORT", 8080)
            agent_url = f"http://{host}:{port}"
        try:
            with st.spinner("Connecting to agent service..."):
                st.session_state.agent_client = AgentClient(base_url=agent_url)
        except AgentClientError as e:
            st.error(f"Error connecting to agent service at {agent_url}: {e}")
            st.markdown("The service might be booting up. Try again in a few seconds.")
            st.stop()
    agent_client: AgentClient = st.session_state.agent_client

    # Check for a new chat action
    if st.session_state.get("start_new_chat", False):
        # Generate a completely new UUID for the thread ID to ensure no context is preserved
        thread_id = str(uuid.uuid4())
        st.session_state.messages = []
        st.session_state.thread_id = thread_id
        st.session_state.start_new_chat = False
        # Clear any URL parameters
        st.query_params.clear()
    elif "thread_id" not in st.session_state:
        thread_id = st.query_params.get("thread_id")
        if not thread_id:
            thread_id = get_script_run_ctx().session_id
            messages = []
        else:
            try:
                messages: ChatHistory = agent_client.get_history(thread_id=thread_id).messages
            except AgentClientError:
                st.error("No message history found for this Thread ID.")
                messages = []
        st.session_state.messages = messages
        st.session_state.thread_id = thread_id

    # --- Sidebar Configuration ---
    with st.sidebar:
        st.header(f"{APP_ICON} {APP_TITLE}")

        # New Chat button at the top of the sidebar
        if st.button("💬 New Chat", use_container_width=True):
            st.session_state.start_new_chat = True
            st.rerun()

        # Direct model and agent selectors in the sidebar
        st.subheader("Settings")
        model_idx = agent_client.info.models.index(agent_client.info.default_model)
        model = st.selectbox("LLM to use", options=agent_client.info.models, index=model_idx)
        
        agent_list = [a.key for a in agent_client.info.agents]
        agent_idx = agent_list.index(agent_client.info.default_agent)
        agent_client.agent = st.selectbox(
            "Agent to use",
            options=agent_list,
            index=agent_idx,
        )
        use_streaming = st.toggle("Stream results", value=True)

        # MCP Tools section removed for now - to be implemented in future enhancement
        # st.subheader("Available MCP Tools")
        # st.info("MCP Tools display coming soon")

        # View source code link
        st.markdown("[View MCP Agent source code](https://github.com/YOUR_USERNAME/mcp-agent)")
    
    # --- Quick Actions on Top of the Main Page ---
    st.subheader("Quick Actions")
    
    # Create tabs for different categories of tests
    tabs = st.tabs(["Basic Tests", "File Operations", "Complex Tests", "Fun Demos"])
    
    # Only show buttons if we're not currently processing a sample input
    if not st.session_state.get("sample_input"):
        # Basic Tests tab
        with tabs[0]:
            st.markdown("##### Simple commands to test basic functionality")
            col1, col2 = st.columns(2)
            with col1:
                if st.button("🛠️ List Available Tools", key="list_tools_btn", use_container_width=True):
                    st.session_state.sample_input = "What tools do you have available?"
                    st.rerun()
                if st.button("📝 Check Memory", key="check_history_btn", use_container_width=True):
                    st.session_state.sample_input = "What have we discussed before? Please check your memory but don't list your tools again."
                    st.rerun()
            with col2:
                if st.button("📅 What's Today's Date?", key="date_btn", use_container_width=True):
                    st.session_state.sample_input = "What is today's date? Can you also tell me what day of the week it is?"
                    st.rerun()
                if st.button("🌐 Current Events", key="news_btn", use_container_width=True):
                    st.session_state.sample_input = "What are 2-3 recent technology news headlines? Keep your response brief and focused."
                    st.rerun()
        
        # File Operations tab
        with tabs[1]:
            st.markdown("##### Test file reading and manipulation capabilities")
            col1, col2 = st.columns(2)
            with col1:
                if st.button("📂 List Files", key="list_files_btn", use_container_width=True):
                    st.session_state.sample_input = "Can you list all the files in the data directory?"
                    st.rerun()
                if st.button("📄 Read Sample File", key="read_file_btn", use_container_width=True):
                    st.session_state.sample_input = "Please read the contents of data/todays_date.txt and summarize what you find."
                    st.rerun()
            with col2:
                if st.button("📋 Create Note", key="create_note_btn", use_container_width=True):
                    st.session_state.sample_input = "Can you create a new text file in the data directory named 'test_note.txt' with today's date and a brief greeting?"
                    st.rerun()
                if st.button("🔍 Find in Files", key="find_in_files_btn", use_container_width=True):
                    st.session_state.sample_input = "Search through files in the data directory for any mentions of 'language models' or 'LLMs'."
                    st.rerun()
        
        # Complex Tests tab
        with tabs[2]:
            st.markdown("##### More complex multi-step operations")
            col1, col2 = st.columns(2)
            with col1:
                if st.button("🔄 Process & Transform", key="process_btn", use_container_width=True):
                    st.session_state.sample_input = "Read data/llms-full.txt if it exists, count how many different LLM providers are mentioned, and create a summary file with the count and list of providers."
                    st.rerun()
                if st.button("🧮 Data Analysis", key="analysis_btn", use_container_width=True):
                    st.session_state.sample_input = "Create a simple dataset of 5 random numbers in a file called 'numbers.txt', then read it back and calculate the average, min, max, and standard deviation."
                    st.rerun()
            with col2:
                if st.button("🔍 Research Assistant", key="research_btn", use_container_width=True):
                    st.session_state.sample_input = "Tell me 2-3 interesting facts about artificial intelligence. Keep your response concise and summarize your findings in a file called 'ai_facts.txt'."
                    st.rerun()
                if st.button("📊 Web Data", key="web_data_btn", use_container_width=True):
                    st.session_state.sample_input = "What is the current weather in San Francisco? Provide a brief summary."
                    st.rerun()
        
        # Fun Demos tab
        with tabs[3]:
            st.markdown("##### Fun demonstrations of capabilities")
            col1, col2 = st.columns(2)
            with col1:
                if st.button("🎨 ASCII Art", key="ascii_btn", use_container_width=True):
                    st.session_state.sample_input = "Create a simple ASCII art of a cat and save it to a file called 'ascii_cat.txt'."
                    st.rerun()
                if st.button("🎮 Text Adventure", key="adventure_btn", use_container_width=True):
                    st.session_state.sample_input = "Let's play a short text adventure game. I'm in a mysterious forest. What do I see around me? Give me 3 options for what to do next."
                    st.rerun()
            with col2:
                if st.button("🎲 Random Challenge", key="random_btn", use_container_width=True):
                    st.session_state.sample_input = "Generate a random coding challenge for me, then provide a solution in Python and save it to a file called 'challenge_solution.py'."
                    st.rerun()
                if st.button("🧩 Puzzle", key="puzzle_btn", use_container_width=True):
                    st.session_state.sample_input = "Create a logic puzzle for me to solve. After I solve it or give up, create a file with the puzzle and solution."
                    st.rerun()

    # --- Chat Area ---
    # Draw existing messages (no welcome message)
    messages: list[ChatMessage] = st.session_state.messages

    async def amessage_iter() -> AsyncGenerator[ChatMessage, None]:
        for m in messages:
            yield m

    await draw_messages(amessage_iter())

    # ---- Retrieve user input ----
    # Use sample query if set; otherwise use manual input.
    if st.session_state.get("sample_input"):
        user_input = st.session_state.sample_input
        st.session_state.sample_input = ""  # Clear after reading.
    else:
        user_input = st.chat_input()

    # Generate new message if the user provided input
    if user_input:
        messages.append(ChatMessage(type="human", content=user_input))
        st.chat_message("human").write(user_input)
        try:
            # Store the current model in session state for display with AI messages
            st.session_state.current_model = model
            
            if use_streaming:
                stream = agent_client.astream(
                    message=user_input,
                    model=model,
                    thread_id=st.session_state.thread_id,
                )
                await draw_messages(stream, is_new=True)
            else:
                response = await agent_client.ainvoke(
                    message=user_input,
                    model=model,
                    thread_id=st.session_state.thread_id,
                )
                messages.append(response)
                st.chat_message("ai").write(response.content)
            st.rerun()  # Clear stale containers
        except AgentClientError as e:
            st.error(f"Error generating response: {e}")
            st.stop()

    # If messages have been generated, show feedback widget
    if len(messages) > 0 and st.session_state.last_message:
        with st.session_state.last_message:
            await handle_feedback()

async def draw_messages(
    messages_agen: AsyncGenerator[ChatMessage | str, None],
    is_new: bool = False,
) -> None:
    last_message_type = None
    st.session_state.last_message = None
    streaming_content = ""
    streaming_placeholder = None
    # Track whether a final AI message was already appended
    final_message_appended = False
    # Keep track of seen tool calls and responses for proper history building
    tool_calls_seen = []
    tool_responses_seen = []
    
    while msg := await anext(messages_agen, None):
        if isinstance(msg, str):
            if not streaming_placeholder:
                if last_message_type != "ai":
                    last_message_type = "ai"
                    current_model = st.session_state.get("current_model", "AI")
                    avatar_img = model_avatar(current_model)
                    st.session_state.last_message = st.chat_message(
                        "ai",
                        avatar=avatar_img
                    )
                    # Show the model name visibly under the avatar
                    with st.session_state.last_message:
                        st.caption(current_model)
                with st.session_state.last_message:
                    streaming_placeholder = st.empty()
            streaming_content += msg
            streaming_placeholder.write(streaming_content)
            continue
            
        if not isinstance(msg, ChatMessage):
            st.error(f"Unexpected message type: {type(msg)}")
            st.write(msg)
            st.stop()
            
        match msg.type:
            case "human":
                last_message_type = "human"
                st.chat_message("human").write(msg.content)
                
            case "ai":
                # Add message to history if it's new
                if is_new and (msg.content or msg.tool_calls):
                    # Only append if it's not a duplicate of what we already have
                    duplicate = False
                    for existing in st.session_state.messages:
                        if (existing.type == "ai" and 
                            ((msg.content and existing.content == msg.content) or 
                             (msg.tool_calls and existing.tool_calls == msg.tool_calls))):
                            duplicate = True
                            break
                    
                    if not duplicate:
                        # Mark that we've appended an AI message
                        st.session_state.messages.append(msg)
                        if msg.content:
                            final_message_appended = True

                if last_message_type != "ai":
                    last_message_type = "ai"
                    model_name = getattr(msg, "model", None) or st.session_state.get("current_model", "AI")
                    avatar_img = model_avatar(model_name)
                    st.session_state.last_message = st.chat_message(
                        "ai",
                        avatar=avatar_img
                    )
                    # Show the model name visibly under the avatar
                    with st.session_state.last_message:
                        st.caption(model_name)

                with st.session_state.last_message:
                    if msg.content:
                        if streaming_placeholder:
                            # If the streaming content exactly matches the message content,
                            # don't write it again to avoid flashing
                            if streaming_content.strip() != msg.content.strip():
                                streaming_placeholder.write(msg.content)
                            streaming_content = ""
                            streaming_placeholder = None
                        else:
                            st.write(msg.content)

                    if msg.tool_calls:
                        # Save tool calls we've seen to track them
                        for tool_call in msg.tool_calls:
                            if tool_call not in tool_calls_seen:
                                tool_calls_seen.append(tool_call)

                        call_results = {}
                        for tool_call in msg.tool_calls:
                            status = st.status(
                                f"Tool Call: {tool_call['name']}",
                                state="running" if is_new else "complete",
                            )
                            call_results[tool_call["id"]] = status
                            status.write("Input:")
                            status.write(tool_call["args"])

                        for _ in range(len(call_results)):
                            try:
                                tool_result: ChatMessage = await anext(messages_agen)
                                if tool_result.type != "tool":
                                    st.error(f"Unexpected ChatMessage type: {tool_result.type}")
                                    st.write(tool_result)
                                    st.stop()

                                # Track tool responses for history
                                if tool_result not in tool_responses_seen:
                                    tool_responses_seen.append(tool_result)

                                # Add tool response to history if it's new
                                if is_new:
                                    # Only append if it's not a duplicate
                                    duplicate = False
                                    for existing in st.session_state.messages:
                                        if (existing.type == "tool" and 
                                            existing.tool_call_id == tool_result.tool_call_id and
                                            existing.content == tool_result.content):
                                            duplicate = True
                                            break

                                    if not duplicate:
                                        st.session_state.messages.append(tool_result)

                                status = call_results.get(tool_result.tool_call_id)
                                # If we can't find the matching tool call, use the first available status
                                if not status and call_results:
                                    status = list(call_results.values())[0]
                                    st.warning(f"Tool result with id {tool_result.tool_call_id} couldn't be matched to a tool call")

                                if status:
                                    status.write("Output:")
                                    status.write(tool_result.content)
                                    status.update(state="complete")
                                else:
                                    st.error(f"Couldn't find a matching tool call for result: {tool_result.tool_call_id}")
                                    st.write(tool_result.content)
                            except StopAsyncIteration:
                                # If we run out of messages but still expecting tool responses,
                                # that's an error in the stream
                                st.error("Stream ended unexpectedly while waiting for tool responses")
                                break
                    
            case "tool":
                # We handle tool messages within the AI message processing
                # But sometimes tool messages might arrive separately, so handle that case:
                if is_new:
                    # Only append if it's not already in our tool_responses_seen list
                    if msg not in tool_responses_seen:
                        tool_responses_seen.append(msg)
                        
                        # And check it's not a duplicate in history
                        duplicate = False
                        for existing in st.session_state.messages:
                            if (existing.type == "tool" and 
                                existing.tool_call_id == msg.tool_call_id and
                                existing.content == msg.content):
                                duplicate = True
                                break
                        
                        if not duplicate:
                            st.session_state.messages.append(msg)
                            
                # If we get a tool message without seeing the corresponding AI message first,
                # we need to display it standalone
                if last_message_type != "ai":
                    st.error(f"Received tool response without a preceding AI message: {msg.tool_call_id}")
                    st.write(msg.content)
                    
            case "custom":
                try:
                    task_data: TaskData = TaskData.model_validate(msg.custom_data)
                except ValidationError:
                    st.error("Unexpected CustomData message received from agent")
                    st.write(msg.custom_data)
                    st.stop()
                if is_new:
                    # Check for duplicates
                    duplicate = False
                    for existing in st.session_state.messages:
                        if (existing.type == "custom" and 
                            existing.custom_data == msg.custom_data):
                            duplicate = True
                            break
                            
                    if not duplicate:
                        st.session_state.messages.append(msg)
                        
                if last_message_type != "task":
                    last_message_type = "task"
                    st.session_state.last_message = st.chat_message(
                        name="task", avatar=":material/manufacturing:"
                    )
                    with st.session_state.last_message:
                        status = TaskDataStatus()
                status.add_and_draw_task_data(task_data)
                
            case _:
                st.error(f"Unexpected ChatMessage type: {msg.type}")
                st.write(msg)
                st.stop()
    
    # If we've been streaming tokens but never got a final AI message with content,
    # create one from the streamed content when we see [DONE]
    if is_new and streaming_content and not final_message_appended:
        final_msg = ChatMessage(type="ai", content=streaming_content)
        
        # Only add if it's not a duplicate
        duplicate = False
        for existing in st.session_state.messages:
            if (existing.type == "ai" and existing.content == streaming_content):
                duplicate = True
                break
                
        if not duplicate:
            st.session_state.messages.append(final_msg)
            
        if streaming_placeholder:
            streaming_placeholder.write(streaming_content)


async def handle_feedback() -> None:
    if "last_feedback" not in st.session_state:
        st.session_state.last_feedback = (None, None)
    latest_run_id = st.session_state.messages[-1].run_id
    feedback = st.feedback("stars", key=latest_run_id)
    if feedback is not None and (latest_run_id, feedback) != st.session_state.last_feedback:
        normalized_score = (feedback + 1) / 5.0
        agent_client: AgentClient = st.session_state.agent_client
        try:
            await agent_client.acreate_feedback(
                run_id=latest_run_id,
                key="human-feedback-stars",
                score=normalized_score,
                kwargs={"comment": "In-line human feedback"},
            )
        except AgentClientError as e:
            st.error(f"Error recording feedback: {e}")
            st.stop()
        st.session_state.last_feedback = (latest_run_id, feedback)
        st.toast("Feedback recorded", icon=":material/reviews:")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError as e:
        if "Event loop is closed" not in str(e):
            raise