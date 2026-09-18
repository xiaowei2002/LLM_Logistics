"""Sandboxed execution of user-supplied pandas code."""

from __future__ import annotations

from typing import Annotated

import numpy as np
import pandas as pd
from fastmcp import FastMCP
from pydantic import Field

from ..loader import DataFrameError, load_dataframe, to_jsonable
from ..utils import ensure_safe_code

# A deliberately small set of builtins. Because ``__builtins__`` is replaced, the
# executed code cannot import modules or reach the filesystem even if the regex
# filter were bypassed — defense in depth.
SAFE_BUILTINS: dict[str, object] = {
    name: __builtins__[name] if isinstance(__builtins__, dict) else getattr(__builtins__, name)
    for name in (
        "abs", "all", "any", "bool", "dict", "enumerate", "filter", "float",
        "int", "len", "list", "max", "min", "range", "round", "set", "sorted",
        "str", "sum", "tuple", "zip",
    )
}


def register(mcp: FastMCP) -> None:
    @mcp.tool
    def run_pandas_code(
        code: Annotated[
            str,
            Field(
                description=(
                    "Python/pandas code to run. `pd` and `np` are available; assign "
                    "the final output to a variable named `result`."
                )
            ),
        ],
        file_path: Annotated[
            str | None,
            Field(
                description=(
                    "Optional CSV/Excel file to preload as a DataFrame named `df` "
                    "before running the code."
                )
            ),
        ] = None,
    ) -> dict:
        """Execute pandas code in a restricted sandbox and return ``result``.

        ``pd`` (pandas) and ``np`` (numpy) are in scope; when ``file_path`` is
        given, the file is loaded into a DataFrame named ``df``. The code must
        assign its output to ``result``. For safety, filesystem/process/interpreter
        access (``import``, ``open``, ``exec``, ``eval``, ``os``/``sys``/…) is
        rejected. DataFrame/Series results are returned as records.
        """
        ensure_safe_code(code)

        namespace: dict[str, object] = {"pd": pd, "np": np, "__builtins__": SAFE_BUILTINS}
        if file_path:
            namespace["df"] = load_dataframe(file_path)

        try:
            exec(code, namespace)  # noqa: S102 - sandboxed by SAFE_BUILTINS + filter
        except DataFrameError:
            raise
        except Exception as exc:  # surface any runtime error from the user's code
            raise DataFrameError(f"Error while executing code: {exc}") from exc

        if "result" not in namespace:
            raise DataFrameError("The code ran but never assigned a `result` variable.")

        return {"result": to_jsonable(namespace["result"])}
