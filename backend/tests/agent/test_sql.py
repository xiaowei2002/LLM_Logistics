
"""
SQLAgent 单元测试。

验证当前 SQLAgent 的真实接口：
1. system_prompt 正确加载
2. ensure_agent() 仅构建一次 Agent
3. MCP 原始 execute_sql 被只读安全工具包装
4. query() 正确返回最后一条消息
5. query() 能处理空消息列表

默认不运行真实 LLM + MCP + MySQL 集成测试。

运行：
    python -m tests.agent.test_sql

详细输出：
    python -m unittest tests.agent.test_sql -v
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.tools import StructuredTool


BACKEND_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_ROOT))

from core.agents.sql_agent import PROMPT_PATH, SQLAgent  # noqa: E402


class SQLAgentTest(unittest.IsolatedAsyncioTestCase):
    """SQLAgent 离线单元测试。"""

    def _make_client(self):
        client = MagicMock()
        client.model = MagicMock()
        return client

    def _make_agent(self):
        return SQLAgent(
            client=self._make_client()
        )

    # ======================================================
    # 1. Prompt 加载
    # ======================================================

    async def test_prompt_loaded_from_yaml(self):
        agent = self._make_agent()

        self.assertTrue(
            agent.system_prompt
        )

        self.assertIn(
            "数据读取专家",
            agent.system_prompt,
        )

        self.assertTrue(
            PROMPT_PATH.is_file()
        )

    # ======================================================
    # 2. Agent 只初始化一次，并接入只读工具包装
    # ======================================================

    async def test_ensure_agent_builds_once(self):
        agent = self._make_agent()

        async def fake_execute_sql(
            query: str,
        ) -> str:
            """模拟 MCP 原始 SQL 执行工具。"""
            return "模拟查询结果"

        raw_execute_tool = StructuredTool.from_function(
            coroutine=fake_execute_sql,
            name="execute_sql",
            description="模拟原始 SQL 工具。",
        )

        agent.mcp = MagicMock()
        agent.mcp.get_tools = AsyncMock(
            return_value=[
                raw_execute_tool
            ]
        )

        fake_agent = MagicMock()

        with patch(
            "core.agents.sql_agent.create_agent",
            return_value=fake_agent,
        ) as mock_create:
            first = await agent.ensure_agent()
            second = await agent.ensure_agent()

        self.assertIs(
            first,
            fake_agent,
        )

        self.assertIs(
            second,
            fake_agent,
        )

        mock_create.assert_called_once()
        agent.mcp.get_tools.assert_awaited_once()

        _, kwargs = mock_create.call_args

        self.assertIs(
            kwargs["model"],
            agent.client.model,
        )

        guarded_tools = kwargs["tools"]

        self.assertEqual(
            len(guarded_tools),
            1,
        )

        self.assertEqual(
            guarded_tools[0].name,
            "execute_sql",
        )

        # 安全要求：不能把原始 SQL 工具直接交给 LLM。
        self.assertIsNot(
            guarded_tools[0],
            raw_execute_tool,
        )

        self.assertEqual(
            kwargs["system_prompt"],
            agent.system_prompt,
        )

    # ======================================================
    # 3. query() 返回最后一条消息
    # ======================================================

    async def test_query_returns_last_message_content(self):
        agent = self._make_agent()

        message = MagicMock()
        message.content = (
            "共有 1,284 条出库记录"
        )

        fake_agent = MagicMock()
        fake_agent.ainvoke = AsyncMock(
            return_value={
                "messages": [
                    message
                ]
            }
        )

        with patch.object(
            agent,
            "ensure_agent",
            new=AsyncMock(
                return_value=fake_agent
            ),
        ):
            result = await agent.query(
                "近7天有多少出库记录"
            )

        self.assertEqual(
            result,
            "共有 1,284 条出库记录",
        )

        fake_agent.ainvoke.assert_awaited_once()

    # ======================================================
    # 4. query() 处理空消息
    # ======================================================

    async def test_query_returns_empty_when_no_messages(self):
        agent = self._make_agent()

        fake_agent = MagicMock()
        fake_agent.ainvoke = AsyncMock(
            return_value={
                "messages": []
            }
        )

        with patch.object(
            agent,
            "ensure_agent",
            new=AsyncMock(
                return_value=fake_agent
            ),
        ):
            result = await agent.query(
                "随便问"
            )

        self.assertEqual(
            result,
            "",
        )


# ==========================================================
# 5. 真实集成测试（默认跳过）
# ==========================================================

@unittest.skipUnless(
    os.getenv("RUN_INTEGRATION_TESTS") == "1",
    (
        "真实集成测试需要 LLM、"
        "MySQL MCP Server 和 MySQL；"
        "须设置 RUN_INTEGRATION_TESTS=1"
    ),
)
class SQLAgentIntegrationTest(
    unittest.IsolatedAsyncioTestCase
):
    """
    SQLAgent 真实集成测试。

    只有显式启用 RUN_INTEGRATION_TESTS=1
    才会连接真实 LLM、MCP 和数据库。
    """

    async def test_query_runs_end_to_end(self):
        agent = SQLAgent()

        result = await agent.query(
            "列出数据库里所有的表"
        )

        self.assertIsInstance(
            result,
            str,
        )

        self.assertTrue(
            result.strip(),
            "query() 应返回非空回答",
        )

        print(
            f"\n[集成] 回答：{result}"
        )


if __name__ == "__main__":
    unittest.main()
