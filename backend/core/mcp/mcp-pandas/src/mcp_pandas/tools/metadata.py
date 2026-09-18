"""Dataset profiling: read the structure and quality of a CSV/Excel file."""

from __future__ import annotations

from typing import Annotated

import pandas as pd
from fastmcp import FastMCP
from pydantic import Field

from ..loader import (
    EXCEL_SUFFIXES,
    SAMPLE_ROWS,
    human_size,
    load_dataframe,
    resolve_path,
    to_jsonable,
)


def _profile_column(series: pd.Series) -> dict:
    """Build a per-column profile (dtype, nulls, cardinality, samples, stats)."""
    non_null = int(series.count())
    nulls = int(series.isnull().sum())
    total = non_null + nulls
    profile: dict = {
        "name": str(series.name),
        "dtype": str(series.dtype),
        "non_null": non_null,
        "nulls": nulls,
        "null_pct": round(nulls / total * 100, 1) if total else 0.0,
        "unique": int(series.nunique(dropna=True)),
        "sample_values": [to_jsonable(v) for v in series.dropna().unique()[:5]],
    }
    if pd.api.types.is_numeric_dtype(series) and non_null:
        profile["min"] = to_jsonable(series.min())
        profile["max"] = to_jsonable(series.max())
        profile["mean"] = round(float(series.mean()), 4)
    return profile


def _warnings(frame: pd.DataFrame, columns: list[dict]) -> list[str]:
    """Flag common data-quality problems for the model to consider."""
    warnings: list[str] = []
    for col in columns:
        if col["null_pct"] >= 50:
            warnings.append(f"Column '{col['name']}' is {col['null_pct']}% null.")
        if col["unique"] <= 1 and col["non_null"]:
            warnings.append(f"Column '{col['name']}' has a single constant value.")
        if col["non_null"] and col["unique"] == col["non_null"] and col["non_null"] > 1:
            warnings.append(f"Column '{col['name']}' looks like a unique identifier.")
    if frame.columns.duplicated().any():
        warnings.append("The file has duplicated column names.")
    return warnings


def _suggested_operations(columns: list[dict]) -> list[str]:
    """Propose next pandas steps based on the column types present."""
    numeric = [c["name"] for c in columns if "mean" in c]
    categorical = [c["name"] for c in columns if "mean" not in c]
    suggestions: list[str] = []
    if numeric:
        suggestions.append(f"result = df[{numeric!r}].describe()")
    if len(numeric) >= 2:
        suggestions.append(f"result = df[{numeric!r}].corr()")
    if categorical and numeric:
        suggestions.append(
            f"result = df.groupby('{categorical[0]}')['{numeric[0]}'].sum()"
        )
    if categorical:
        suggestions.append(f"result = df['{categorical[0]}'].value_counts()")
    return suggestions


def register(mcp: FastMCP) -> None:
    @mcp.tool
    def read_metadata(
        file_path: Annotated[
            str,
            Field(description="Path to a CSV/TSV or Excel (.xlsx/.xls) file."),
        ],
    ) -> dict:
        """Profile a data file: structure, types, quality warnings and next steps.

        Reads only the first rows for efficiency and returns file info, a
        per-column profile (dtype, null counts, cardinality, sample values and
        numeric min/max/mean), data-quality warnings, and suggested pandas
        operations to run next with ``run_pandas_code``. This is the recommended
        first call when exploring an unknown dataset.
        """
        path = resolve_path(file_path)
        frame = load_dataframe(file_path, nrows=SAMPLE_ROWS)
        columns = [_profile_column(frame[col]) for col in frame.columns]
        return {
            "file": {
                "path": str(path),
                "name": path.name,
                "type": "excel" if path.suffix.lower() in EXCEL_SUFFIXES else "csv",
                "size": human_size(path.stat().st_size),
            },
            "sampled_rows": int(len(frame)),
            "column_count": int(frame.shape[1]),
            "note": f"Profiled the first {SAMPLE_ROWS} rows; the file may contain more.",
            "columns": columns,
            "warnings": _warnings(frame, columns),
            "suggested_operations": _suggested_operations(columns),
        }
