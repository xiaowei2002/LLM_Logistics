"""Interactive chart generation (Chart.js) from tabular series data."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field

from .. import charting


def register(mcp: FastMCP) -> None:
    @mcp.tool
    def generate_chartjs(
        data: Annotated[
            dict[str, Any],
            Field(
                description=(
                    "Chart data as {'labels': [...], 'series': [{'name': str, "
                    "'values': [...]}]}. Every series must match the label count."
                )
            ),
        ],
        chart_type: Literal["bar", "line", "pie"] = "bar",
        title: Annotated[str, Field(description="Chart title.")] = "Chart",
    ) -> dict:
        """Generate an interactive Chart.js HTML file from series data.

        Supports ``bar``, ``line`` and ``pie`` charts. The self-contained HTML is
        written to the charts directory (``MCP_CHARTS_DIR``, default ``./charts``)
        and the tool returns its path. Pie charts use the first series only. Feed
        it aggregated data — e.g. the output of a ``run_pandas_code`` group-by.
        """
        return charting.generate(data, chart_type, title)
