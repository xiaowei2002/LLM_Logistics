"""
外层 Multi-Agent LangGraph 集成测试。

全部使用 Fake Node，
验证确定性路由、执行轨迹、Human Review 和终态，
不调用真实 LLM、数据库或 MCP。
"""

import asyncio
import sys
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from core.graph.workflow import build_logistics_workflow  # noqa: E402


async def fake_sql_node(state):
    return {
        "current_agent": "sql",
        "sql_result": "查询完成",
        "sql_status": "success",
        "status": "running",
        "execution_trace": ["sql"],
    }


async def fake_cleaning_node(state):
    return {
        "current_agent": "cleaning",
        "cleaning_result": "清洗完成",
        "cleaning_status": "success",
        "status": "running",
        "execution_trace": ["cleaning"],
    }


def fake_forecast_node(state):
    return {
        "current_agent": "forecast",
        "forecast_result": {
            "answer": "M001=10",
        },
        "forecast_status": "success",
        "status": "running",
        "execution_trace": ["forecast"],
    }


def fake_forecast_interrupt_node(state):
    return {
        "current_agent": "forecast",
        "forecast_result": {
            "__interrupt__": [
                "请补充预测时间。"
            ],
        },
        "forecast_status": "needs_human_input",
        "forecast_thread_id": "test-thread-001",
        "needs_human_input": True,
        "human_reason": "missing_time",
        "human_question": "请补充预测时间。",
        "status": "needs_human_input",
        "execution_trace": ["forecast"],
    }


def fake_report_node(state):
    return {
        "current_agent": "report",
        "report_result": "最终报告",
        "report_status": "success",
        "status": "completed",
        "execution_trace": ["report"],
    }


class MultiAgentWorkflowTest(
    unittest.TestCase
):

    def _build_graph(self):
        return build_logistics_workflow(
            sql_node=fake_sql_node,
            cleaning_node=fake_cleaning_node,
            forecast_node=fake_forecast_node,
            report_node=fake_report_node,
        )

    def test_sql_report_route(self):
        graph = self._build_graph()

        result = asyncio.run(
            graph.ainvoke(
                {
                    "user_query": "查询订单",
                    "task_mode": "sql_report",
                    "execution_trace": [],
                    "status": "running",
                }
            )
        )

        self.assertEqual(
            result["execution_trace"],
            [
                "sql",
                "report",
            ],
        )

        self.assertEqual(
            result["status"],
            "completed",
        )

    def test_cleaning_report_route(self):
        graph = self._build_graph()

        result = asyncio.run(
            graph.ainvoke(
                {
                    "user_query": "清洗数据",
                    "task_mode": "cleaning_report",
                    "execution_trace": [],
                    "status": "running",
                }
            )
        )

        self.assertEqual(
            result["execution_trace"],
            [
                "cleaning",
                "report",
            ],
        )

        self.assertEqual(
            result["status"],
            "completed",
        )

    def test_forecast_report_route(self):
        graph = self._build_graph()

        result = asyncio.run(
            graph.ainvoke(
                {
                    "user_query": "预测需求",
                    "task_mode": "forecast_report",
                    "execution_trace": [],
                    "status": "running",
                }
            )
        )

        self.assertEqual(
            result["execution_trace"],
            [
                "forecast",
                "report",
            ],
        )

        self.assertEqual(
            result["status"],
            "completed",
        )

    def test_forecast_human_review_stops_before_report(self):
        graph = build_logistics_workflow(
            sql_node=fake_sql_node,
            cleaning_node=fake_cleaning_node,
            forecast_node=fake_forecast_interrupt_node,
            report_node=fake_report_node,
        )

        result = asyncio.run(
            graph.ainvoke(
                {
                    "user_query": "预测未来需求",
                    "task_mode": "forecast_report",
                    "execution_trace": [],
                    "status": "running",
                }
            )
        )

        self.assertEqual(
            result["status"],
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
            result["forecast_thread_id"],
            "test-thread-001",
        )

        self.assertEqual(
            result["execution_trace"],
            ["forecast"],
        )

        self.assertNotIn(
            "report",
            result["execution_trace"],
        )

        self.assertNotIn(
            "report_result",
            result,
        )

    def test_invalid_task_mode(self):
        graph = self._build_graph()

        result = asyncio.run(
            graph.ainvoke(
                {
                    "user_query": "未知任务",
                    "task_mode": "unknown",
                    "execution_trace": [],
                    "status": "running",
                }
            )
        )

        self.assertEqual(
            result["status"],
            "failed",
        )

        self.assertIn(
            "不支持的 task_mode",
            result["report_error"],
        )


if __name__ == "__main__":
    unittest.main()