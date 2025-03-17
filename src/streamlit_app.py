import asyncio
import os
import urllib.parse
import uuid
from collections.abc import AsyncGenerator

import streamlit as st
from dotenv import load_dotenv
from pydantic import ValidationError
from streamlit.runtime.scriptrunner import get_script_run_ctx

from client import AgentClient, AgentClientError
from schema import ChatHistory, ChatMessage
from schema.task_data import TaskData, TaskDataStatus

APP_TITLE = "Agent Service Toolkit"
APP_ICON = "🧰"

async def main() -> None:
    st.set_page_config(
        page_title=APP_TITLE,
        page_icon=APP_ICON,
        menu_items={},
    )

    # Hide the Streamlit upper-right chrome
    st.html(
        """
        <style>
        [data-testid="stStatusWidget"] {
                visibility: hidden;
                height: 0%;
                position: fixed;
            }
        </style>
        """
    )
    if st.get_option("client.toolbarMode") != "minimal":
        st.set_option("client.toolbarMode", "minimal")
        await asyncio.sleep(0.1)
        st.rerun()

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

        # Settings popover remains
        with st.popover(":material/settings: Settings", use_container_width=True):
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

        # Share/resume chat dialog
        @st.dialog("Share/resume chat")
        def share_chat_dialog() -> None:
            session = st.runtime.get_instance()._session_mgr.list_active_sessions()[0]
            st_base_url = urllib.parse.urlunparse(
                [session.client.request.protocol, session.client.request.host, "", "", "", ""]
            )
            if not st_base_url.startswith("https") and "localhost" not in st_base_url:
                st_base_url = st_base_url.replace("http", "https")
            chat_url = f"{st_base_url}?thread_id={st.session_state.thread_id}"
            st.markdown(f"**Chat URL:**\n```text\n{chat_url}\n```")
            st.info("Copy the above URL to share or revisit this chat")

        if st.button(":material/upload: Share/resume chat", use_container_width=True):
            share_chat_dialog()

        # View source code link
        st.markdown("[View the source code](https://github.com/madtank/agent-service-toolkit/tree/main)")
    
    # --- Quick Actions on Top of the Main Page ---
    st.subheader("Quick Actions")
    col1, col2 = st.columns(2)
    
    # Only show buttons if we're not currently processing a sample input
    if not st.session_state.get("sample_input"):
        with col1:
            if st.button("🛠️ List Available Tools", key="list_tools_btn", use_container_width=True):
                st.session_state.sample_input = "What tools do you have available?"
                st.rerun()
        with col2:
            if st.button("📝 Check Previous Conversation", key="check_history_btn", use_container_width=True):
                st.session_state.sample_input = "What have we discussed before? Please check your memory but don't list your tools again."
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
                    st.session_state.last_message = st.chat_message("ai")
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
                    st.session_state.last_message = st.chat_message("ai")
                    
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