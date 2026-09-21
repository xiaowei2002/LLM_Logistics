"""
报告生成智能体。

负责根据 SQL、数据清洗、需求预测等上游智能体的真实结果，
生成最终物流分析报告。
"""

from pathlib import Path

from langchain.agents import create_agent

from core.llm import LLMClient, get_llm
from core.utils.yaml_loader import load_yaml


PROMPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "prompts"
    / "report_agent.yaml"
)


class ReportAgent:
    """物流分析报告生成智能体。"""

    def __init__(
        self,
        client: LLMClient | None = None,
    ) -> None:
        self.client = client or get_llm()

        self.agent = create_agent(
            model=self.client.model,
            tools=[],
            system_prompt=load_yaml(
                PROMPT_PATH
            )["system_prompt"],
        )

    def generate(
        self,
        instruction: str,
    ) -> str:
        """
        根据上游智能体结果生成报告。

        ReportAgent 不负责重新查询、清洗或预测，
        只对输入中已经存在的真实结果进行整理。
        """

        result = self.agent.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": instruction,
                    }
                ]
            }
        )

        messages = result.get(
            "messages",
            [],
        )

        if not messages:
            return ""

        return str(
            getattr(
                messages[-1],
                "content",
                "",
            )
        )