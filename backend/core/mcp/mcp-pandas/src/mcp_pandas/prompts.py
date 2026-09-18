"""Guided prompts (workflows) for the MCP server."""

from __future__ import annotations

from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field


def register(mcp: FastMCP) -> None:
    @mcp.prompt
    def explore_dataset(
        file_path: Annotated[str, Field(description="Path to the CSV/Excel file to explore.")],
    ) -> str:
        """Guide a structured exploration of an unknown data file."""
        return (
            f"Explore the dataset at '{file_path}'.\n\n"
            "1. Call `read_metadata` to inspect its structure, types and quality warnings.\n"
            "2. For any categorical column of interest, call `interpret_column_data` to see "
            "the full value distribution.\n"
            "3. Use `run_pandas_code` (with `file_path`) to compute the statistics that answer "
            "the user's question, assigning the output to `result`.\n"
            "4. When a comparison or trend helps, call `generate_chartjs` with the aggregated data.\n"
            "Base every statement on the tool output; do not invent values."
        )

    @mcp.prompt
    def visualize_column(
        file_path: Annotated[str, Field(description="Path to the CSV/Excel file.")],
        column: Annotated[str, Field(description="Column to summarize and chart.")],
    ) -> str:
        """Summarize a single column and turn its distribution into a chart."""
        return (
            f"Summarize and visualize the column '{column}' from '{file_path}'.\n\n"
            f"1. Call `interpret_column_data` for '{column}' to get its value distribution.\n"
            "2. Take the top categories and their counts and build a `data` object shaped as "
            "{'labels': [...], 'series': [{'name': '" + column + "', 'values': [...]}]}.\n"
            "3. Call `generate_chartjs` with a `bar` (or `pie`) chart and report the file path.\n"
            "Keep labels readable and rely only on the returned data."
        )
