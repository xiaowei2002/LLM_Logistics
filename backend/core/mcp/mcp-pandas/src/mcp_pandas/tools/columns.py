"""Full value-distribution analysis for one or more columns of a file."""

from __future__ import annotations

from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from ..loader import to_jsonable
from ..utils import validate_columns

# Cap the number of distinct values returned per column so a high-cardinality
# column can't produce an unbounded payload.
MAX_VALUES = 200


def register(mcp: FastMCP) -> None:
    @mcp.tool
    def interpret_column_data(
        file_path: Annotated[
            str,
            Field(description="Path to a CSV/TSV or Excel (.xlsx/.xls) file."),
        ],
        column_names: Annotated[
            list[str],
            Field(min_length=1, description="Columns to analyze."),
        ],
        sheet_name: Annotated[
            str | None,
            Field(description="Excel sheet name; ignored for CSV files."),
        ] = None,
    ) -> dict:
        """Return the complete value distribution of one or more columns.

        For each requested column, reports dtype, total/null/unique counts and the
        value frequencies (sorted most-common first). Unlike ``read_metadata``,
        this scans the whole file rather than a sample, so it is ideal for
        understanding categorical columns. Frequencies are capped at 200 distinct
        values per column.
        """
        # Import here to keep the module import graph flat and avoid cycles.
        from ..loader import load_dataframe

        frame = load_dataframe(file_path, sheet_name=sheet_name or 0)
        validate_columns(column_names, list(frame.columns))

        result: dict = {}
        for name in column_names:
            series = frame[name]
            counts = series.value_counts(dropna=False)
            truncated = len(counts) > MAX_VALUES
            distribution = [
                {"value": to_jsonable(value), "count": int(count)}
                for value, count in counts.head(MAX_VALUES).items()
            ]
            result[name] = {
                "dtype": str(series.dtype),
                "total": int(series.size),
                "nulls": int(series.isnull().sum()),
                "unique": int(series.nunique(dropna=True)),
                "truncated": truncated,
                "distribution": distribution,
            }
        return {"file_path": file_path, "columns": result}
