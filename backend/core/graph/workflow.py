
"""
物流多智能体外层 LangGraph Workflow。

支持：
- sql_report
- cleaning_report
- cleaning_safe_report
- forecast_report

安全清洗首次执行：
Precheck -> Restricted Cleaning -> Quality Gate -> Report

安全清洗事后恢复：
Resume Validation -> Quality Gate -> Report

旧版 Cleaning 工作流保持兼容。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langgraph.graph import (
    END,
    START,
    StateGraph,
)

from core.graph.nodes.cleaning_precheck_node import (
    cleaning_precheck_node,
)
from core.graph.nodes.cleaning_quality_node import (
    cleaning_quality_node,
)
from core.graph.nodes.cleaning_resume_node import (
    cleaning_resume_node,
)
from core.graph.nodes.restricted_cleaning_node import (
    restricted_cleaning_node,
)
from core.graph.router import (
    route_after_agent,
    route_from_start,
)
from core.graph.state import (
    LogisticsAgentState,
)


NodeCallable = Callable[
    [LogisticsAgentState],
    Any,
]


def _invalid_task_node(
    state: LogisticsAgentState,
) -> LogisticsAgentState:
    return {
        "current_agent": None,
        "status": "failed",
        "report_status": "error",
        "report_error": (
            "不支持的 task_mode。"
            "当前支持：sql_report、"
            "cleaning_report、"
            "cleaning_safe_report、"
            "forecast_report。"
        ),
        "execution_trace": ["invalid"],
    }


def build_logistics_workflow(
    *,
    sql_node: NodeCallable,
    cleaning_node: NodeCallable,
    forecast_node: NodeCallable,
    report_node: NodeCallable,
):
    """
    构建物流多智能体外层工作流。

    保留现有依赖注入接口。
    """

    workflow = StateGraph(
        LogisticsAgentState
    )

    # ======================================================
    # 1. 注册节点
    # ======================================================

    workflow.add_node("sql", sql_node)

    workflow.add_node(
        "cleaning_precheck",
        cleaning_precheck_node,
    )

    workflow.add_node(
        "restricted_cleaning",
        restricted_cleaning_node,
    )

    workflow.add_node(
        "cleaning_resume",
        cleaning_resume_node,
    )

    workflow.add_node(
        "cleaning",
        cleaning_node,
    )

    workflow.add_node(
        "cleaning_quality",
        cleaning_quality_node,
    )

    workflow.add_node(
        "forecast",
        forecast_node,
    )

    workflow.add_node(
        "report",
        report_node,
    )

    workflow.add_node(
        "invalid",
        _invalid_task_node,
    )

    # ======================================================
    # 2. START
    # ======================================================

    workflow.add_conditional_edges(
        START,
        route_from_start,
        {
            "sql": "sql",
            "cleaning_precheck": "cleaning_precheck",
            "cleaning_resume": "cleaning_resume",
            "cleaning": "cleaning",
            "cleaning_quality": "cleaning_quality",
            "forecast": "forecast",
            "invalid": "invalid",
        },
    )

    # ======================================================
    # 3. Precheck -> Restricted Executor
    # ======================================================

    workflow.add_conditional_edges(
        "cleaning_precheck",
        route_after_agent,
        {
            "report": "restricted_cleaning",
            "needs_human_input": END,
            "failed": END,
        },
    )

    # ======================================================
    # 4. Restricted Executor -> Quality Gate
    # ======================================================

    workflow.add_conditional_edges(
        "restricted_cleaning",
        route_after_agent,
        {
            "report": "cleaning_quality",
            "needs_human_input": END,
            "failed": END,
        },
    )

    # ======================================================
    # 5. 安全恢复 -> Quality Gate
    # ======================================================

    workflow.add_conditional_edges(
        "cleaning_resume",
        route_after_agent,
        {
            "report": "cleaning_quality",
            "needs_human_input": END,
            "failed": END,
        },
    )

    # ======================================================
    # 6. SQL -> Report
    # ======================================================

    workflow.add_conditional_edges(
        "sql",
        route_after_agent,
        {
            "report": "report",
            "needs_human_input": END,
            "failed": END,
        },
    )

    # ======================================================
    # 7. 旧版 Cleaning -> Quality Gate
    # ======================================================

    workflow.add_conditional_edges(
        "cleaning",
        route_after_agent,
        {
            "report": "cleaning_quality",
            "needs_human_input": END,
            "failed": END,
        },
    )

    # ======================================================
    # 8. Quality Gate -> Report / HITL / END
    # ======================================================

    workflow.add_conditional_edges(
        "cleaning_quality",
        route_after_agent,
        {
            "report": "report",
            "needs_human_input": END,
            "failed": END,
        },
    )

    # ======================================================
    # 9. Forecast -> Report
    # ======================================================

    workflow.add_conditional_edges(
        "forecast",
        route_after_agent,
        {
            "report": "report",
            "needs_human_input": END,
            "failed": END,
        },
    )

    # ======================================================
    # 10. END
    # ======================================================

    workflow.add_edge("report", END)
    workflow.add_edge("invalid", END)

    return workflow.compile()