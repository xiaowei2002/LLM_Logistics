
"""
SQLAgent.query_with_evidence() 测试。

不调用真实 LLM、MCP 或数据库。

运行：
python -m tests.test_sql_agent_evidence
"""

import unittest
from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)

from langchain_core.messages import (
    AIMessage,
    ToolMessage,
)

from core.agents.sql_agent import SQLAgent


def query_messages(
    *,
    tool_status="success",
    tool_content='[{"quantity":42}]',
    final_text="查询结果为42",
):
    return [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "execute_sql",
                    "args": {
                        "query": "SELECT quantity FROM inventory"
                    },
                    "id": "sql-call-1",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content=tool_content,
            name="execute_sql",
            tool_call_id="sql-call-1",
            status=tool_status,
        ),
        AIMessage(
            content=final_text
        ),
    ]


class SQLAgentEvidenceTest(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        client = MagicMock()
        client.model = MagicMock()
        self.agent = SQLAgent(client=client)

    async def run_with_messages(self, messages):
        fake_agent = MagicMock()
        fake_agent.ainvoke = AsyncMock(
            return_value={
                "messages": messages
            }
        )

        with patch.object(
            self.agent,
            "ensure_agent",
            new=AsyncMock(
                return_value=fake_agent
            ),
        ):
            return await (
                self.agent.query_with_evidence(
                    "查询库存数量"
                )
            )

    async def test_verified_success(self):
        result = await self.run_with_messages(
            query_messages()
        )

        self.assertEqual(
            result["status"],
            "success",
        )
        self.assertTrue(
            result["query_verified"]
        )

    async def test_tool_error(self):
        result = await self.run_with_messages(
            query_messages(
                tool_status="error",
                tool_content="数据库连接失败",
            )
        )

        self.assertEqual(
            result["status"],
            "error",
        )
        self.assertFalse(
            result["query_verified"]
        )

    async def test_structured_error(self):
        result = await self.run_with_messages(
            query_messages(
                tool_content=(
                    '{"status":"error",'
                    '"error":"表不存在"}'
                )
            )
        )

        self.assertEqual(
            result["status"],
            "error",
        )

    async def test_no_tool_evidence(self):
        result = await self.run_with_messages([
            AIMessage(
                content="库存数量为42"
            )
        ])

        self.assertEqual(
            result["status"],
            "error",
        )
        self.assertFalse(
            result["query_verified"]
        )

    async def test_empty_rows_success(self):
        result = await self.run_with_messages(
            query_messages(
                tool_content="[]",
                final_text="查询成功，结果为空",
            )
        )

        self.assertEqual(
            result["status"],
            "success",
        )

    async def test_empty_question(self):
        result = await (
            self.agent.query_with_evidence(" ")
        )

        self.assertEqual(
            result["status"],
            "error",
        )

    async def test_agent_exception(self):
        with patch.object(
            self.agent,
            "_invoke_agent",
            new=AsyncMock(
                side_effect=RuntimeError(
                    "模拟 MCP 连接失败"
                )
            ),
        ):
            result = await (
                self.agent.query_with_evidence(
                    "查询库存"
                )
            )

        self.assertEqual(
            result["status"],
            "error",
        )
        self.assertIn(
            "模拟 MCP 连接失败",
            result["error"],
        )


if __name__ == "__main__":
    unittest.main()
