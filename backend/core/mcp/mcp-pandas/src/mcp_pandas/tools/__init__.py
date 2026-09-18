"""MCP tools for pandas-based data analysis.

Each submodule exposes ``register(mcp)`` which registers its tools on the
FastMCP instance.
"""

from fastmcp import FastMCP

from . import charts, columns, execution, metadata


def register_all(mcp: FastMCP) -> None:
    """Register every pandas tool on the server."""
    for module in (metadata, columns, execution, charts):
        module.register(mcp)
