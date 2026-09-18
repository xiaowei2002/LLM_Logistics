"""Shared data-loading infrastructure for the pandas tools.

Centralizes file resolution, size/type validation, delimited/Excel reading,
memory optimization and result serialization, so each MCP tool stays lean and
focused on its own analysis concern.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

# --- limits and locations ------------------------------------------------- #
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB, mirroring pandas-mcp-server.
SAMPLE_ROWS = 100                  # Rows scanned when profiling metadata.
CHARTS_DIR = Path(os.environ.get("MCP_CHARTS_DIR", "charts")).expanduser()

CSV_SUFFIXES = {".csv", ".tsv", ".txt"}
EXCEL_SUFFIXES = {".xlsx", ".xls"}
SUPPORTED_SUFFIXES = CSV_SUFFIXES | EXCEL_SUFFIXES


class DataFrameError(Exception):
    """Friendly business error, safe to return to the model/user.

    Unlike raw pandas/OS exceptions, this carries a human-readable message ready
    to be relayed by an MCP tool.
    """


def human_size(num_bytes: int) -> str:
    """Render a byte count as a compact human-readable string."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GB"


def resolve_path(file_path: str) -> Path:
    """Validate a file path and enforce the type/size limits before reading."""
    if not file_path or not str(file_path).strip():
        raise DataFrameError("Provide a file path.")

    path = Path(str(file_path)).expanduser()
    if not path.exists():
        raise DataFrameError(f"File not found: {file_path}")
    if not path.is_file():
        raise DataFrameError(f"Not a file: {file_path}")

    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        supported = ", ".join(sorted(SUPPORTED_SUFFIXES))
        raise DataFrameError(f"Unsupported file type '{suffix}'. Supported: {supported}.")

    size = path.stat().st_size
    if size > MAX_FILE_SIZE:
        raise DataFrameError(
            f"File too large ({human_size(size)}); the limit is {human_size(MAX_FILE_SIZE)}."
        )
    return path


def optimize(frame: pd.DataFrame) -> pd.DataFrame:
    """Downcast numeric columns and categorize low-cardinality text in place."""
    rows = max(len(frame), 1)
    for col in frame.select_dtypes(include=["float64"]).columns:
        frame[col] = pd.to_numeric(frame[col], downcast="float")
    for col in frame.select_dtypes(include=["int64"]).columns:
        frame[col] = pd.to_numeric(frame[col], downcast="integer")
    for col in frame.columns:
        if frame[col].dtype == object and frame[col].nunique(dropna=True) / rows < 0.5:
            frame[col] = frame[col].astype("category")
    return frame


def load_dataframe(
    file_path: str,
    *,
    nrows: int | None = None,
    sheet_name: str | int = 0,
) -> pd.DataFrame:
    """Read a CSV/TSV/Excel file into an optimized DataFrame.

    ``nrows`` caps the number of rows read (used for metadata sampling); the
    delimiter of text files is sniffed automatically.
    """
    path = resolve_path(file_path)
    suffix = path.suffix.lower()
    try:
        if suffix in EXCEL_SUFFIXES:
            frame = pd.read_excel(path, nrows=nrows, sheet_name=sheet_name)
        else:
            frame = pd.read_csv(path, nrows=nrows, sep=None, engine="python")
    except DataFrameError:
        raise
    except Exception as exc:  # pandas surfaces many read/parse error types
        raise DataFrameError(f"Failed to read '{path.name}': {exc}") from exc
    return optimize(frame)


def to_jsonable(obj: object) -> object:
    """Convert a pandas/numpy value into a JSON-native structure for MCP."""
    if isinstance(obj, pd.DataFrame):
        return {
            "kind": "dataframe",
            "shape": list(obj.shape),
            "records": json.loads(obj.to_json(orient="records", date_format="iso")),
        }
    if isinstance(obj, pd.Series):
        return {
            "kind": "series",
            "length": int(obj.size),
            "values": json.loads(obj.to_json(date_format="iso")),
        }
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return str(obj)
