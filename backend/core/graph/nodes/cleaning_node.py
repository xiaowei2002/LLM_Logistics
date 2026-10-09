"""
CleaningAgent 适配节点。

负责把外层 LogisticsAgentState 中的数据文件路径与用户任务，
转换为 CleaningAgent.clean() 调用，并把结果写回统一 State。

本节点同时负责确定 CleaningAgent 的输出路径，
避免由 LLM 自行选择不可预测的落盘位置。
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

    使用依赖注入，便于单元测试，
    不直接在节点内部实例化真实 CleaningAgent。
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

        # ==================================================
        # 1. 输入路径检查
        # ==================================================

        if not file_path:
            return {
                "current_agent": "cleaning",
                "cleaning_status": "error",
                "cleaning_error": (
                    "未提供待清洗数据文件路径。"
                ),
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

        if not path.is_file():
            return {
                "current_agent": "cleaning",
                "cleaning_status": "error",
                "cleaning_error": (
                    f"数据路径不是文件：{file_path}"
                ),
                "status": "failed",
                "execution_trace": ["cleaning"],
            }

        # ==================================================
        # 2. 确定性输出路径
        # ==================================================

        output_path = path.with_name(
            f"{path.stem}_cleaned{path.suffix}"
        ).resolve()

        # 避免旧输出文件影响本次结果判断。
        if output_path.exists():
            try:
                output_path.unlink()
            except OSError as exc:
                return {
                    "current_agent": "cleaning",
                    "cleaning_status": "error",
                    "cleaning_error": (
                        "无法删除旧的清洗结果文件："
                        f"{output_path}。原因：{exc}"
                    ),
                    "status": "failed",
                    "execution_trace": ["cleaning"],
                }

        # ==================================================
        # 3. 构造 CleaningAgent 指令
        # ==================================================

        instruction = (
            "请对以下数据文件进行质量诊断和清洗：\n"
            f"{path.resolve()}\n\n"
            "请先使用 read_metadata 检查数据质量，"
            "再根据实际数据问题执行必要的清洗。\n\n"
            "【重要输出约束】\n"
            "清洗完成后，必须将最终清洗结果保存到以下指定路径：\n"
            f"{output_path}\n"
            "不得自行修改文件名、目录或另选其他保存路径。\n"
            "保存完成后，必须重新读取该文件进行最终复核。"
        )

        if user_query:
            instruction += (
                "\n\n本次任务目标："
                f"{user_query}"
            )

        instruction += (
            "\n\n最终回复必须说明："
            "\n1. 发现了哪些数据质量问题；"
            "\n2. 执行了哪些处理；"
            "\n3. 各步骤影响的数据量；"
            "\n4. 清洗后的实际文件保存路径；"
            "\n5. 重新读取后的复核结果；"
            "\n6. 仍然存在的问题。"
        )

        # ==================================================
        # 4. 调用真实 CleaningAgent
        # ==================================================

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

            # ==============================================
            # 5. 不相信文字声明，检查文件是否真实落盘
            # ==============================================

            if not output_path.exists():
                return {
                    "current_agent": "cleaning",
                    "cleaning_result": result,
                    "cleaned_file_path": None,
                    "cleaning_status": "error",
                    "cleaning_error": (
                        "CleaningAgent 返回了清洗结果，"
                        "但指定的清洗文件没有真实落盘："
                        f"{output_path}"
                    ),
                    "status": "failed",
                    "execution_trace": ["cleaning"],
                }

            if not output_path.is_file():
                return {
                    "current_agent": "cleaning",
                    "cleaning_result": result,
                    "cleaned_file_path": None,
                    "cleaning_status": "error",
                    "cleaning_error": (
                        "CleaningAgent 的输出路径存在，"
                        "但不是有效文件："
                        f"{output_path}"
                    ),
                    "status": "failed",
                    "execution_trace": ["cleaning"],
                }

            # ==============================================
            # 6. 成功写回统一 State
            # ==============================================

            return {
                "current_agent": "cleaning",
                "cleaning_result": result,
                "cleaned_file_path": str(
                    output_path
                ),
                "cleaning_status": "success",
                "cleaning_error": None,
                "status": "running",
                "execution_trace": ["cleaning"],
            }

        except Exception as exc:
            return {
                "current_agent": "cleaning",
                "cleaned_file_path": None,
                "cleaning_status": "error",
                "cleaning_error": str(exc),
                "status": "failed",
                "execution_trace": ["cleaning"],
            }

    return cleaning_node