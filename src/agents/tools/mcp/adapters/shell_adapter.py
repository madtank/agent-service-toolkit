from typing import Any
import logging
from ..client import MCPClient

logger = logging.getLogger(__name__)

class ShellServer(MCPClient):
    """Shell command execution server with MCP protocol support"""

    def __init__(self):
        # Set command and args before super init
        self.command = "mcp-shell"
        self.args = []
        super().__init__()  # Now call super init after setting required attributes
            
    async def execute_command(self, command: str) -> Any:
        """Execute a shell command and return the output"""
        try:
            if not self._process:
                await self.initialize()
            result = await self.execute("run_command", {"command": command})
            return result.content[0].text if result.content else "Command executed successfully"
        except Exception as e:
            logger.error(f"Shell command execution failed: {str(e)}")
            return f"Error executing shell command: {str(e)}"
