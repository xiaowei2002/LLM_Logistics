"""Input validation and safety helpers for the pandas tools."""

from __future__ import annotations

import re

# Patterns that must never appear in user-supplied pandas code. The goal is to
# block filesystem, process and interpreter escapes while still allowing normal
# DataFrame manipulation (mirrors pandas-mcp-server's safety filter).
FORBIDDEN_PATTERNS: tuple[str, ...] = (
    r"\bos\b",
    r"\bsys\b",
    r"\bsubprocess\b",
    r"\bshutil\b",
    r"\bsocket\b",
    r"\bimport\b",
    r"__import__",
    r"\bopen\s*\(",
    r"\bexec\s*\(",
    r"\beval\s*\(",
    r"\bcompile\s*\(",
    r"\bglobals\s*\(",
    r"\blocals\s*\(",
    r"\bgetattr\s*\(",
    r"\bsetattr\s*\(",
    r"__\w+__",
)


def ensure_safe_code(code: str) -> str:
    """Reject unsafe pandas code and require a ``result`` assignment.

    Raises ``ValueError`` (surfaced to the client) when the code is empty, uses a
    forbidden construct, or never assigns the ``result`` variable that the
    executor reads back.
    """
    if not code or not code.strip():
        raise ValueError("Provide pandas code to execute.")

    for pattern in FORBIDDEN_PATTERNS:
        if re.search(pattern, code):
            raise ValueError(
                f"Code rejected for safety: the pattern '{pattern}' is not allowed."
            )

    if not re.search(r"\bresult\b", code):
        raise ValueError(
            "Assign the final output to a variable named `result` (e.g. `result = df.describe()`)."
        )
    return code


def validate_columns(requested: list[str], available: list[str]) -> list[str]:
    """Ensure every requested column exists, raising a readable error if not."""
    if not requested:
        raise ValueError("Provide at least one column name.")
    missing = [col for col in requested if col not in available]
    if missing:
        raise ValueError(
            f"Column(s) not found: {', '.join(missing)}. Available: {', '.join(available)}."
        )
    return requested
