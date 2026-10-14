
"""
SQL V2.3：真实 MCP 错误文本识别回归测试。

目标：
1. 拒绝 status=success 但 content 为执行错误的响应。
2. 覆盖真实 MySQL Access denied 错误格式。
3. 正常 SQL 查询结果仍应成功。
4. 正常数据中的 error 文本不应误判。
5. 保留原有 artifact。
6. 不连接真实 LLM、MCP 或 MySQL。

运行：
python -m unittest tests.test_sql_mcp_error_text_v23 -v
"""

from __future__ import annotations

import unittest

from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool

from core.tools.sql_mcp_guard import (
    SQLMCPExecutionError,
    _has_error_flag,
    _validate_mcp_response,
    build_readonly_sql_tools,
)


class SQLMCPErrorTextUnitTest(unittest.TestCase):

    def test_real_access_denied_text_detected(self):
        content = [
            {
                "type": "text",
                "text": (
                    "Error executing query: "
                    "Access denied for user "
                    "'olist_eval_reader'@'client-ip' "
                    "(using password: YES)"
                ),
            }
        ]

        self.assertTrue(
            _has_error_flag(content)
        )

    def test_case_and_whitespace_detected(self):
        content = [
            {
                "type": "text",
                "text": (
                    " \n  ERROR EXECUTING QUERY: "
                    "Unknown table"
                ),
            }
        ]

        self.assertTrue(
            _has_error_flag(content)
        )

    def test_normal_business_text_is_not_error(self):
        content = [
            {
                "type": "text",
                "text": (
                    "message\n"
                    "Error executing query: "
                    "is the name of a log category"
                ),
            }
        ]

        self.assertFalse(
            _has_error_flag(content)
        )

    def test_normal_query_response_is_not_error(self):
        content = [
            {
                "type": "text",
                "text": "total_orders\n99441",
            }
        ]

        self.assertFalse(
            _has_error_flag(content)
        )

    def test_real_error_response_rejected(self):
        call_id = "test-inner-call"

        message = ToolMessage(
            content=[
                {
                    "type": "text",
                    "text": (
                        "Error executing query: "
                        "Access denied for user"
                    ),
                }
            ],
            tool_call_id=call_id,
            name="execute_sql",
            status="success",
        )

        with self.assertRaises(
            SQLMCPExecutionError
        ):
            _validate_mcp_response(
                message,
                expected_call_id=call_id,
            )


class SQLMCPErrorTextIntegrationTest(
    unittest.IsolatedAsyncioTestCase
):

    def make_tool(self, *, error: bool):
        async def raw_execute_sql(query: str):
            if error:
                return (
                    [
                        {
                            "type": "text",
                            "text": (
                                "Error executing query: "
                                "Access denied for user"
                            ),
                        }
                    ],
                    None,
                )

            return (
                [
                    {
                        "type": "text",
                        "text": "total_orders\n99441",
                    }
                ],
                {
                    "rows": [
                        {"total_orders": 99441}
                    ]
                },
            )

        raw_tool = StructuredTool.from_function(
            coroutine=raw_execute_sql,
            name="execute_sql",
            description="Mock MySQL MCP tool.",
            response_format="content_and_artifact",
        )

        return build_readonly_sql_tools(
            [raw_tool]
        )[0]

    async def test_wrapper_rejects_real_error_text(self):
        tool = self.make_tool(
            error=True
        )

        # LangChain may propagate the exception or,
        # if error handling is configured, return
        # a ToolMessage with status=error.
        try:
            result = await tool.ainvoke({
                "type": "tool_call",
                "id": "outer-test-error",
                "name": "execute_sql",
                "args": {
                    "query": (
                        "SELECT COUNT(*) "
                        "FROM orders"
                    ),
                },
            })
        except SQLMCPExecutionError:
            return

        self.assertIsInstance(
            result,
            ToolMessage,
        )

        self.assertEqual(
            result.status,
            "error",
            "Error executing query was treated as success.",
        )

    async def test_wrapper_preserves_success(self):
        tool = self.make_tool(
            error=False
        )

        result = await tool.ainvoke({
            "type": "tool_call",
            "id": "outer-test-success",
            "name": "execute_sql",
            "args": {
                "query": (
                    "SELECT COUNT(*) "
                    "FROM orders"
                ),
            },
        })

        self.assertIsInstance(
            result,
            ToolMessage,
        )

        self.assertEqual(
            result.status,
            "success",
        )

        self.assertIn(
            "99441",
            str(result.content),
        )

        self.assertEqual(
            result.artifact["rows"],
            [{"total_orders": 99441}],
        )


if __name__ == "__main__":
    unittest.main()
