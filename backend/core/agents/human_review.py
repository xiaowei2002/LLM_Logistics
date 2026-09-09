from __future__ import annotations

import re
from typing import Any


# ==========================================================
# 1. 时间表达式
# ==========================================================

# 支持：
# 2026-01-30
# 2026/01/30
# 2026年1月30日
_DATE_PATTERN = re.compile(
    r"(?<!\d)"
    r"(?:"
    r"\d{4}[-/]\d{1,2}[-/]\d{1,2}"
    r"|"
    r"\d{4}年\d{1,2}月\d{1,2}日?"
    r")"
)


# 支持：
# 2026-01-30 12:00:00
# 2026-01-30 12:00
# 2026/01/30 12:00
# 2026年1月30日12点
# 2026年1月30日12时
# 2026年1月30日12:00
_DATETIME_PATTERNS = [
    re.compile(
        r"(?<!\d)"
        r"\d{4}[-/]\d{1,2}[-/]\d{1,2}"
        r"[ T]"
        r"\d{1,2}:\d{1,2}"
        r"(?::\d{1,2})?"
    ),
    re.compile(
        r"(?<!\d)"
        r"\d{4}年\d{1,2}月\d{1,2}日?"
        r"\s*"
        r"\d{1,2}"
        r"(?:"
        r"点"
        r"|时"
        r"|:\d{1,2}(?::\d{1,2})?"
        r")"
    ),
]


def _contains_datetime(text: str) -> bool:
    return any(
        pattern.search(text)
        for pattern in _DATETIME_PATTERNS
    )


# ==========================================================
# 2. Human Review 输入检查
# ==========================================================

def inspect_forecast_query(
    query: str,
) -> dict[str, Any]:
    """
    检查需求预测请求是否包含执行预测所需的基本信息。

    当前检查：
    1. query 是否为空；
    2. 是否给出日期；
    3. 是否给出明确预测基准时间。

    不对用户输入进行猜测或自动补全。
    """

    text = query.strip()

    if not text:
        return {
            "ready": False,
            "reason": "empty_query",
            "question": "请输入需要执行的需求预测任务。",
        }

    # 完整日期 + 时间已经给出
    if _contains_datetime(text):
        return {
            "ready": True,
            "reason": None,
            "question": None,
        }

    # 有日期，但没有具体时刻
    if _DATE_PATTERN.search(text):
        return {
            "ready": False,
            "reason": "missing_forecast_time",
            "question": (
                "已识别到预测日期，但缺少具体预测基准时刻。"
                "请补充时间，例如：12:00:00。"
            ),
        }

    # 日期和时间都缺少
    return {
        "ready": False,
        "reason": "missing_forecast_timestamp",
        "question": (
            "当前请求缺少明确的预测基准时间。"
            "请提供预测时间，例如："
            "2026-01-30 12:00:00。"
        ),
    }