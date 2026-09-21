"""
ReportAgent 适配节点。

负责汇总 SQL、Cleaning、Forecast 等上游结果，
调用 ReportAgent 生成最终报告。
"""

from __future__ import annotations

import trace
from typing import Protocol

from core.graph.state import LogisticsAgentState


class ReportGenerator(Protocol):
    def generate(self, instruction: str) -> str:
        ...


def build_report_node(
    report_agent: ReportGenerator,
):
    """
    构建报告生成节点。
    """

    def report_node(
        state: LogisticsAgentState,
    ) -> LogisticsAgentState:


        user_query = str(
            state.get(
                "user_query",
                "",
            )
            or ""
        ).strip()

        sql_result = state.get(
            "sql_result"
        )

        cleaning_result = state.get(
            "cleaning_result"
        )

        forecast_result = state.get(
            "forecast_result"
        )

        sections: list[str] = []

        if user_query:
            sections.append(
                f"【用户任务】\n{user_query}"
            )

        if sql_result:
            sections.append(
                f"【数据查询结果】\n{sql_result}"
            )

        if cleaning_result:
            sections.append(
                f"【数据清洗结果】\n{cleaning_result}"
            )

        if forecast_result:
            forecast_answer = (
                forecast_result.get(
                    "answer"
                )
                if isinstance(
                    forecast_result,
                    dict,
                )
                else None
            )

            if forecast_answer:
                sections.append(
                    "【需求预测结果】\n"
                    + str(
                        forecast_answer
                    )
                )

        # 补充异常信息
        errors: list[str] = []

        if state.get("sql_error"):
            errors.append(
                "SQLAgent："
                + str(
                    state["sql_error"]
                )
            )

        if state.get("cleaning_error"):
            errors.append(
                "CleaningAgent："
                + str(
                    state["cleaning_error"]
                )
            )

        if state.get("forecast_error"):
            errors.append(
                "DemandForecastAgent："
                + str(
                    state["forecast_error"]
                )
            )

        if errors:
            sections.append(
                "【异常信息】\n"
                + "\n".join(errors)
            )

        if not sections:
            return {
                "current_agent": "report",
                "report_status": "error",
                "report_error": (
                    "没有可用于生成报告的上游结果。"
                ),
                "status": "failed",
                "execution_trace": ["report"],
            }

        instruction = (
            "请基于以下多智能体系统真实执行结果，"
            "生成最终物流分析报告。\n\n"
            + "\n\n".join(sections)
        )

        try:
            result = report_agent.generate(
                instruction
            )

            if not result.strip():
                return {
                    "current_agent": "report",
                    "report_result": "",
                    "report_status": "error",
                    "report_error": (
                        "ReportAgent 未返回报告。"
                    ),
                    "status": "failed",
                    "execution_trace": ["report"],
                }

            return {
                "current_agent": "report",
                "report_result": result,
                "report_status": "success",
                "report_error": None,
                "status": "completed",
                "execution_trace": ["report"],
            }

        except Exception as exc:
            return {
                "current_agent": "report",
                "report_status": "error",
                "report_error": str(exc),
                "status": "failed",
                "execution_trace": ["report"],
            }

    return report_node