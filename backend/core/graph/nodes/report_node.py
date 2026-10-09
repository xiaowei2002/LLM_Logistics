"""
ReportAgent 适配节点。

负责汇总 SQL、Cleaning、Cleaning Quality Gate、
Forecast、Human-in-the-loop 等上游真实结果，
调用 ReportAgent 生成最终报告。

Report Node 本身不做业务推断或重新计算，
只负责把外层 Workflow 中已经存在的事实
完整、明确地传递给 ReportAgent。
"""

from __future__ import annotations

import json
from typing import Protocol

from core.graph.state import LogisticsAgentState


class ReportGenerator(Protocol):
    def generate(
        self,
        instruction: str,
    ) -> str:
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

        # ==================================================
        # 1. 读取统一 State 中的真实结果
        # ==================================================

        user_query = str(
            state.get(
                "user_query",
                "",
            )
            or ""
        ).strip()

        data_file_path = state.get(
            "data_file_path"
        )

        cleaned_file_path = state.get(
            "cleaned_file_path"
        )

        sql_result = state.get(
            "sql_result"
        )

        cleaning_result = state.get(
            "cleaning_result"
        )

        cleaning_quality = state.get(
            "cleaning_quality"
        )

        cleaning_quality_status = state.get(
            "cleaning_quality_status"
        )

        cleaning_quality_error = state.get(
            "cleaning_quality_error"
        )

        large_deletion_approved = bool(
            state.get(
                "cleaning_large_deletion_approved",
                False,
            )
        )

        forecast_result = state.get(
            "forecast_result"
        )

        # ==================================================
        # 2. 构造给 ReportAgent 的事实区块
        # ==================================================

        sections: list[str] = []

        # --------------------------------------------------
        # 用户任务
        # --------------------------------------------------

        if user_query:
            sections.append(
                "【用户任务】\n"
                + user_query
            )

        # --------------------------------------------------
        # 数据文件路径
        # --------------------------------------------------

        file_lines: list[str] = []

        if data_file_path:
            file_lines.append(
                "原始数据文件路径："
                + str(
                    data_file_path
                )
            )

        if cleaned_file_path:
            file_lines.append(
                "清洗后数据文件路径："
                + str(
                    cleaned_file_path
                )
            )

        if file_lines:
            sections.append(
                "【数据文件】\n"
                + "\n".join(
                    file_lines
                )
            )

        # --------------------------------------------------
        # SQL
        # --------------------------------------------------

        if sql_result:
            sections.append(
                "【数据查询结果】\n"
                + str(
                    sql_result
                )
            )

        # --------------------------------------------------
        # CleaningAgent 自然语言结果
        # --------------------------------------------------

        if cleaning_result:
            sections.append(
                "【CleaningAgent 清洗结果】\n"
                + str(
                    cleaning_result
                )
            )

        # --------------------------------------------------
        # Cleaning Quality Gate
        #
        # 这是确定性 Python 检查结果。
        # 对文件存在性、行数、删除率、缺失值、
        # 重复、负值等事实，其可信优先级高于
        # CleaningAgent 的自然语言总结。
        # --------------------------------------------------

        if cleaning_quality:
            quality_json = json.dumps(
                cleaning_quality,
                ensure_ascii=False,
                indent=2,
                default=str,
            )

            sections.append(
                "【Cleaning Quality Gate 确定性验证结果】\n"
                "以下内容由 Python 对实际落盘文件重新读取后得到，"
                "不是 LLM 推测。\n"
                + quality_json
            )

        # --------------------------------------------------
        # Cleaning Quality Gate 状态
        # --------------------------------------------------

        if cleaning_quality_status:
            quality_status_lines = [
                "Quality Gate 状态："
                + str(
                    cleaning_quality_status
                )
            ]

            if cleaning_quality_error:
                quality_status_lines.append(
                    "Quality Gate 错误："
                    + str(
                        cleaning_quality_error
                    )
                )

            sections.append(
                "【Cleaning Quality Gate 状态】\n"
                + "\n".join(
                    quality_status_lines
                )
            )

        # --------------------------------------------------
        # Human-in-the-loop
        #
        # 第二阶段恢复后：
        # needs_human_input 已经会被清除，
        # human_reason 也会变为 None。
        #
        # 因此不能只看当前 needs_human_input。
        # cleaning_large_deletion_approved=True
        # 本身就是已经发生人工确认的系统事实。
        # --------------------------------------------------

        if large_deletion_approved:
            hitl_lines = [
                "已发生 Human-in-the-loop 人工确认。",
                (
                    "人工确认事项："
                    "Cleaning Quality Gate 检测到"
                    "自动删行比例超过 10% 安全阈值。"
                ),
                (
                    "人工确认结果："
                    "用户或业务方已明确批准"
                    "保留当前清洗结果并继续下游流程。"
                ),
                (
                    "系统处理方式："
                    "人工确认后未重新执行 CleaningAgent，"
                    "而是复用已经落盘的清洗文件，"
                    "重新通过 Cleaning Quality Gate 后"
                    "进入 Report。"
                ),
            ]

            sections.append(
                "【Human-in-the-loop 人工确认】\n"
                + "\n".join(
                    hitl_lines
                )
            )

        # --------------------------------------------------
        # Forecast
        # --------------------------------------------------

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

        # ==================================================
        # 3. 补充异常信息
        # ==================================================

        errors: list[str] = []

        if state.get(
            "sql_error"
        ):
            errors.append(
                "SQLAgent："
                + str(
                    state[
                        "sql_error"
                    ]
                )
            )

        if state.get(
            "cleaning_error"
        ):
            errors.append(
                "CleaningAgent："
                + str(
                    state[
                        "cleaning_error"
                    ]
                )
            )

        if state.get(
            "forecast_error"
        ):
            errors.append(
                "DemandForecastAgent："
                + str(
                    state[
                        "forecast_error"
                    ]
                )
            )

        if errors:
            sections.append(
                "【异常信息】\n"
                + "\n".join(
                    errors
                )
            )

        # ==================================================
        # 4. 没有上游结果时禁止生成报告
        # ==================================================

        if not sections:
            return {
                "current_agent": "report",
                "report_status": "error",
                "report_error": (
                    "没有可用于生成报告的上游结果。"
                ),
                "status": "failed",
                "execution_trace": [
                    "report"
                ],
            }

        # ==================================================
        # 5. 构造最终 ReportAgent instruction
        # ==================================================

        instruction = (
            "请基于以下多智能体系统真实执行结果，"
            "生成最终物流分析报告。\n\n"
            "【重要事实优先级】\n"
            "1. Cleaning Quality Gate 是 Python "
            "对实际落盘文件重新读取后的确定性结果。\n"
            "2. 对文件路径、行数、列数、删除数量、删除比例、"
            "缺失值、重复值、负值等可程序化验证的事实，"
            "如果 CleaningAgent 的自然语言描述与 "
            "Cleaning Quality Gate 不一致，"
            "必须以 Cleaning Quality Gate 为准。\n"
            "3. 如果 CleaningAgent 的清洗台账内部存在"
            "算术矛盾或无法解释的记录变化，"
            "不得自行替它补全原因，"
            "应明确指出存在不一致。\n"
            "4. 如果输入中明确提供"
            "cleaning_large_deletion_approved=True "
            "或 Human-in-the-loop 人工确认信息，"
            "报告必须如实说明发生过人工确认，"
            "不得写成“无人工确认”或“全自动执行”。\n\n"
            + "\n\n".join(
                sections
            )
        )

        # ==================================================
        # 6. 调用 ReportAgent
        # ==================================================

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
                    "execution_trace": [
                        "report"
                    ],
                }

            return {
                "current_agent": "report",
                "report_result": result,
                "report_status": "success",
                "report_error": None,
                "status": "completed",
                "execution_trace": [
                    "report"
                ],
            }

        except Exception as exc:
            return {
                "current_agent": "report",
                "report_status": "error",
                "report_error": str(
                    exc
                ),
                "status": "failed",
                "execution_trace": [
                    "report"
                ],
            }

    return report_node