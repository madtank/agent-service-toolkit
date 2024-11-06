import os
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_aws import ChatBedrock  # Correct import
from langchain_ollama import ChatOllama  # Import ChatOllama

models: dict[str, BaseChatModel] = {}

# Add AWS Bedrock models unconditionally
models["bedrock-sonnet"] = ChatBedrock(
    model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
    model_kwargs=dict(temperature=0.7),
    max_tokens=4096
)

models["bedrock-haiku"] = ChatBedrock(
    model_id="anthropic.claude-3-5-haiku-20241022-v1:0",
    model_kwargs=dict(temperature=0.7),
    max_tokens=4096
)

models["ollama"] = ChatOllama(
    model="llama3.2",
    temperature=0.7,
    streaming=True  # Enable streaming
)

# Adjust the exit condition if necessary
if not models:
    print("No LLM available. Please configure a model in 'src/agents/models.py'.")
    exit(1)