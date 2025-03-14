# math_server.py
from typing import List
from mcp.server.fastmcp import FastMCP
import numexpr as ne

mcp = FastMCP("Math")

@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers"""
    return a + b

@mcp.tool()
def multiply(a: int, b: int) -> int:
    """Multiply two numbers"""
    return a * b

@mcp.tool()
async def calculate(expression: str) -> float:
    """Calculate result of a mathematical expression."""
    return float(ne.evaluate(expression))

if __name__ == "__main__":
    mcp.run(transport="stdio")