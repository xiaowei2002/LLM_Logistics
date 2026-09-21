"""
ReportAgent 单元测试。

只验证 ReportAgent 本身的接口、Prompt 和结果提取逻辑，
不调用真实 LLM。
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

BACKEND_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_ROOT))

from core.agents.report_agent import PROMPT_PATH, ReportAgent  # noqa: E402


class ReportAgentTest(unittest.TestCase):

    @staticmethod
    def _make_client():
        client = MagicMock()
        client.model = MagicMock()
        return client

    def test_prompt_loaded_from_yaml(self):
        with patch(
            "core.agents.report_agent.create_agent"
        ):
            agent = ReportAgent(
                client=self._make_client()
            )

        self.assertTrue(PROMPT_PATH.exists())
        self.assertIsNotNone(agent.agent)

    def test_create_agent_without_tools(self):
        fake_agent = MagicMock()

        with patch(
            "core.agents.report_agent.create_agent",
            return_value=fake_agent,
        ) as mock_create:

            ReportAgent(
                client=self._make_client()
            )

        mock_create.assert_called_once()

        call_kwargs = mock_create.call_args.kwargs

        self.assertEqual(
            call_kwargs["tools"],
            [],
        )

        self.assertIn(
            "报告",
            call_kwargs["system_prompt"],
        )

    def test_generate_returns_last_message_content(self):
        fake_message = MagicMock()
        fake_message.content = "本次需求预测任务已完成。"

        fake_agent = MagicMock()
        fake_agent.invoke.return_value = {
            "messages": [
                fake_message
            ]
        }

        with patch(
            "core.agents.report_agent.create_agent",
            return_value=fake_agent,
        ):
            agent = ReportAgent(
                client=self._make_client()
            )

        result = agent.generate(
            (
                "用户任务：预测未来两小时需求。\n"
                "预测结果：M001=10。"
            )
        )

        self.assertEqual(
            result,
            "本次需求预测任务已完成。",
        )

        fake_agent.invoke.assert_called_once()

    def test_generate_returns_empty_when_no_messages(self):
        fake_agent = MagicMock()
        fake_agent.invoke.return_value = {
            "messages": []
        }

        with patch(
            "core.agents.report_agent.create_agent",
            return_value=fake_agent,
        ):
            agent = ReportAgent(
                client=self._make_client()
            )

        result = agent.generate(
            "生成测试报告。"
        )

        self.assertEqual(
            result,
            "",
        )


if __name__ == "__main__":
    unittest.main()