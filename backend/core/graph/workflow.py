"""
物流多智能体外层 LangGraph Workflow。

当前第一版采用确定性 task_mode 路由，
用于验证已有专业 Agent 的协作能力和系统级执行流程。

Cleaning 链包含：
CleaningAgent
-> Cleaning Quality Gate
-> Human-in-the-loop（必要时）
-> ReportAgent
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langgraph.graph import (
    END,
    START,
    StateGraph,
)

from core.graph.nodes.cleaning_quality_node import (
    cleaning_quality_node,
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
    """
    处理不支持的 task_mode。
    """

    return {
        "current_agent": None,
        "status": "failed",
        "report_status": "error",
        "report_error": (
            "不支持的 task_mode。"
            "当前支持：sql_report、"
            "cleaning_report、forecast_report。"
        ),
        "execution_trace": [
            "invalid"
        ],
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

    Cleaning 正常流程：

        Cleaning
        -> Cleaning Quality
        -> Report

    Cleaning HITL 恢复：

        START
        -> Cleaning Quality
        -> Report

    人工确认后不会重新执行 CleaningAgent。
    """

    workflow = StateGraph(
        LogisticsAgentState
    )

    # ======================================================
    # 1. 注册节点
    # ======================================================

    workflow.add_node(
        "sql",
        sql_node,
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
    # 2. START 路由
    # ======================================================

    workflow.add_conditional_edges(
        START,
        route_from_start,
        {
            "sql": "sql",
            "cleaning": "cleaning",

            # Cleaning HITL 获得人工确认后，
            # 直接重新进入 Quality Gate。
            "cleaning_quality": (
                "cleaning_quality"
            ),

            "forecast": "forecast",
            "invalid": "invalid",
        },
    )

    # ======================================================
    # 3. SQL 后
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
    # 4. Cleaning 后
    # ======================================================

    workflow.add_conditional_edges(
        "cleaning",
        route_after_agent,
        {
            # Cleaning 成功不能直接报告，
            # 必须进入 Quality Gate。
            "report": (
                "cleaning_quality"
            ),

            "needs_human_input": END,
            "failed": END,
        },
    )

    # ======================================================
    # 5. Cleaning Quality Gate 后
    # ======================================================

    workflow.add_conditional_edges(
        "cleaning_quality",
        route_after_agent,
        {
            "report": "report",

            # 大比例删行等需要人工确认时，
            # 第一阶段先结束外层图并返回状态。
            "needs_human_input": END,

            "failed": END,
        },
    )

    # ======================================================
    # 6. Forecast 后
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
    # 7. Report / Invalid 后结束
    # ======================================================

    workflow.add_edge(
        "report",
        END,
    )

    workflow.add_edge(
        "invalid",
        END,
    )

    return workflow.compile()