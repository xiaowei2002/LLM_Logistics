from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI

from core.llm.settings import LLMSettings
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

    def __init__(
        self,
        settings: LLMSettings | None = None,
    ) -> None:

        self.settings = settings or LLMSettings.from_env()

        self.model = ChatOpenAI(
            model=self.settings.model,
            api_key=self.settings.api_key,
            base_url=self.settings.base_url,
            temperature=(
                self.settings.temperature
                if self.settings.temperature is not None
                else 0
            ),
            max_tokens=self.settings.max_tokens,
            timeout=self.settings.timeout,
            max_retries=self.settings.max_retries,
        )

        self.agent = create_agent(
            model=self.model,
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