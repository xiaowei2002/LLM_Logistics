
"""
数据读取智能体。

SQL V2：
1. 对 MCP execute_sql 进行只读工具包装。
2. 保留原有 query(question) -> str 接口。
3. 增加 query_with_evidence() 结构化证据接口。
4. 证据失败时，不返回已验证查询成功状态。
5. 支持通过环境变量选择 MCP 通信模式。

注意：
query() 是历史兼容接口，只返回文本。
需要可信执行状态的下游应使用
query_with_evidence()。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from langchain.agents import create_agent

from core.llm import LLMClient, get_llm
from core.mcp.mysql_client import MySQLMCPClient
from core.tools.sql_evidence import (
    inspect_sql_evidence,
)
from core.tools.sql_mcp_guard import (
    build_readonly_sql_tools,
)
from core.utils.yaml_loader import load_yaml


PROMPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "prompts"
    / "sql_agent.yaml"
)


class SQLAgent:
    """基于 MySQL MCP 的数据读取智能体。"""

    def __init__(
        self,
        client: LLMClient | None = None,
    ) -> None:
        self.client = client or get_llm()

        transport = os.getenv(
            "MYSQL_MCP_TRANSPORT",
            "sse",
        ).strip().lower()

        if transport not in {"sse", "stdio"}:
            raise ValueError(
                "MYSQL_MCP_TRANSPORT 必须为 sse 或 stdio。"
            )

        self.mcp = MySQLMCPClient(
            transport=transport
        )
        self.agent = None

        self.system_prompt = load_yaml(
            PROMPT_PATH
        )["system_prompt"]

    async def ensure_agent(self):
        """
        首次使用时构建 LangChain Agent。

        严禁直接向 LLM 暴露原始 execute_sql。
        """
        if self.agent is None:
            raw_tools = await self.mcp.get_tools()

            guarded_tools = build_readonly_sql_tools(
                raw_tools
            )

            self.agent = create_agent(
                model=self.client.model,
                tools=guarded_tools,
                system_prompt=self.system_prompt,
            )

        return self.agent

    async def _invoke_agent(
        self,
        question: str,
    ) -> dict[str, Any]:
        agent = await self.ensure_agent()

        result = await agent.ainvoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": question,
                    }
                ]
            }
        )

        if not isinstance(result, dict):
            raise TypeError(
                "SQLAgent 返回的运行状态不是字典。"
            )

        return result

    async def query(
        self,
        question: str,
    ) -> str:
        """
        历史兼容接口。

        仅返回最终自然语言内容。
        不保证存在成功的 SQL 工具执行证据。
        """
        result = await self._invoke_agent(
            question
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

    async def query_with_evidence(
        self,
        question: str,
    ) -> dict[str, Any]:
        """
        执行一次查询并核验工具执行证据。

        与 query() 不同，此接口返回明确状态。
        不将 LLM 的非空回答直接认定为成功。

        不验证最终回答的全部数字与工具数据
        是否逐项一致。
        """
        if not isinstance(question, str):
            return {
                "status": "error",
                "answer": "",
                "query_verified": False,
                "tool_name": None,
                "tool_call_id": None,
                "error": "用户查询必须是字符串。",
            }

        if not question.strip():
            return {
                "status": "error",
                "answer": "",
                "query_verified": False,
                "tool_name": None,
                "tool_call_id": None,
                "error": "用户查询为空。",
            }

        try:
            result = await self._invoke_agent(
                question
            )

            messages = result.get(
                "messages",
                [],
            )

            return inspect_sql_evidence(
                messages
            )

        except Exception as exc:
            return {
                "status": "error",
                "answer": "",
                "query_verified": False,
                "tool_name": None,
                "tool_call_id": None,
                "error": (
                    f"{type(exc).__name__}: {exc}"
                ),
            }
