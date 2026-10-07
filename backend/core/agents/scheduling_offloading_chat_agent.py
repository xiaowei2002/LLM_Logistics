from __future__ import annotations

import json
from typing import Any

from langchain.agents import create_agent

from core.llm import LLMClient, get_llm
from core.prompts.scheduling_offloading import SCHEDULING_OFFLOADING_SYSTEM_PROMPT
from core.tools.scheduling_offloading.scheduling_tool import (
    SCHEDULING_OFFLOADING_TOOLS,
)


class SchedulingOffloadingChatAgent:
    """Natural-language orchestrator; numerical decisions always come from tools."""

    def __init__(self, client: LLMClient | None = None) -> None:
        self.client = client or get_llm()
        self.agent = create_agent(
            model=self.client.model,
            tools=SCHEDULING_OFFLOADING_TOOLS,
            system_prompt=SCHEDULING_OFFLOADING_SYSTEM_PROMPT,
        )

    @staticmethod
    def _as_dict(content: Any) -> dict[str, Any] | None:
        if isinstance(content, dict):
            return content
        if isinstance(content, str):
            try:
                value = json.loads(content)
                return value if isinstance(value, dict) else None
            except json.JSONDecodeError:
                return None
        return None

    def invoke(self, query: str) -> dict[str, Any]:
        response = self.agent.invoke({
            "messages": [{"role": "user", "content": query}]
        })
        messages = response.get("messages", [])
        if not messages:
            raise ValueError("智能体未返回任何消息。")

        run_result = None
        tool_error = None
        for message in messages:
            if getattr(message, "type", "") != "tool":
                continue
            value = self._as_dict(getattr(message, "content", None))
            if not value or value.get("tool") != "run_scheduling_offloading":
                continue
            if value.get("status") == "error":
                tool_error = str(value.get("message") or "联合计算失败")
            elif isinstance(value.get("result"), dict):
                run_result = value["result"]

        answer = str(getattr(messages[-1], "content", "")).strip()
        if tool_error:
            raise RuntimeError(tool_error)
        if not answer:
            raise ValueError("智能体未返回最终回答。")
        return {"answer": answer, "run_result": run_result}
