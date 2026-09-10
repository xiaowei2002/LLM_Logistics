"""
SQLAgent单元测试。
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

BACKEND_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_ROOT))

from core.agents.sql_agent import PROMPT_PATH, SQLAgent  # noqa: E402


class SQLAgentTest(unittest.IsolatedAsyncioTestCase):
    def _make_client(self):
        client = MagicMock()
        client.model = MagicMock()
        return client

    def _make_agent(self):
        return SQLAgent(client=self._make_client())

    async def test_prompt_loaded_from_yaml(self):
        agent = self._make_agent()
        self.assertTrue(agent._system_prompt)
        self.assertIn("数据读取专家", agent._system_prompt)
        self.assertIn("list_tables", agent._system_prompt)

    async def test_ensure_agent_builds_once(self):
        agent = self._make_agent()
        agent._mcp = MagicMock()
        agent._mcp.get_tools = AsyncMock(return_value=[])

        fake_agent = MagicMock()
        with patch("core.agents.sql_agent.create_agent", return_value=fake_agent) as mock_create:
            first = await agent._ensure_agent()
            second = await agent._ensure_agent()

        self.assertIs(first, fake_agent)
        self.assertIs(second, fake_agent)
        mock_create.assert_called_once()

        _, kwargs = mock_create.call_args
        self.assertIs(kwargs["model"], agent.client.model)
        self.assertEqual(kwargs["tools"], [])
        self.assertEqual(kwargs["system_prompt"], agent._system_prompt)

    async def test_query_returns_last_message_content(self):
        agent = self._make_agent()

        message = MagicMock()
        message.content = "共有 1,284 条出库记录"

        fake_agent = MagicMock()
        fake_agent.ainvoke = AsyncMock(return_value={"messages": [message]})

        with patch.object(agent, "_ensure_agent", new=AsyncMock(return_value=fake_agent)):
            result = await agent.query("近7天有多少出库记录")

        self.assertEqual(result, "共有 1,284 条出库记录")
        fake_agent.ainvoke.assert_awaited_once()

    async def test_query_returns_empty_when_no_messages(self):
        agent = self._make_agent()

        fake_agent = MagicMock()
        fake_agent.ainvoke = AsyncMock(return_value={"messages": []})

        with patch.object(agent, "_ensure_agent", new=AsyncMock(return_value=fake_agent)):
            result = await agent.query("随便问")

        self.assertEqual(result, "")


@unittest.skipUnless(
    os.getenv("RUN_INTEGRATION_TESTS") == "1",
    "集成测试需真实 LLM + mysql-mcp-server + MySQL，设置 RUN_INTEGRATION_TESTS=1 后运行",
)
class SQLAgentIntegrationTest(unittest.IsolatedAsyncioTestCase):
    """真实环境集成测试：走通 query() 全链路（LLM + MCP + MySQL）。"""

    async def test_query_runs_end_to_end(self):
        agent = SQLAgent()
        result = await agent.query("列出数据库里所有的表")
        self.assertIsInstance(result, str)
        self.assertTrue(result.strip(), "query() 应返回非空回答")
        print(f"\n[集成] 回答：{result}")


if __name__ == "__main__":
    unittest.main()
