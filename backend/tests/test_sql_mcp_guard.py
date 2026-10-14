
"""
SQL V2-B1: MCP wrapper unit tests.

No real LLM, MCP server or database is used.

Run:
    python -m tests.test_sql_mcp_guard
"""

import unittest
from unittest.mock import AsyncMock

from langchain_core.tools import (
    StructuredTool,
)

from core.tools.sql_mcp_guard import (
    SQLMCPToolConfigurationError,
    build_readonly_sql_tools,
)
from core.tools.sql_readonly_guard import (
    SQLReadOnlyViolation,
)


class SQLMCPGuardTest(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        self.database_execute = AsyncMock(
            return_value="模拟查询结果：42"
        )

        async def fake_execute_sql(
            query: str,
        ) -> str:
            """Simulated original database execution."""
            return await self.database_execute(
                query
            )

        async def fake_schema(
            table_name: str = "",
        ) -> str:
            """Simulated schema inspection."""
            return "模拟表结构"

        async def fake_sample(
            table_name: str,
            limit: int = 5,
        ) -> str:
            """Simulated table sample."""
            return "模拟数据样本"

        self.raw_execute_tool = (
            StructuredTool.from_function(
                coroutine=fake_execute_sql,
                name="execute_sql",
                description="Raw simulated SQL tool.",
            )
        )

        self.schema_tool = (
            StructuredTool.from_function(
                coroutine=fake_schema,
                name="get_schema_info",
                description="Schema tool.",
            )
        )

        self.sample_tool = (
            StructuredTool.from_function(
                coroutine=fake_sample,
                name="get_table_sample",
                description="Sample tool.",
            )
        )

        self.raw_tools = [
            self.raw_execute_tool,
            self.schema_tool,
            self.sample_tool,
        ]

    def get_guarded_execute(self):
        tools = build_readonly_sql_tools(
            self.raw_tools
        )
        return next(
            tool for tool in tools
            if tool.name == "execute_sql"
        )

    async def test_wrapped_names(self):
        tools = build_readonly_sql_tools(
            self.raw_tools
        )

        self.assertEqual(
            [tool.name for tool in tools],
            [
                "execute_sql",
                "get_schema_info",
                "get_table_sample",
            ],
        )

        self.assertIsNot(
            tools[0],
            self.raw_execute_tool,
        )

        self.assertIs(
            tools[1],
            self.schema_tool,
        )

        self.assertIs(
            tools[2],
            self.sample_tool,
        )

    async def test_valid_select_forwarded(self):
        tool = self.get_guarded_execute()

        sql = (
            "SELECT quantity FROM inventory "
            "WHERE sku = 'SKU-001' LIMIT 10"
        )

        result = await tool.ainvoke({
            "query": sql,
        })

        self.assertEqual(
            result,
            "模拟查询结果：42",
        )

        self.database_execute.assert_awaited_once_with(
            sql
        )

    async def test_delete_never_reaches_database(self):
        tool = self.get_guarded_execute()

        with self.assertRaises(
            SQLReadOnlyViolation
        ):
            await tool.ainvoke({
                "query": "DELETE FROM inventory"
            })

        self.database_execute.assert_not_awaited()

    async def test_update_never_reaches_database(self):
        tool = self.get_guarded_execute()

        with self.assertRaises(
            SQLReadOnlyViolation
        ):
            await tool.ainvoke({
                "query": (
                    "UPDATE inventory "
                    "SET quantity = 99"
                )
            })

        self.database_execute.assert_not_awaited()

    async def test_multi_statement_rejected(self):
        tool = self.get_guarded_execute()

        with self.assertRaises(
            SQLReadOnlyViolation
        ):
            await tool.ainvoke({
                "query": (
                    "SELECT * FROM inventory; "
                    "DROP TABLE inventory"
                )
            })

        self.database_execute.assert_not_awaited()

    async def test_unknown_tool_rejected(self):
        async def fake_admin(
            query: str,
        ) -> str:
            """Simulated administrative command."""
            return "ok"

        unknown = StructuredTool.from_function(
            coroutine=fake_admin,
            name="admin_execute",
            description="Unknown tool.",
        )

        with self.assertRaises(
            SQLMCPToolConfigurationError
        ):
            build_readonly_sql_tools(
                self.raw_tools + [unknown]
            )

    async def test_duplicate_tool_rejected(self):
        with self.assertRaises(
            SQLMCPToolConfigurationError
        ):
            build_readonly_sql_tools(
                self.raw_tools
                + [self.raw_execute_tool]
            )

    async def test_missing_execute_rejected(self):
        with self.assertRaises(
            SQLMCPToolConfigurationError
        ):
            build_readonly_sql_tools([
                self.schema_tool,
                self.sample_tool,
            ])

    async def test_schema_tool_preserved(self):
        tools = build_readonly_sql_tools(
            self.raw_tools
        )

        schema_tool = next(
            tool for tool in tools
            if tool.name == "get_schema_info"
        )

        result = await schema_tool.ainvoke({
            "table_name": "inventory"
        })

        self.assertEqual(
            result,
            "模拟表结构",
        )

        self.database_execute.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()