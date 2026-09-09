from __future__ import annotations

from typing import Any

from langchain.agents import create_agent

from core.llm import LLMClient, get_llm
from core.prompts.demand_forecast import DEMAND_FORECAST_SYSTEM_PROMPT
from core.tools.demand_forecast.demand_forecast_tool import (
    DEMAND_FORECAST_TOOLS,
)


class DemandForecastAgent:
    """
    需求预测 Agent。

    职责：
    - 理解自然语言预测请求
    - 选择需求预测 Tool
    - 调用真实预测模型
    - 基于 Tool 输出生成业务回答
    """

    def __init__(self, client: LLMClient | None = None) -> None:
        # 复用全局 LLM 单例（get_llm），与聊天共享同一份配置与热更新入口
        self.client = client or get_llm()

        self.agent = create_agent(
            model=self.client.model,
            tools=DEMAND_FORECAST_TOOLS,
            system_prompt=DEMAND_FORECAST_SYSTEM_PROMPT,
        )

    def invoke(
        self,
        query: str,
    ) -> dict[str, Any]:

        return self.agent.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": query,
                    }
                ]
            }
        )