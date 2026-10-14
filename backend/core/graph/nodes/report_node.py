
"""
ReportAgent 适配节点。

负责汇总 SQL、Cleaning、Cleaning Quality Gate、
Forecast、Human-in-the-loop 等上游真实结果，
调用 ReportAgent 生成最终报告。

Report Node 不重新查询、清洗或预测。

安全约束：
1. 只有用户任务而没有上游事实时，不生成报告。
2. 明确的上游异常可用于生成失败说明报告。
3. 保留 Cleaning Quality Gate 的事实优先级。
4. 保留 HITL 人工确认事实。
5. 只有状态一致的结构化预测结果才作为预测依据。
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from core.graph.state import LogisticsAgentState


class ReportGenerator(Protocol):
    def generate(self, instruction: str) -> str:
        ...


def _has_content(value: Any) -> bool:
    """判断上游是否提供了非空内容。"""
    if value is None:
        return False

    if isinstance(value, str):
        return bool(value.strip())

    if isinstance(value, (dict, list, tuple, set)):
        return bool(value)

    return True


def _is_verified_forecast_payload(
    forecast_result: Any,
    forecast_status: Any,
) -> bool:
    """
    检查是否存在具备基本状态一致性的结构化预测结果。

    这里的 verified 指结构与状态检查通过，
    不是证明预测数字与底层数据库完全一致。

    当前兼容：
        forecast_result["tool_result"]

    对于未知结构、缺失状态或失败状态，不将其
    提升为可信的结构化预测事实。
    """
    if forecast_status != "success":
        return False

    if not isinstance(forecast_result, dict):
        return False

    tool_result = forecast_result.get("tool_result")

    if not isinstance(tool_result, dict):
        return False

    if tool_result.get("status") != "success":
        return False

    forecast = tool_result.get("forecast")

    if not isinstance(forecast, dict) or not forecast:
        return False

    return True


def build_report_node(
    report_agent: ReportGenerator,
):
    """构建报告生成节点。"""

    def report_node(
        state: LogisticsAgentState,
    ) -> LogisticsAgentState:

        # ==================================================
        # 1. 读取统一 State
        # ==================================================

        user_query = str(
            state.get("user_query", "") or ""
        ).strip()

        data_file_path = state.get("data_file_path")
        cleaned_file_path = state.get(
            "cleaned_file_path"
        )

        sql_result = state.get("sql_result")
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
        forecast_status = state.get(
            "forecast_status"
        )

        # ==================================================
        # 2. 分类构造报告事实
        # ==================================================

        sections: list[str] = []

        # 是否存在上游执行事实，而非仅有用户需求。
        has_upstream_evidence = False

        # --------------------------------------------------
        # 用户任务
        # --------------------------------------------------

        if user_query:
            sections.append(
                "【用户任务】\n" + user_query
            )

        # --------------------------------------------------
        # 文件路径
        # --------------------------------------------------

        file_lines: list[str] = []

        if data_file_path:
            file_lines.append(
                "原始数据文件路径："
                + str(data_file_path)
            )

        if cleaned_file_path:
            file_lines.append(
                "清洗后数据文件路径："
                + str(cleaned_file_path)
            )

        if file_lines:
            sections.append(
                "【数据文件】\n"
                + "\n".join(file_lines)
            )

        # 原始文件路径属于用户输入或数据定位信息，
        # 本身不证明 SQL、Cleaning、Forecast 已执行。
        # 清洗后路径则作为辅助事实保存，但不能单独
        # 证明清洗成功。

        # --------------------------------------------------
        # SQL 查询结果
        # --------------------------------------------------

        if _has_content(sql_result):
            sections.append(
                "【数据查询结果】\n"
                + str(sql_result)
            )
            has_upstream_evidence = True

        # --------------------------------------------------
        # CleaningAgent 自然语言结果
        # --------------------------------------------------

        if _has_content(cleaning_result):
            sections.append(
                "【CleaningAgent 清洗结果】\n"
                + str(cleaning_result)
            )
            has_upstream_evidence = True

        # --------------------------------------------------
        # Cleaning Quality Gate
        # --------------------------------------------------

        if _has_content(cleaning_quality):
            quality_json = json.dumps(
                cleaning_quality,
                ensure_ascii=False,
                indent=2,
                default=str,
            )

            sections.append(
                "【Cleaning Quality Gate 确定性验证结果】\n"
                "以下内容由 Python 对实际落盘文件"
                "重新读取后得到，不是 LLM 推测。\n"
                + quality_json
            )

            has_upstream_evidence = True

        # --------------------------------------------------
        # Cleaning Quality Gate 状态
        # --------------------------------------------------

        if _has_content(cleaning_quality_status):
            quality_status_lines = [
                "Quality Gate 状态："
                + str(cleaning_quality_status)
            ]

            if _has_content(cleaning_quality_error):
                quality_status_lines.append(
                    "Quality Gate 错误："
                    + str(cleaning_quality_error)
                )

            sections.append(
                "【Cleaning Quality Gate 状态】\n"
                + "\n".join(
                    quality_status_lines
                )
            )

            has_upstream_evidence = True

        elif _has_content(cleaning_quality_error):
            sections.append(
                "【Cleaning Quality Gate 状态】\n"
                "Quality Gate 错误："
                + str(cleaning_quality_error)
            )
            has_upstream_evidence = True

        # --------------------------------------------------
        # HITL 人工确认
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
                + "\n".join(hitl_lines)
            )
            has_upstream_evidence = True

        # --------------------------------------------------
        # Forecast 自然语言及结构化结果
        # --------------------------------------------------

        if isinstance(forecast_result, dict):
            forecast_answer = forecast_result.get(
                "answer"
            )

            if (
                _has_content(forecast_answer)
                and forecast_status != "error"
            ):
                sections.append(
                    "【需求预测结果】\n"
                    + str(forecast_answer)
                )
                has_upstream_evidence = True

            if _is_verified_forecast_payload(
                forecast_result,
                forecast_status,
            ):
                tool_result = forecast_result[
                    "tool_result"
                ]

                forecast_json = json.dumps(
                    tool_result,
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )

                sections.append(
                    "【需求预测工具结构化结果】\n"
                    "以下为上游提供且状态检查通过的"
                    "结构化预测数据。"
                    "其数值不由 Report Node 重新计算；"
                    "报告应优先使用这些原始字段"
                    "核对预测回答。\n"
                    + forecast_json
                )

                has_upstream_evidence = True

        # --------------------------------------------------
        # 上游异常
        # --------------------------------------------------

        errors: list[str] = []

        if _has_content(state.get("sql_error")):
            errors.append(
                "SQLAgent："
                + str(state["sql_error"])
            )

        if _has_content(state.get("cleaning_error")):
            errors.append(
                "CleaningAgent："
                + str(state["cleaning_error"])
            )

        if _has_content(state.get("forecast_error")):
            errors.append(
                "DemandForecastAgent："
                + str(state["forecast_error"])
            )

        if errors:
            sections.append(
                "【异常信息】\n"
                + "\n".join(errors)
            )

            # 明确的失败信息可以作为失败说明报告依据。
            has_upstream_evidence = True

        # ==================================================
        # 3. RP002：拒绝仅有用户任务的报告
        # ==================================================

        if not has_upstream_evidence:
            return {
                "current_agent": "report",
                "report_status": "error",
                "report_error": (
                    "没有可用于生成报告的上游执行"
                    "结果或异常信息。"
                ),
                "status": "failed",
                "execution_trace": ["report"],
            }

        # ==================================================
        # 4. 构造 ReportAgent instruction
        # ==================================================

        instruction = (
            "请基于以下多智能体系统真实执行结果，"
            "生成最终物流分析报告。\n\n"

            "【重要事实优先级】\n"

            "1. Cleaning Quality Gate 是 Python "
            "对实际落盘文件重新读取后的确定性结果。\n"

            "2. 对文件路径、行数、列数、删除数量、"
            "删除比例、缺失值、重复值、负值等"
            "可程序化验证的事实，如果 "
            "CleaningAgent 的自然语言描述与 "
            "Cleaning Quality Gate 不一致，"
            "必须以 Cleaning Quality Gate 为准。\n"

            "3. 如果 CleaningAgent 的清洗台账内部"
            "存在算术矛盾或无法解释的记录变化，"
            "不得自行补全原因，"
            "应明确指出存在不一致。\n"

            "4. 如果输入中明确提供"
            "cleaning_large_deletion_approved=True "
            "或 Human-in-the-loop 人工确认信息，"
            "报告必须如实说明发生过人工确认，"
            "不得写成“无人工确认”或“全自动执行”。\n"

            "5. 结构化预测结果只有在上游状态"
            "与工具返回状态检查通过时才作为预测依据。"
            "如果结构化数据与自然语言回答不一致，"
            "应指出差异，不能自行修正预测数据。\n"

            "6. 上游执行失败时，可以生成失败说明报告，"
            "但不得将失败的 SQL、清洗或预测任务"
            "描述为成功完成。\n\n"

            + "\n\n".join(sections)
        )

        # ==================================================
        # 5. 调用 ReportAgent
        # ==================================================

        try:
            result = report_agent.generate(
                instruction
            )

            if not isinstance(result, str):
                return {
                    "current_agent": "report",
                    "report_status": "error",
                    "report_error": (
                        "ReportAgent 返回了非文本结果。"
                    ),
                    "status": "failed",
                    "execution_trace": ["report"],
                }

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