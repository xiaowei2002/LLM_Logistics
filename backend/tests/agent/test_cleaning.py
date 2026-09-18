"""
CleaningAgent单元测试。
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

BACKEND_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_ROOT))

from core.agents.cleaning_agent import PROMPT_PATH, CleaningAgent  # noqa: E402


class CleaningAgentTest(unittest.IsolatedAsyncioTestCase):
    def _make_client(self):
        client = MagicMock()
        client.model = MagicMock()
        return client

    def _make_agent(self):
        return CleaningAgent(client=self._make_client())

    async def test_prompt_loaded_from_yaml(self):
        agent = self._make_agent()
        self.assertTrue(agent.system_prompt)
        self.assertIn("数据清洗专家", agent.system_prompt)
        self.assertIn("read_metadata", agent.system_prompt)

    async def test_ensure_agent_builds_once(self):
        agent = self._make_agent()
        agent.mcp = MagicMock()
        agent.mcp.get_tools = AsyncMock(return_value=[])

        fake_agent = MagicMock()
        with patch("core.agents.cleaning_agent.create_agent", return_value=fake_agent) as mock_create:
            first = await agent.ensure_agent()
            second = await agent.ensure_agent()

        self.assertIs(first, fake_agent)
        self.assertIs(second, fake_agent)
        mock_create.assert_called_once()

        _, kwargs = mock_create.call_args
        self.assertIs(kwargs["model"], agent.client.model)
        self.assertEqual(kwargs["tools"], [])
        self.assertEqual(kwargs["system_prompt"], agent.system_prompt)

    async def test_clean_returns_last_message_content(self):
        agent = self._make_agent()

        message = MagicMock()
        message.content = "去重删除 47 行，缺失值按中位数填充 312 行"

        fake_agent = MagicMock()
        fake_agent.ainvoke = AsyncMock(return_value={"messages": [message]})

        with patch.object(agent, "ensure_agent", new=AsyncMock(return_value=fake_agent)):
            result = await agent.clean("清洗近7天的出库表")

        self.assertEqual(result, "去重删除 47 行，缺失值按中位数填充 312 行")
        fake_agent.ainvoke.assert_awaited_once()

    async def test_clean_returns_empty_when_no_messages(self):
        agent = self._make_agent()

        fake_agent = MagicMock()
        fake_agent.ainvoke = AsyncMock(return_value={"messages": []})

        with patch.object(agent, "ensure_agent", new=AsyncMock(return_value=fake_agent)):
            result = await agent.clean("随便清洗")

        self.assertEqual(result, "")


@unittest.skipUnless(
    os.getenv("RUN_INTEGRATION_TESTS") == "1",
    "集成测试需真实 LLM + mcp-pandas 服务，设置 RUN_INTEGRATION_TESTS=1 后运行",
)
class CleaningAgentIntegrationTest(unittest.IsolatedAsyncioTestCase):
    """真实环境集成测试：走通 clean() 全链路（LLM + mcp-pandas MCP）。"""

    async def test_clean_runs_end_to_end(self):
        agent = CleaningAgent()
        result = await agent.clean("列出你当前可用的数据清洗工具")
        self.assertIsInstance(result, str)
        self.assertTrue(result.strip(), "clean() 应返回非空回答")
        print(f"\n[集成] 回答：{result}")


if __name__ == "__main__":
    unittest.main()
