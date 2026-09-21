"""
Report Node 单元测试。
"""

import sys
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from core.graph.nodes.report_node import build_report_node  # noqa: E402


class FakeReportAgent:
    def __init__(
        self,
        result: str = "最终报告",
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.last_instruction: str | None = None

    def generate(
        self,
        instruction: str,
    ) -> str:
        self.last_instruction = instruction

        if self.error is not None:
            raise self.error

        return self.result


class ReportNodeTest(unittest.TestCase):

    def test_report_node_success(self):
        agent = FakeReportAgent(
            result="物流分析报告生成完成。"
        )

        node = build_report_node(agent)

        result = node(
            {
                "user_query": "预测物料需求",
                "sql_result": "查询到订单数据",
                "cleaning_result": "清洗完成",
                "forecast_result": {
                    "answer": "M001=10"
                },
                "execution_trace": [
                    "sql",
                    "cleaning",
                    "forecast",
                ],
            }
        )

        self.assertEqual(
            result["report_status"],
            "success",
        )

        self.assertEqual(
            result["status"],
            "completed",
        )

        self.assertEqual(
            result["report_result"],
            "物流分析报告生成完成。",
        )

        # Report Node 单独调用时，
        # 只返回当前节点新增的执行轨迹。
        # 完整历史轨迹由 LangGraph State reducer 负责累计。
        self.assertEqual(
            result["execution_trace"],
            ["report"],
        )

        self.assertIn(
            "查询到订单数据",
            agent.last_instruction,
        )

        self.assertIn(
            "清洗完成",
            agent.last_instruction,
        )

        self.assertIn(
            "M001=10",
            agent.last_instruction,
        )

    def test_report_node_without_upstream_results(self):
        agent = FakeReportAgent()

        node = build_report_node(agent)

        result = node(
            {
                "execution_trace": [],
            }
        )

        self.assertEqual(
            result["report_status"],
            "error",
        )

        self.assertEqual(
            result["status"],
            "failed",
        )

        self.assertEqual(
            result["execution_trace"],
            ["report"],
        )

    def test_report_node_empty_result(self):
        agent = FakeReportAgent(
            result=""
        )

        node = build_report_node(agent)

        result = node(
            {
                "user_query": "生成报告",
                "execution_trace": [],
            }
        )

        self.assertEqual(
            result["report_status"],
            "error",
        )

        self.assertEqual(
            result["status"],
            "failed",
        )

        self.assertEqual(
            result["execution_trace"],
            ["report"],
        )

    def test_report_node_exception(self):
        agent = FakeReportAgent(
            error=RuntimeError(
                "报告生成失败"
            )
        )

        node = build_report_node(agent)

        result = node(
            {
                "user_query": "生成报告",
                "execution_trace": [],
            }
        )

        self.assertEqual(
            result["report_status"],
            "error",
        )

        self.assertEqual(
            result["report_error"],
            "报告生成失败",
        )

        self.assertEqual(
            result["status"],
            "failed",
        )

        self.assertEqual(
            result["execution_trace"],
            ["report"],
        )


if __name__ == "__main__":
    unittest.main()