from abc import ABC
from typing import Any, Dict, Optional
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from core.settings import settings
import asyncio

class MCPClient(ABC):
    """Base client for all MCP servers"""
    
    def __init__(self):
        if not settings.MCP_ENABLED:
            raise ValueError("MCP is not enabled")
        
        self.command: str
        self.args: list[str]
        self._process = None

    async def initialize(self) -> None:
        """Initialize the MCP client and create the subprocess"""
        if not self._process:
            self._process = await self._create_process()

    async def _create_process(self) -> asyncio.subprocess.Process:
        """Create and return a new subprocess"""
        if not self.command:
            raise ValueError("Command not set")
        return await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
    
    async def execute(self, tool: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        """Execute an MCP tool"""
        async with stdio_client(StdioServerParameters(
            command=self.command,
            args=self.args
        )) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(tool, arguments or {})