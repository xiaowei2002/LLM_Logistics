"""
物流多智能体工作流确定性路由。

第一版不使用 LLM Supervisor，
避免路由随机性影响系统功能验证和后续评估。

Cleaning HITL 场景下：
如果大比例删行已经获得人工确认，
则从 START 直接重新进入 Cleaning Quality Gate，
不重复执行 CleaningAgent。
"""

from __future__ import annotations

from core.graph.state import LogisticsAgentState


VALID_TASK_MODES = {
    "sql_report",
    "cleaning_report",
    "forecast_report",
}


def route_from_start(
    state: LogisticsAgentState,
) -> str:
    """
    根据显式 task_mode 选择第一个执行节点。

    Cleaning HITL 恢复逻辑：

    cleaning_report
    + cleaned_file_path 已存在
    + cleaning_large_deletion_approved=True

    → 直接重新进入 cleaning_quality

    这样人工确认后不会再次调用 CleaningAgent，
    避免 LLM 二次清洗产生不同结果。
    """

    task_mode = str(
        state.get(
            "task_mode",
            "",
        )
        or ""
    ).strip()

    if task_mode == "sql_report":
        return "sql"

    if task_mode == "cleaning_report":

        approved = bool(
            state.get(
                "cleaning_large_deletion_approved",
                False,
            )
        )

        cleaned_file_path = str(
            state.get(
                "cleaned_file_path",
                "",
            )
            or ""
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
    Agent / Gate 执行后的统一路由。

    - running:
        进入下一个正常节点

    - needs_human_input:
        当前外层流程暂停，由调用方处理人工确认

    - failed:
        终止
    """

    status = state.get(
        "status"
    )

    if status == "running":
        return "report"

    if status == "needs_human_input":
        return "needs_human_input"

    return "failed"