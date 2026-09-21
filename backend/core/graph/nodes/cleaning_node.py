"""
CleaningAgent 适配节点。

负责把外层 LogisticsAgentState 中的数据文件路径与用户任务，
转换为 CleaningAgent.clean() 调用，并把结果写回统一 State。
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from core.graph.state import LogisticsAgentState


class DataCleaningAgent(Protocol):
    async def clean(self, instruction: str) -> str:
        ...


def build_cleaning_node(
    cleaning_agent: DataCleaningAgent,
):
    """
    构建数据清洗节点。

    使用依赖注入，便于单元测试，不直接在节点内部实例化真实 CleaningAgent。
    """

    async def cleaning_node(
        state: LogisticsAgentState,
    ) -> LogisticsAgentState:

        file_path = str(
            state.get(
                "data_file_path",
                "",
            )
            or ""
        ).strip()

        user_query = str(
            state.get(
                "user_query",
                "",
            )
            or ""
        ).strip()


        if not file_path:
            return {
                "current_agent": "cleaning",
                "cleaning_status": "error",
                "cleaning_error": "未提供待清洗数据文件路径。",
                "status": "failed",
                "execution_trace": ["cleaning"],
            }

        path = Path(file_path)

        if not path.exists():
            return {
                "current_agent": "cleaning",
                "cleaning_status": "error",
                "cleaning_error": (
                    f"数据文件不存在：{file_path}"
                ),
                "status": "failed",
                "execution_trace": ["cleaning"],
            }

        instruction = (
            f"请对以下数据文件进行质量诊断和清洗：{path.resolve()}。\n"
            "请先使用 read_metadata 检查数据质量，"
            "再根据实际数据问题执行必要的清洗。"
        )

        if user_query:
            instruction += (
                f"\n本次任务目标：{user_query}"
            )

        instruction += (
            "\n清洗完成后请说明："
            "发现了哪些数据质量问题、"
            "执行了哪些处理、"
            "各步骤影响的数据量、"
            "清洗后数据文件保存路径，"
            "以及仍然存在的问题。"
        )

        try:
            result = await cleaning_agent.clean(
                instruction
            )

            if not result.strip():
                return {
                    "current_agent": "cleaning",
                    "cleaning_result": "",
                    "cleaning_status": "error",
                    "cleaning_error": (
                        "CleaningAgent 未返回清洗结果。"
                    ),
                    "status": "failed",
                    "execution_trace": ["cleaning"],
                }

            return {
                "current_agent": "cleaning",
                "cleaning_result": result,
                "cleaning_status": "success",
                "cleaning_error": None,
                "status": "running",
                "execution_trace": ["cleaning"],
            }

        except Exception as exc:
            return {
                "current_agent": "cleaning",
                "cleaning_status": "error",
                "cleaning_error": str(exc),
                "status": "failed",
                "execution_trace": ["cleaning"],
            }

    return cleaning_node