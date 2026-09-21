"""
DemandForecastAgent 适配节点。

负责调用现有 DemandForecastAgent，
并把其 LangGraph/HITL 返回结果转换为外层统一 State。
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from core.graph.state import LogisticsAgentState


class ForecastAgent(Protocol):
    def invoke(
        self,
        query: str,
        thread_id: str,
    ) -> dict[str, Any]:
        ...


def _extract_interrupt_payload(
    result: dict[str, Any],
) -> dict[str, Any] | None:
    """
    从 DemandForecastAgent 返回结果中提取 interrupt payload。
    """

    interrupts = result.get("__interrupt__")

    if not interrupts:
        return None

    interrupt_item = interrupts[0]

    value = getattr(
        interrupt_item,
        "value",
        None,
    )

    if isinstance(value, dict):
        return value

    return {
        "question": str(value),
    }


def build_forecast_node(
    forecast_agent: ForecastAgent,
):
    """
    构建需求预测节点。
    """

    def forecast_node(
        state: LogisticsAgentState,
    ) -> LogisticsAgentState:

        query = str(
            state.get(
                "user_query",
                "",
            )
            or ""
        ).strip()


        if not query:
            return {
                "current_agent": "forecast",
                "forecast_status": "error",
                "forecast_error": "用户预测任务为空。",
                "status": "failed",
                "execution_trace": ["forecast"],
            }

        thread_id = str(
            state.get(
                "forecast_thread_id",
                "",
            )
            or ""
        ).strip()

        if not thread_id:
            thread_id = str(uuid.uuid4())

        try:
            result = forecast_agent.invoke(
                query=query,
                thread_id=thread_id,
            )

            interrupt_payload = (
                _extract_interrupt_payload(
                    result
                )
            )

            if interrupt_payload:
                return {
                    "current_agent": "forecast",
                    "forecast_result": result,
                    "forecast_status": (
                        "needs_human_input"
                    ),
                    "forecast_error": None,
                    "forecast_thread_id": thread_id,
                    "needs_human_input": True,
                    "human_reason": (
                        interrupt_payload.get(
                            "reason"
                        )
                    ),
                    "human_question": (
                        interrupt_payload.get(
                            "question"
                        )
                    ),
                    "status": (
                        "needs_human_input"
                    ),
                    "execution_trace": ["forecast"],
                }

            tool_status = result.get(
                "tool_status"
            )

            tool_error = result.get(
                "tool_error"
            )

            answer = result.get(
                "answer"
            )

            if (
                tool_status == "error"
                or tool_error
            ):
                return {
                    "current_agent": "forecast",
                    "forecast_result": result,
                    "forecast_status": "error",
                    "forecast_error": str(
                        tool_error
                        or "预测工具执行失败。"
                    ),
                    "forecast_thread_id": thread_id,
                    "status": "failed",
                    "execution_trace": ["forecast"],
                }

            if not answer:
                return {
                    "current_agent": "forecast",
                    "forecast_result": result,
                    "forecast_status": "error",
                    "forecast_error": (
                        "DemandForecastAgent "
                        "未返回最终预测回答。"
                    ),
                    "forecast_thread_id": thread_id,
                    "status": "failed",
                    "execution_trace": ["forecast"],
                }

            return {
                "current_agent": "forecast",
                "forecast_result": result,
                "forecast_status": "success",
                "forecast_error": None,
                "forecast_thread_id": thread_id,
                "needs_human_input": False,
                "human_reason": None,
                "human_question": None,
                "status": "running",
                "execution_trace": ["forecast"],
            }

        except Exception as exc:
            return {
                "current_agent": "forecast",
                "forecast_status": "error",
                "forecast_error": str(exc),
                "forecast_thread_id": thread_id,
                "status": "failed",
                "execution_trace": ["forecast"],
            }

    return forecast_node