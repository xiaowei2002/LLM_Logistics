"""
物流多智能体工作流确定性路由。

第一版不使用 LLM Supervisor，
避免路由随机性影响系统功能验证和后续评估。
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
    根据显式 task_mode 选择第一个专业 Agent。
    """

    task_mode = str(
        state.get("task_mode", "")
        or ""
    ).strip()

    if task_mode == "sql_report":
        return "sql"

    if task_mode == "cleaning_report":
        return "cleaning"

    if task_mode == "forecast_report":
        return "forecast"

    return "invalid"


def route_after_agent(
    state: LogisticsAgentState,
) -> str:
    """
    专业 Agent 执行后决定是否进入报告节点。

    - running：进入 Report
    - needs_human_input：暂停当前外层流程
    - failed：结束
    """

    status = state.get("status")

    if status == "running":
        return "report"

    if status == "needs_human_input":
        return "needs_human_input"

    return "failed"