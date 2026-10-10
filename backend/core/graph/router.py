
"""
物流多智能体工作流确定性路由。

支持：
1. SQL -> Report
2. 旧版 Cleaning -> Quality Gate -> Report
3. 安全 Cleaning：
   Precheck -> Restricted Cleaning -> Quality Gate
4. 安全 Cleaning 事后 HITL 恢复：
   Resume Validation -> Quality Gate
5. Forecast -> Report

不使用 LLM Supervisor。
"""

from __future__ import annotations

from core.graph.state import LogisticsAgentState


VALID_TASK_MODES = {
    "sql_report",
    "cleaning_report",
    "cleaning_safe_report",
    "forecast_report",
}


def route_from_start(
    state: LogisticsAgentState,
) -> str:
    task_mode = str(
        state.get("task_mode", "") or ""
    ).strip()

    if task_mode == "sql_report":
        return "sql"

    if task_mode == "cleaning_safe_report":
        # 声明已经获得事后批准时，
        # 必须先进入专用恢复验证节点。
        # 即使恢复证据缺失，也不能跳回普通清洗。
        post_approved = (
            state.get(
                "cleaning_large_deletion_approved"
            ) is True
        )

        if post_approved:
            return "cleaning_resume"

        return "cleaning_precheck"

    if task_mode == "cleaning_report":
        # 旧模式保持现有行为，供回归测试兼容。
        approved = bool(
            state.get(
                "cleaning_large_deletion_approved",
                False,
            )
        )

        cleaned_file_path = str(
            state.get(
                "cleaned_file_path", ""
            ) or ""
        ).strip()

        if approved and cleaned_file_path:
            return "cleaning_quality"

        return "cleaning"

    if task_mode == "forecast_report":
        return "forecast"

    return "invalid"


def route_after_agent(
    state: LogisticsAgentState,
) -> str:
    """
    running -> 正常继续
    needs_human_input -> 暂停
    其他状态 -> 失败
    """
    status = state.get("status")

    if status == "running":
        return "report"

    if status == "needs_human_input":
        return "needs_human_input"

    return "failed"