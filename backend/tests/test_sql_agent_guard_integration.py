
"""
SQLAgent read-only MCP integration tests.

Real components:
- SQLAgent.ensure_agent()
- build_readonly_sql_tools()
- validate_readonly_sql()

Mocked components:
- LLM
- MCP tools
- LangChain Agent construction

No real MySQL queries.

Run:
python -m tests.test_sql_agent_guard_integration
"""

import unittest
from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)

from langchain_core.tools import StructuredTool

from core.agents.sql_agent import SQLAgent
from core.tools.sql_mcp_guard import (
    SQLMCPToolConfigurationError,
)
from core.tools.sql_readonly_guard import (
    SQLReadOnlyViolation,
)


class SQLAgentGuardIntegrationTest(
    unittest.IsolatedAsyncioTestCase
):

    def setUp(self):
        client = MagicMock()
        client.model = MagicMock()

        self.agent = SQLAgent(client=client)

        self.database_execute = AsyncMock(
            return_value="模拟查询成功"
        )

        async def raw_execute(
            query: str,
        ) -> str:
            """Simulated SQL execution."""
            return await self.database_execute(
                query
            )

        async def schema_info(
            table_name: str = "",
        ) -> str:
            """Simulated metadata."""
            return "模拟字段"

        async def table_sample(
            table_name: str,
            limit: int = 5,
        ) -> str:
            """Simulated table rows."""
            return "模拟样本"

        self.raw_execute = StructuredTool.from_function(
            coroutine=raw_execute,
            name="execute_sql",
            description="Original SQL execution tool.",
        )

        self.schema_tool = StructuredTool.from_function(
            coroutine=schema_info,
            name="get_schema_info",
            description="Schema metadata.",
        )

        self.sample_tool = StructuredTool.from_function(
            coroutine=table_sample,
            name="get_table_sample",
            description="Sample rows.",
        )

        self.agent.mcp = MagicMock()

        self.agent.mcp.get_tools = AsyncMock(
            return_value=[
                self.raw_execute,
                self.schema_tool,
                self.sample_tool,
            ]
        )

    async def test_agent_uses_guarded_execute_sql(self):
        fake_langchain_agent = MagicMock()

        with patch(
            "core.agents.sql_agent.create_agent",
            return_value=fake_langchain_agent,
        ) as create_mock:
            result = await self.agent.ensure_agent()

        self.assertIs(
            result,
            fake_langchain_agent,
        )

        create_mock.assert_called_once()

        guarded_tools = (
            create_mock.call_args.kwargs["tools"]
        )

        self.assertEqual(
            len(guarded_tools),
            3,
        )

        guarded_execute = next(
            tool
            for tool in guarded_tools
            if tool.name == "execute_sql"
        )

        self.assertIsNot(
            guarded_execute,
            self.raw_execute,
        )

    async def test_valid_select_reaches_mock_mcp(self):
        with patch(
            "core.agents.sql_agent.create_agent",
            return_value=MagicMock(),
        ) as create_mock:
            await self.agent.ensure_agent()

        tools = create_mock.call_args.kwargs["tools"]

        execute = next(
            tool
            for tool in tools
            if tool.name == "execute_sql"
        )

        sql = "SELECT sku FROM inventory LIMIT 10"

        result = await execute.ainvoke({
            "query": sql
        })

        self.assertEqual(
            result,
            "模拟查询成功",
        )

        self.database_execute.assert_awaited_once_with(
            sql
        )

    async def test_delete_never_reaches_mock_mcp(self):
        with patch(
            "core.agents.sql_agent.create_agent",
            return_value=MagicMock(),
        ) as create_mock:
            await self.agent.ensure_agent()

        tools = create_mock.call_args.kwargs["tools"]

        execute = next(
            tool
            for tool in tools
            if tool.name == "execute_sql"
        )

        with self.assertRaises(
            SQLReadOnlyViolation
        ):
            await execute.ainvoke({
                "query": "DELETE FROM inventory"
            })

        self.database_execute.assert_not_awaited()

    async def test_unknown_tool_fails_closed(self):
        async def unsafe_admin(
            query: str,
        ) -> str:
            """Simulated unreviewed tool."""
            return "executed"

        unknown = StructuredTool.from_function(
            coroutine=unsafe_admin,
            name="admin_execute",
            description="Unreviewed administrative tool.",
        )

        self.agent.mcp.get_tools = AsyncMock(
            return_value=[
                self.raw_execute,
                unknown,
            ]
        )

        with patch(
            "core.agents.sql_agent.create_agent",
        ) as create_mock:
            with self.assertRaises(
                SQLMCPToolConfigurationError
            ):
                await self.agent.ensure_agent()

        create_mock.assert_not_called()

        self.assertIsNone(
            self.agent.agent
        )

    async def test_agent_is_cached_once(self):
        fake_langchain_agent = MagicMock()

        with patch(
            "core.agents.sql_agent.create_agent",
            return_value=fake_langchain_agent,
        ) as create_mock:
            first = await self.agent.ensure_agent()
            second = await self.agent.ensure_agent()

        self.assertIs(first, second)

        create_mock.assert_called_once()

        self.agent.mcp.get_tools.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()