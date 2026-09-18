"""Entry point for the pandas MCP server.

Creates the FastMCP instance, registers tools and prompts, and exposes ``main()``.

Transport is configurable via environment variables:

- ``MCP_TRANSPORT``: ``stdio`` (default) or ``http``.
- ``MCP_HOST``: listen host for the network transport (default ``0.0.0.0``).
- ``MCP_PORT``: listen port for the network transport (default ``8080``).
"""

from __future__ import annotations

import os

from fastmcp import FastMCP

from . import prompts
from .tools import register_all

mcp = FastMCP(
    name="mcp-pandas",
    version="1.0.0",
    instructions=(
        "Pandas-powered data analysis server. Provides tools to profile a CSV/Excel "
        "file (read_metadata), inspect a column's full value distribution "
        "(interpret_column_data), run sandboxed pandas code (run_pandas_code) and "
        "render interactive Chart.js charts (generate_chartjs). "
        "Start with read_metadata, then drive the analysis with run_pandas_code."
    ),
)

register_all(mcp)
prompts.register(mcp)


def main() -> None:
    """Console entry point. Transport is selected via environment variables.

    MCP_TRANSPORT = stdio (default) | http
    MCP_HOST (default 0.0.0.0), MCP_PORT (default 8080) for http.
    """
    transport = os.environ.get("MCP_TRANSPORT", "stdio").lower()

    if transport == "http":
        mcp.run(
            show_banner=False,
            transport="http",
            host=os.environ.get("MCP_HOST", "0.0.0.0"),
            port=int(os.environ.get("MCP_PORT", "8080")),
        )
    else:
        mcp.run()


if __name__ == "__main__":
    main()
