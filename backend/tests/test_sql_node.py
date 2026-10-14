
"""
SQL Node V2 单元测试。

验证：
- 结构化工具执行证据成功
- 数据库连接失败
- 数据表不存在
- 无工具证据的自然语言回答
- 空任务、空回答、异常
- 错误结构、缺少工具调用标识
- 旧版 query() 字符串接口禁止回退

所有测试完全离线。
不调用真实 LLM、MCP 或 MySQL。

运行：
    python -m tests.test_sql_node
"""

import asyncio
import unittest

from core.graph.nodes.sql_node import build_sql_node


def verified_result(
    answer: str = "已查询到订单数据。",
) -> dict:
    """模拟 SQLAgent 已核验的成功结果。"""
    return {
        "status": "success",
        "answer": answer,
        "query_verified": True,
        "tool_name": "execute_sql",
        "tool_call_id": "sql-call-001",
        "error": None,
    }


def failed_result(
    error: str = "SQL 查询失败",
    answer: str = "",
) -> dict:
    """模拟 SQLAgent 查询失败结果。"""
    return {
        "status": "error",
        "answer": answer,
        "query_verified": False,
        "tool_name": None,
        "tool_call_id": None,
        "error": error,
    }


class FakeVerifiedSQLAgent:
    """具有 SQL V2 结构化接口的模拟 Agent。"""

    def __init__(
        self,
        result=None,
        error: Exception | None = None,
    ):
        self.result = (
            verified_result()
            if result is None
            else result
        )
        self.error = error
        self.last_question = None
        self.call_count = 0

    async def query_with_evidence(
        self,
        question: str,
    ) -> dict:
        self.last_question = question
        self.call_count += 1

        if self.error is not None:
            raise self.error

        return self.result


class FakeLegacySQLAgent:
    """旧版只返回字符串的 Agent。"""

    def __init__(self):
        self.called = False

    async def query(
        self,
        question: str,
    ) -> str:
        self.called = True
        return "未经验证的订单数量为42"


class SQLNodeTest(unittest.TestCase):

    @staticmethod
    def invoke(
        agent,
        user_query="查询最近订单",
    ):
        node = build_sql_node(agent)

        return asyncio.run(
            node(
                {
                    "user_query": user_query,
                    "execution_trace": [],
                    "status": "running",
                }
            )
        )

    def assert_failed(
        self,
        result,
    ):
        self.assertEqual(
            result["sql_status"],
            "error",
        )
        self.assertEqual(
            result["status"],
            "failed",
        )
        self.assertEqual(
            result["sql_result"],
            "",
        )
        self.assertEqual(
            result["execution_trace"],
            ["sql"],
        )

    # ------------------------------------------------------
    # 正常查询
    # ------------------------------------------------------

    def test_sql_node_success(self):
        agent = FakeVerifiedSQLAgent(
            result=verified_result(
                "已查询到订单数据。"
            )
        )

        result = self.invoke(
            agent,
            " 查询最近订单 ",
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
            result["sql_error"],
            None,
        )
        self.assertEqual(
            result["status"],
            "running",
        )
        self.assertEqual(
            result["execution_trace"],
            ["sql"],
        )

    # ------------------------------------------------------
    # 输入异常
    # ------------------------------------------------------

    def test_sql_node_empty_query(self):
        agent = FakeVerifiedSQLAgent()

        result = self.invoke(
            agent,
            "",
        )

        self.assert_failed(result)
        self.assertEqual(
            agent.call_count,
            0,
        )

    def test_sql_node_non_string_query(self):
        agent = FakeVerifiedSQLAgent()

        result = self.invoke(
            agent,
            None,
        )

        self.assert_failed(result)
        self.assertEqual(
            agent.call_count,
            0,
        )

    # ------------------------------------------------------
    # SQ005：数据库连接失败
    # ------------------------------------------------------

    def test_sq005_connection_error(self):
        agent = FakeVerifiedSQLAgent(
            result=failed_result(
                error="数据库连接失败",
                answer="查询失败，请稍后重试。",
            )
        )

        result = self.invoke(agent)

        self.assert_failed(result)
        self.assertIn(
            "数据库连接失败",
            result["sql_error"],
        )

    # ------------------------------------------------------
    # SQ006：数据库表不存在
    # ------------------------------------------------------

    def test_sq006_table_not_found(self):
        agent = FakeVerifiedSQLAgent(
            result=failed_result(
                error="Table 'erp.orders' doesn't exist",
                answer="未找到订单表。",
            )
        )

        result = self.invoke(agent)

        self.assert_failed(result)
        self.assertIn(
            "doesn't exist",
            result["sql_error"],
        )

    # ------------------------------------------------------
    # SQ007：没有工具证据却返回数字
    # ------------------------------------------------------

    def test_sq007_answer_without_evidence(self):
        agent = FakeVerifiedSQLAgent(
            result={
                "status": "error",
                "answer": "库存数量为42",
                "query_verified": False,
                "tool_name": None,
                "tool_call_id": None,
                "error": (
                    "未发现 execute_sql 工具调用证据。"
                ),
            }
        )

        result = self.invoke(agent)

        self.assert_failed(result)
        self.assertNotEqual(
            result["sql_result"],
            "库存数量为42",
        )

    # ------------------------------------------------------
    # 防御性结构校验
    # ------------------------------------------------------

    def test_success_without_verified_flag(self):
        data = verified_result()
        data["query_verified"] = False

        result = self.invoke(
            FakeVerifiedSQLAgent(data)
        )

        self.assert_failed(result)

    def test_success_without_tool_name(self):
        data = verified_result()
        data["tool_name"] = None

        result = self.invoke(
            FakeVerifiedSQLAgent(data)
        )

        self.assert_failed(result)

    def test_success_without_call_id(self):
        data = verified_result()
        data["tool_call_id"] = None

        result = self.invoke(
            FakeVerifiedSQLAgent(data)
        )

        self.assert_failed(result)

    def test_success_with_error(self):
        data = verified_result()
        data["error"] = "工具执行异常"

        result = self.invoke(
            FakeVerifiedSQLAgent(data)
        )

        self.assert_failed(result)

    def test_empty_answer(self):
        data = verified_result("")
        result = self.invoke(
            FakeVerifiedSQLAgent(data)
        )

        self.assert_failed(result)

    def test_non_dict_result(self):
        result = self.invoke(
            FakeVerifiedSQLAgent(
                result="查询成功"
            )
        )

        self.assert_failed(result)

    # ------------------------------------------------------
    # 执行异常
    # ------------------------------------------------------

    def test_sql_node_exception(self):
        agent = FakeVerifiedSQLAgent(
            error=RuntimeError(
                "数据库连接失败"
            )
        )

        result = self.invoke(agent)

        self.assert_failed(result)
        self.assertIn(
            "数据库连接失败",
            result["sql_error"],
        )

    # ------------------------------------------------------
    # 安全迁移：不回退旧字符串接口
    # ------------------------------------------------------

    def test_legacy_query_agent_rejected(self):
        agent = FakeLegacySQLAgent()

        result = self.invoke(agent)

        self.assert_failed(result)
        self.assertFalse(
            agent.called
        )
        self.assertIn(
            "结构化工具执行证据接口",
            result["sql_error"],
        )


if __name__ == "__main__":
    unittest.main()
