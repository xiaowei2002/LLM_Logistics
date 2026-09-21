"""
SQL Node 单元测试。

使用 Fake SQLAgent 验证：
user_query -> sql_result / sql_status / execution_trace
"""

import asyncio
import sys
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from core.graph.nodes.sql_node import build_sql_node  # noqa: E402


class FakeSQLAgent:
    def __init__(
        self,
        result: str = "查询成功",
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.last_question: str | None = None

    async def query(self, question: str) -> str:
        self.last_question = question

        if self.error is not None:
            raise self.error

        return self.result


class SQLNodeTest(unittest.TestCase):

    def test_sql_node_success(self):
        agent = FakeSQLAgent(
            result="已查询到订单数据。"
        )

        node = build_sql_node(agent)

        result = asyncio.run(
            node(
                {
                    "user_query": "查询最近订单",
                    "execution_trace": [],
                }
            )
        )

        self.assertEqual(
            agent.last_question,
            "查询最近订单",
        )

        self.assertEqual(
            result["sql_status"],
            "success",
        )

        self.assertEqual(
            result["sql_result"],
            "已查询到订单数据。",
        )

        self.assertEqual(
            result["status"],
            "running",
        )

        self.assertEqual(
            result["execution_trace"],
            ["sql"],
        )

    def test_sql_node_empty_query(self):
        agent = FakeSQLAgent()

        node = build_sql_node(agent)

        result = asyncio.run(
            node(
                {
                    "user_query": "",
                    "execution_trace": [],
                }
            )
        )

        self.assertEqual(
            result["sql_status"],
            "error",
        )

        self.assertEqual(
            result["status"],
            "failed",
        )

        self.assertEqual(
            result["execution_trace"],
            ["sql"],
        )

    def test_sql_node_empty_result(self):
        agent = FakeSQLAgent(
            result=""
        )

        node = build_sql_node(agent)

        result = asyncio.run(
            node(
                {
                    "user_query": "查询订单",
                    "execution_trace": [],
                }
            )
        )

        self.assertEqual(
            result["sql_status"],
            "error",
        )

        self.assertEqual(
            result["status"],
            "failed",
        )

    def test_sql_node_exception(self):
        agent = FakeSQLAgent(
            error=RuntimeError(
                "数据库连接失败"
            )
        )

        node = build_sql_node(agent)

        result = asyncio.run(
            node(
                {
                    "user_query": "查询订单",
                    "execution_trace": [],
                }
            )
        )

        self.assertEqual(
            result["sql_status"],
            "error",
        )

        self.assertEqual(
            result["sql_error"],
            "数据库连接失败",
        )

        self.assertEqual(
            result["status"],
            "failed",
        )


if __name__ == "__main__":
    unittest.main()