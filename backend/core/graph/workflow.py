"""
物流多智能体外层 LangGraph Workflow。

当前第一版采用确定性 task_mode 路由，
用于验证已有专业 Agent 的协作能力和系统级执行流程。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langgraph.graph import END, START, StateGraph

from core.graph.router import (
    route_after_agent,
    route_from_start,
)
from core.graph.state import LogisticsAgentState


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

    trace = list(
        state.get(
            "execution_trace",
            [],
        )
    )

    return {
        "current_agent": None,
        "status": "failed",
        "report_status": "error",
        "report_error": (
            "不支持的 task_mode。"
            "当前支持：sql_report、"
            "cleaning_report、forecast_report。"
        ),
        "execution_trace": trace,
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

    专业 Agent 通过 Adapter Node 注入，
    Workflow 本身不直接依赖具体 Agent 实现。
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
            "forecast": "forecast",
            "invalid": "invalid",
        },
    )

    # ======================================================
    # 3. 专业 Agent 执行后的统一路由
    # ======================================================

    for node_name in (
        "sql",
        "cleaning",
        "forecast",
    ):
        workflow.add_conditional_edges(
            node_name,
            route_after_agent,
            {
                "report": "report",

                # 发生 Human Review 时，
                # 当前第一版先结束外层图，
                # 将状态返回给调用方处理。
                "needs_human_input": END,

                # Agent 执行失败时直接结束，
                # 不生成成功报告。
                "failed": END,
            },
        )

    # ======================================================
    # 4. Report / Invalid 后结束
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