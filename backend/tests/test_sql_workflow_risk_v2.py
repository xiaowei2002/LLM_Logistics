
"""
SQL V2 工作流级风险验证。

测试真实组件：
- build_sql_node()
- build_logistics_workflow()
- SQL -> Report 的 LangGraph 路由

模拟组件：
- SQLAgent 查询证据
- Report Node
- Cleaning / Forecast Node

不连接真实 LLM、MCP 或 MySQL。

运行：
    python -m tests.test_sql_workflow_risk_v2
"""

from __future__ import annotations

import asyncio
import unittest
from typing import Any

from core.graph.nodes.sql_node import build_sql_node
from core.graph.workflow import build_logistics_workflow


def verified_success() -> dict[str, Any]:
    return {
        "status": "success",
        "answer": "查询到订单数量为42。",
        "query_verified": True,
        "tool_name": "execute_sql",
        "tool_call_id": "sql-call-001",
        "error": None,
    }


def verified_failure(
    error: str,
    answer: str = "",
) -> dict[str, Any]:
    return {
        "status": "error",
        "answer": answer,
        "query_verified": False,
        "tool_name": None,
        "tool_call_id": None,
        "error": error,
    }


class FakeEvidenceSQLAgent:
    """模拟结构化 SQLAgent。"""

    def __init__(
        self,
        response: dict[str, Any],
    ) -> None:
        self.response = response
        self.call_count = 0

    async def query_with_evidence(
        self,
        question: str,
    ) -> dict[str, Any]:
        self.call_count += 1
        return dict(self.response)


class FakeLegacySQLAgent:
    """只提供历史字符串接口。"""

    def __init__(self) -> None:
        self.call_count = 0

    async def query(
        self,
        question: str,
    ) -> str:
        self.call_count += 1
        return "订单数量为42"


class ReportCallRecorder:
    """记录 Report Node 是否实际执行。"""

    def __init__(self) -> None:
        self.call_count = 0

    async def __call__(
        self,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        self.call_count += 1

        return {
            "current_agent": "report",
            "report_result": "模拟查询报告",
            "report_status": "success",
            "report_error": None,
            "status": "completed",
            "execution_trace": ["report"],
        }


async def unused_cleaning_node(
    state: dict[str, Any],
) -> dict[str, Any]:
    raise AssertionError(
        "SQL 工作流不应执行 Cleaning Node"
    )


async def unused_forecast_node(
    state: dict[str, Any],
) -> dict[str, Any]:
    raise AssertionError(
        "SQL 工作流不应执行 Forecast Node"
    )


class SQLWorkflowRiskV2Test(unittest.TestCase):
    """SQL 查询证据对外层工作流的影响。"""

    def run_workflow(
        self,
        agent: Any,
    ) -> tuple[
        dict[str, Any],
        ReportCallRecorder,
    ]:
        report = ReportCallRecorder()

        graph = build_logistics_workflow(
            sql_node=build_sql_node(agent),
            cleaning_node=unused_cleaning_node,
            forecast_node=unused_forecast_node,
            report_node=report,
        )

        result = asyncio.run(
            graph.ainvoke(
                {
                    "user_query": "查询订单数量",
                    "task_mode": "sql_report",
                    "status": "running",
                    "execution_trace": [],
                }
            )
        )

        return result, report

    def assert_stopped_before_report(
        self,
        result: dict[str, Any],
        report: ReportCallRecorder,
    ) -> None:
        self.assertEqual(
            result["status"],
            "failed",
        )

        self.assertEqual(
            result["sql_status"],
            "error",
        )

        self.assertEqual(
            result["sql_result"],
            "",
        )

        self.assertEqual(
            report.call_count,
            0,
        )

        self.assertEqual(
            result["execution_trace"],
            ["sql"],
        )

        self.assertNotIn(
            "report",
            result["execution_trace"],
        )

        self.assertNotEqual(
            result.get("report_status"),
            "success",
        )

        self.assertFalse(
            result.get("report_result")
        )

    def test_sw201_verified_success_reaches_report(
        self,
    ):
        agent = FakeEvidenceSQLAgent(
            verified_success()
        )

        result, report = self.run_workflow(
            agent
        )

        self.assertEqual(
            agent.call_count,
            1,
        )

        self.assertEqual(
            result["sql_status"],
            "success",
        )

        self.assertEqual(
            result["status"],
            "completed",
        )

        self.assertEqual(
            result["sql_result"],
            "查询到订单数量为42。",
        )

        self.assertEqual(
            report.call_count,
            1,
        )

        self.assertEqual(
            result["report_status"],
            "success",
        )

        self.assertEqual(
            result["execution_trace"],
            ["sql", "report"],
        )

    def test_sw202_connection_error_stops(
        self,
    ):
        agent = FakeEvidenceSQLAgent(
            verified_failure(
                "数据库连接失败",
                "查询失败，请稍后重试。",
            )
        )

        result, report = self.run_workflow(
            agent
        )

        self.assert_stopped_before_report(
            result,
            report,
        )

        self.assertIn(
            "数据库连接失败",
            result["sql_error"],
        )

    def test_sw203_missing_table_stops(
        self,
    ):
        agent = FakeEvidenceSQLAgent(
            verified_failure(
                "Table 'erp.orders' doesn't exist",
                "未找到订单数据。",
            )
        )

        result, report = self.run_workflow(
            agent
        )

        self.assert_stopped_before_report(
            result,
            report,
        )

        self.assertIn(
            "doesn't exist",
            result["sql_error"],
        )

    def test_sw204_no_tool_evidence_stops(
        self,
    ):
        agent = FakeEvidenceSQLAgent(
            verified_failure(
                "未发现 execute_sql 工具调用证据。",
                "订单数量为42。",
            )
        )

        result, report = self.run_workflow(
            agent
        )

        self.assert_stopped_before_report(
            result,
            report,
        )

        self.assertIn(
            "execute_sql",
            result["sql_error"],
        )

    def test_sw205_legacy_string_agent_stops(
        self,
    ):
        agent = FakeLegacySQLAgent()

        result, report = self.run_workflow(
            agent
        )

        self.assert_stopped_before_report(
            result,
            report,
        )

        # 禁止调用没有证据接口的旧 Agent。
        self.assertEqual(
            agent.call_count,
            0,
        )


if __name__ == "__main__":
    unittest.main()
