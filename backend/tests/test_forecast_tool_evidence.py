
"""
ForecastAgent 工具证据安全检查测试。

测试 _run_agent 的核心行为，
不调用在线 LLM 或真实预测模型。

运行：
python -m tests.test_forecast_tool_evidence
"""

from __future__ import annotations

import json
import unittest

from langchain_core.messages import (
    AIMessage,
    ToolMessage,
)

from core.agents.demand_forecast_agent import (
    DemandForecastAgent,
)


class FakeBaseAgent:

    def __init__(self, messages):
        self.messages = messages

    def invoke(self, payload):
        return {
            "messages": self.messages
        }


class ForecastToolEvidenceTest(unittest.TestCase):

    def make_agent(self, messages):
        # 不调用 DemandForecastAgent.__init__，
        # 避免创建 LLM、LangGraph 或网络连接。
        agent = object.__new__(
            DemandForecastAgent
        )

        agent.base_agent = FakeBaseAgent(
            messages
        )

        return agent

    def tool_message(
        self,
        *,
        name="demand_forecast",
        payload=None,
    ):
        if payload is None:
            payload = {
                "tool": "demand_forecast",
                "status": "success",
                "result": {
                    "status": "success",
                    "forecast": {
                        "M001": {
                            "t_plus_1": 10,
                            "t_plus_2": 12,
                        }
                    },
                    "two_hour_peak_demand": {
                        "M001": 12
                    },
                },
            }

        return ToolMessage(
            content=json.dumps(
                payload, ensure_ascii=False
            ),
            tool_call_id="test_tool_call",
            name=name,
        )

    def run_messages(self, messages):
        agent = self.make_agent(messages)

        return agent._run_agent({
            "query": (
                "请预测 2026-01-30 12:00:00 "
                "之后两小时的物料需求"
            )
        })

    def test_no_tool_message_rejected(self):
        result = self.run_messages([
            AIMessage(
                content="M001 需求预测为 10 和 12"
            )
        ])

        self.assertEqual(
            result["tool_status"], "error"
        )
        self.assertIn(
            "未经工具验证",
            result["tool_error"],
        )

    def test_valid_tool_message_accepted(self):
        result = self.run_messages([
            self.tool_message(),
            AIMessage(
                content="预测工具结果已返回。"
            ),
        ])

        self.assertEqual(
            result["tool_status"], "success"
        )
        self.assertIsNone(
            result["tool_error"]
        )

    def test_unknown_tool_rejected(self):
        result = self.run_messages([
            self.tool_message(
                name="unrelated_tool",
                payload={
                    "tool": "unrelated_tool",
                    "status": "success",
                },
            ),
            AIMessage(
                content="模拟预测回答"
            ),
        ])

        self.assertEqual(
            result["tool_status"], "error"
        )

    def test_mismatched_tool_name_rejected(self):
        result = self.run_messages([
            self.tool_message(
                name="compare_demand_forecasts"
            ),
            AIMessage(
                content="模拟预测回答"
            ),
        ])

        self.assertEqual(
            result["tool_status"], "error"
        )

    def test_invalid_tool_payload_rejected(self):
        result = self.run_messages([
            ToolMessage(
                content="not valid json",
                tool_call_id="test_invalid",
                name="demand_forecast",
            ),
            AIMessage(
                content="模拟预测回答"
            ),
        ])

        self.assertEqual(
            result["tool_status"], "error"
        )


if __name__ == "__main__":
    unittest.main()