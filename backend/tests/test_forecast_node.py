"""
Forecast Node 单元测试。

验证：
- 正常预测
- 空任务
- Agent 异常
- Tool 错误
- Human-in-the-loop interrupt
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from core.graph.nodes.forecast_node import build_forecast_node  # noqa: E402


class FakeForecastAgent:
    def __init__(
        self,
        result: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result or {}
        self.error = error
        self.last_query: str | None = None
        self.last_thread_id: str | None = None

    def invoke(
        self,
        query: str,
        thread_id: str,
    ) -> dict[str, Any]:

        self.last_query = query
        self.last_thread_id = thread_id

        if self.error is not None:
            raise self.error

        return self.result


class ForecastNodeTest(unittest.TestCase):

    def test_forecast_node_success(self):
        agent = FakeForecastAgent(
            result={
                "query": "预测需求",
                "answer": "M001=10",
                "tool_status": "success",
                "tool_error": None,
            }
        )

        node = build_forecast_node(agent)

        result = node(
            {
                "user_query": "预测需求",
                "execution_trace": [],
            }
        )

        self.assertEqual(
            result["forecast_status"],
            "success",
        )

        self.assertEqual(
            result["status"],
            "running",
        )

        self.assertEqual(
            result["execution_trace"],
            ["forecast"],
        )

        self.assertTrue(
            result["forecast_thread_id"]
        )

        self.assertFalse(
            result["needs_human_input"]
        )

    def test_forecast_node_empty_query(self):
        agent = FakeForecastAgent()

        node = build_forecast_node(agent)

        result = node(
            {
                "user_query": "",
                "execution_trace": [],
            }
        )

        self.assertEqual(
            result["forecast_status"],
            "error",
        )

        self.assertEqual(
            result["status"],
            "failed",
        )

    def test_forecast_node_tool_error(self):
        agent = FakeForecastAgent(
            result={
                "answer": "",
                "tool_status": "error",
                "tool_error": "模型文件不存在",
            }
        )

        node = build_forecast_node(agent)

        result = node(
            {
                "user_query": "预测需求",
                "execution_trace": [],
            }
        )

        self.assertEqual(
            result["forecast_status"],
            "error",
        )

        self.assertEqual(
            result["forecast_error"],
            "模型文件不存在",
        )

        self.assertEqual(
            result["status"],
            "failed",
        )

    def test_forecast_node_interrupt(self):
        interrupt = SimpleNamespace(
            value={
                "reason": "missing_time",
                "question": "请补充预测时间。",
            }
        )

        agent = FakeForecastAgent(
            result={
                "__interrupt__": [
                    interrupt
                ]
            }
        )

        node = build_forecast_node(agent)

        result = node(
            {
                "user_query": "预测未来需求",
                "execution_trace": [],
            }
        )

        self.assertEqual(
            result["forecast_status"],
            "needs_human_input",
        )

        self.assertTrue(
            result["needs_human_input"]
        )

        self.assertEqual(
            result["human_reason"],
            "missing_time",
        )

        self.assertEqual(
            result["human_question"],
            "请补充预测时间。",
        )

        self.assertEqual(
            result["status"],
            "needs_human_input",
        )

    def test_forecast_node_exception(self):
        agent = FakeForecastAgent(
            error=RuntimeError(
                "预测 Agent 调用失败"
            )
        )

        node = build_forecast_node(agent)

        result = node(
            {
                "user_query": "预测需求",
                "execution_trace": [],
            }
        )

        self.assertEqual(
            result["forecast_status"],
            "error",
        )

        self.assertEqual(
            result["forecast_error"],
            "预测 Agent 调用失败",
        )

        self.assertEqual(
            result["status"],
            "failed",
        )


if __name__ == "__main__":
    unittest.main()