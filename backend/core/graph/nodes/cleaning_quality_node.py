"""
Cleaning Quality Gate 节点。

负责对 CleaningAgent 实际落盘的数据文件进行
确定性的程序化质量验证。

该节点不调用 LLM。

特殊安全规则：
当自动删行比例超过 10% 时，
不直接判普通失败，而是进入 Human-in-the-loop。

只有用户或业务方明确确认后，
才允许当前清洗结果继续进入 Report。
"""

from __future__ import annotations

from core.graph.cleaning_quality import (
    validate_cleaned_file,
)
from core.graph.state import (
    LogisticsAgentState,
)


LARGE_DELETION_ERROR_PREFIX = (
    "清洗过程自动删除数据比例过高"
)


def _remove_large_deletion_error(
    quality: dict,
) -> list[str]:
    """
    人工已经确认大比例删行后，
    从 errors 中移除“大比例删除”这一项。

    其他错误仍然必须保留，
    不能因为用户确认删除就绕过其他质量问题。
    """

    errors = quality.get(
        "errors",
        [],
    )

    remaining_errors = [
        str(error)
        for error in errors
        if not str(error).startswith(
            LARGE_DELETION_ERROR_PREFIX
        )
    ]

    quality["errors"] = (
        remaining_errors
    )

    return remaining_errors


def cleaning_quality_node(
    state: LogisticsAgentState,
) -> LogisticsAgentState:
    """
    对 CleaningAgent 的实际输出执行确定性检查。

    正常情况：
        Gate 通过
        → status = running
        → Report

    大比例删行：
        未人工确认
        → status = needs_human_input

        已人工确认
        → 重新验证
        → 若无其他错误则继续 Report

    其他质量错误：
        → status = failed
    """

    source_file_path = state.get(
        "data_file_path"
    )

    cleaned_file_path = state.get(
        "cleaned_file_path"
    )

    approved = bool(
        state.get(
            "cleaning_large_deletion_approved",
            False,
        )
    )

    # ==================================================
    # 1. 执行确定性质量检查
    # ==================================================

    quality = validate_cleaned_file(
        cleaned_file_path,
        source_file_path=(
            source_file_path
        ),
    )

    large_deletion_detected = bool(
        quality.get(
            "large_deletion_detected",
            False,
        )
    )

    # ==================================================
    # 2. 检测到大比例删行，但尚未人工确认
    # ==================================================

    if (
        large_deletion_detected
        and not approved
    ):
        source_row_count = quality.get(
            "source_row_count"
        )

        cleaned_row_count = quality.get(
            "cleaned_row_count"
        )

        removed_row_count = quality.get(
            "removed_row_count"
        )

        removal_rate = quality.get(
            "removal_rate"
        )

        if isinstance(
            removal_rate,
            (int, float),
        ):
            removal_rate_text = (
                f"{removal_rate:.2%}"
            )
        else:
            removal_rate_text = "未知"

        human_question = (
            "数据清洗触发大比例删行安全规则："
            f"原始数据 {source_row_count} 行，"
            f"清洗后 {cleaned_row_count} 行，"
            f"共删除 {removed_row_count} 行，"
            f"删除比例为 {removal_rate_text}，"
            "超过自动允许的 10% 阈值。"
            "是否确认保留当前清洗结果，"
            "并继续生成下游报告？"
        )

        return {
            "current_agent": (
                "cleaning_quality"
            ),
            "cleaning_quality": quality,
            "cleaning_quality_status": (
                "needs_human_input"
            ),
            "cleaning_quality_error": None,

            "needs_human_input": True,
            "human_reason": (
                "large_deletion"
            ),
            "human_question": (
                human_question
            ),

            "status": (
                "needs_human_input"
            ),

            "execution_trace": [
                "cleaning_quality"
            ],
        }

    # ==================================================
    # 3. 大比例删行已经人工确认
    # ==================================================

    if (
        large_deletion_detected
        and approved
    ):
        remaining_errors = (
            _remove_large_deletion_error(
                quality
            )
        )

        quality[
            "large_deletion_approved"
        ] = True

        quality["warnings"].append(
            (
                "自动删行比例超过 10%，"
                "但已经获得用户或业务方明确确认，"
                "因此允许当前清洗结果继续进入下游流程。"
            )
        )

        # 人工确认只能解除“大比例删除”这一项。
        # 如果还有其他真正错误，仍然失败。
        if remaining_errors:
            quality["passed"] = False

        else:
            quality["passed"] = True

    # ==================================================
    # 4. 普通 Quality Gate 失败
    # ==================================================

    if not bool(
        quality.get("passed")
    ):
        errors = quality.get(
            "errors",
            [],
        )

        error_text = (
            "; ".join(
                str(item)
                for item in errors
            )
            if errors
            else (
                "清洗结果未通过"
                "确定性质量验证。"
            )
        )

        return {
            "current_agent": (
                "cleaning_quality"
            ),
            "cleaning_quality": quality,
            "cleaning_quality_status": (
                "error"
            ),
            "cleaning_quality_error": (
                error_text
            ),

            "needs_human_input": False,
            "human_reason": None,
            "human_question": None,

            "status": "failed",

            "execution_trace": [
                "cleaning_quality"
            ],
        }

    # ==================================================
    # 5. Quality Gate 通过
    # ==================================================

    return {
        "current_agent": (
            "cleaning_quality"
        ),
        "cleaning_quality": quality,
        "cleaning_quality_status": (
            "success"
        ),
        "cleaning_quality_error": None,

        # 如果这是人工确认后的第二次运行，
        # 这里需要把之前暂停状态清除。
        "needs_human_input": False,
        "human_reason": None,
        "human_question": None,

        "status": "running",

        "execution_trace": [
            "cleaning_quality"
        ],
    }