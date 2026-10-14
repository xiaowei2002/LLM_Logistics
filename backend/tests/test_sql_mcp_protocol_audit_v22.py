
"""
SQL V2.2：MCP 工具返回协议审计。

真实组件：
- build_readonly_sql_tools()
- validate_readonly_sql()
- LangChain StructuredTool
- LangChain ToolMessage

模拟组件：
- 原始 MySQL MCP execute_sql 工具

审计项目：
MP201 artifact 保留
MP202 原始工具错误状态传播
MP203 artifact.isError 错误传播
MP204 原始工具异常传播
MP205 正常 SELECT
MP206 危险 SQL 拦截

首次运行允许 FAIL，用于记录修复前缺陷。

运行：
python -m unittest tests.test_sql_mcp_protocol_audit_v22 -v

不访问真实 LLM、MCP 或 MySQL。
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from langchain_core.messages import ToolMessage
from langchain_core.tools import (
    StructuredTool,
    ToolException,
)

from core.tools.sql_mcp_guard import (
    build_readonly_sql_tools,
)


class SQLMCPProtocolAuditV22Test(
    unittest.IsolatedAsyncioTestCase
):
    """
    用真实 LangChain 工具执行机制，
    模拟 MySQL MCP 的不同返回状态。
    """

    def setUp(self):
        self.raw_execution = AsyncMock()

    def make_guarded_tool(
        self,
        *,
        raw_mode: str,
    ):
        """
        创建使用 content_and_artifact 的模拟 MCP 工具。

        raw_mode:
            success
            tool_error
            artifact_error
            exception
        """

        async def simulated_execute_sql(
            query: str,
        ):
            """模拟原始 MCP SQL 工具。"""

            await self.raw_execution(query)

            if raw_mode == "success":
                return (
                    [
                        {
                            "type": "text",
                            "text": "查询完成",
                        }
                    ],
                    {
                        "isError": False,
                        "rows": [
                            {"quantity": 42}
                        ],
                    },
                )

            if raw_mode == "tool_error":
                raise ToolException(
                    "数据库连接失败"
                )

            if raw_mode == "artifact_error":
                return (
                    [
                        {
                            "type": "text",
                            "text": "查询失败",
                        }
                    ],
                    {
                        "isError": True,
                        "error": "表不存在",
                    },
                )

            if raw_mode == "exception":
                raise RuntimeError(
                    "模拟 MCP 执行异常"
                )

            raise AssertionError(
                "未知测试模式：" + raw_mode
            )

        raw_tool = StructuredTool.from_function(
            coroutine=simulated_execute_sql,
            name="execute_sql",
            description="模拟原始 MySQL MCP 工具。",
            response_format="content_and_artifact",
        )

        # LangChain 将 ToolException 转成错误 ToolMessage，
        # 以模拟底层工具已捕获并返回错误状态。
        raw_tool.handle_tool_error = True

        guarded = build_readonly_sql_tools(
            [raw_tool]
        )

        self.assertEqual(
            len(guarded),
            1,
        )

        self.assertIsNot(
            guarded[0],
            raw_tool,
        )

        return guarded[0]

    async def call_as_tool(
        self,
        tool,
        query: str,
    ) -> ToolMessage:
        """
        使用真实 LangChain tool_call 输入形式，
        从而可以观察状态与 artifact。
        """

        result = await tool.ainvoke(
            {
                "type": "tool_call",
                "id": "outer-call-001",
                "name": "execute_sql",
                "args": {
                    "query": query,
                },
            }
        )

        self.assertIsInstance(
            result,
            ToolMessage,
        )

        self.assertEqual(
            result.tool_call_id,
            "outer-call-001",
        )

        return result

    async def test_mp201_preserve_artifact(self):
        """成功查询必须保留结构化结果。"""

        tool = self.make_guarded_tool(
            raw_mode="success"
        )

        result = await self.call_as_tool(
            tool,
            "SELECT quantity FROM inventory",
        )

        self.assertEqual(
            result.status,
            "success",
        )

        self.assertIsInstance(
            result.artifact,
            dict,
            "包装后的 artifact 丢失。",
        )

        self.assertEqual(
            result.artifact["rows"],
            [{"quantity": 42}],
        )

        self.raw_execution.assert_awaited_once_with(
            "SELECT quantity FROM inventory"
        )

    async def test_mp202_propagate_tool_error(self):
        """原始工具错误不得变成包装工具成功。"""

        tool = self.make_guarded_tool(
            raw_mode="tool_error"
        )

        try:
            result = await self.call_as_tool(
                tool,
                "SELECT quantity FROM inventory",
            )
        except Exception:
            # 明确抛出异常也属于拒绝成功。
            self.raw_execution.assert_awaited_once()
            return

        self.assertEqual(
            result.status,
            "error",
            "原始工具失败却被包装成成功。",
        )

        self.raw_execution.assert_awaited_once()

    async def test_mp203_propagate_artifact_error(self):
        """artifact.isError=True 必须拒绝成功。"""

        tool = self.make_guarded_tool(
            raw_mode="artifact_error"
        )

        try:
            result = await self.call_as_tool(
                tool,
                "SELECT quantity FROM inventory",
            )
        except Exception:
            self.raw_execution.assert_awaited_once()
            return

        self.assertEqual(
            result.status,
            "error",
            "原始 artifact 已报告失败，"
            "但包装工具仍标记成功。",
        )

        self.raw_execution.assert_awaited_once()

    async def test_mp204_exception_not_success(self):
        """原始工具抛出异常不能伪装成成功。"""

        tool = self.make_guarded_tool(
            raw_mode="exception"
        )

        try:
            result = await self.call_as_tool(
                tool,
                "SELECT quantity FROM inventory",
            )
        except RuntimeError as exc:
            self.assertIn(
                "模拟 MCP 执行异常",
                str(exc),
            )
            self.raw_execution.assert_awaited_once()
            return

        self.assertEqual(
            result.status,
            "error",
        )

        self.raw_execution.assert_awaited_once()

    async def test_mp205_valid_select(self):
        """合法 SELECT 应正常转发。"""

        tool = self.make_guarded_tool(
            raw_mode="success"
        )

        sql = (
            "SELECT sku, quantity "
            "FROM inventory LIMIT 10"
        )

        result = await self.call_as_tool(
            tool,
            sql,
        )

        self.assertEqual(
            result.status,
            "success",
        )

        self.raw_execution.assert_awaited_once_with(
            sql
        )

    async def test_mp206_delete_never_forwarded(self):
        """危险 SQL 不得到达原始 MCP。"""

        tool = self.make_guarded_tool(
            raw_mode="success"
        )

        try:
            result = await self.call_as_tool(
                tool,
                "DELETE FROM inventory",
            )
        except Exception:
            self.raw_execution.assert_not_awaited()
            return

        self.assertEqual(
            result.status,
            "error",
        )

        self.raw_execution.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
