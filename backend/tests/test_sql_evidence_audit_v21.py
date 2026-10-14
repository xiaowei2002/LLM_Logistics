
"""
SQL V2.1：工具执行证据链完整性审计。

目标：
检测 SQLAgent 在工具消息不完整、顺序异常、
其他工具失败等情况下是否错误报告成功。

注意：
本文件是修复前审计测试。
首次运行允许出现 FAIL。
不得为了让测试通过而放宽预期断言。

运行：
    python -m unittest tests.test_sql_evidence_audit_v21 -v
"""

from __future__ import annotations

import unittest

from langchain_core.messages import AIMessage, ToolMessage

from core.tools.sql_evidence import inspect_sql_evidence


def call(
    name: str = "execute_sql",
    call_id: str = "q1",
) -> AIMessage:
    if name == "execute_sql":
        args = {"query": "SELECT quantity FROM inventory"}
    else:
        args = {"table_name": "inventory"}

    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": name,
                "args": args,
                "id": call_id,
                "type": "tool_call",
            }
        ],
    )


def response(
    *,
    name: str = "execute_sql",
    call_id: str = "q1",
    content: str = '[{"quantity":42}]',
    status: str = "success",
) -> ToolMessage:
    return ToolMessage(
        content=content,
        name=name,
        tool_call_id=call_id,
        status=status,
    )


def answer(text: str = "查询结果为42") -> AIMessage:
    return AIMessage(content=text)


class SQLEvidenceAuditV21Test(unittest.TestCase):

    def assert_rejected(self, messages):
        result = inspect_sql_evidence(messages)

        self.assertEqual(
            result["status"],
            "error",
            msg=f"异常执行链被错误接受：{result}",
        )

        self.assertIs(
            result["query_verified"],
            False,
        )

        self.assertTrue(
            str(result.get("error") or "").strip()
        )

    def assert_accepted(self, messages):
        result = inspect_sql_evidence(messages)

        self.assertEqual(
            result["status"],
            "success",
            msg=f"有效执行链被拒绝：{result}",
        )

        self.assertIs(
            result["query_verified"],
            True,
        )

        self.assertEqual(
            result["tool_name"],
            "execute_sql",
        )

    def test_ea201_orphan_tool_result(self):
        """有孤立工具结果，即使 SQL 成功也应拒绝。"""
        messages = [
            response(
                name="get_schema_info",
                call_id="orphan",
                content="columns: sku, quantity",
            ),
            call(),
            response(),
            answer(),
        ]

        self.assert_rejected(messages)

    def test_ea202_sql_call_id_mismatch(self):
        """SQL 工具调用与工具返回 ID 不一致。"""
        messages = [
            call(call_id="q1"),
            response(call_id="q2"),
            answer(),
        ]

        self.assert_rejected(messages)

    def test_ea203_metadata_tool_error(self):
        """元数据失败不能被后续 SQL 成功掩盖。"""
        messages = [
            call(
                name="get_schema_info",
                call_id="m1",
            ),
            response(
                name="get_schema_info",
                call_id="m1",
                content="Error: schema unavailable",
                status="error",
            ),
            call(),
            response(),
            answer(),
        ]

        self.assert_rejected(messages)

    def test_ea204_result_before_call(self):
        """ToolMessage 不得先于对应的工具调用。"""
        messages = [
            response(),
            call(),
            answer(),
        ]

        self.assert_rejected(messages)

    def test_ea205_missing_metadata_result(self):
        """任何已发出的工具调用都必须有对应结果。"""
        messages = [
            call(
                name="get_schema_info",
                call_id="m1",
            ),
            call(),
            response(),
            answer(),
        ]

        self.assert_rejected(messages)

    def test_ea206_duplicate_tool_result(self):
        """重复工具返回不能被接受。"""
        messages = [
            call(),
            response(),
            response(),
            answer(),
        ]

        self.assert_rejected(messages)

    def test_ea207_tool_name_mismatch(self):
        """ToolMessage 名称须与原调用名称一致。"""
        messages = [
            call(),
            response(
                name="get_schema_info",
            ),
            answer(),
        ]

        self.assert_rejected(messages)

    def test_ea208_later_tool_failure(self):
        """SQL 成功后其他工具失败，不能判整体成功。"""
        messages = [
            call(),
            response(),
            call(
                name="get_table_sample",
                call_id="s1",
            ),
            response(
                name="get_table_sample",
                call_id="s1",
                content="Error: connection lost",
                status="error",
            ),
            answer(),
        ]

        self.assert_rejected(messages)

    def test_ea209_metadata_only(self):
        """仅获取表结构，不代表完成数据查询。"""
        messages = [
            call(
                name="get_schema_info",
                call_id="m1",
            ),
            response(
                name="get_schema_info",
                call_id="m1",
                content="columns: sku, quantity",
            ),
            answer(),
        ]

        self.assert_rejected(messages)

    def test_ea210_valid_complete_chain(self):
        """完整、成功的元数据加 SQL 查询执行链。"""
        messages = [
            call(
                name="get_schema_info",
                call_id="m1",
            ),
            response(
                name="get_schema_info",
                call_id="m1",
                content="columns: sku, quantity",
            ),
            call(),
            response(),
            answer(),
        ]

        self.assert_accepted(messages)

    def test_ea211_nested_mcp_error(self):
        """模拟 MCP 文本块中的结构化失败信息。"""
        messages = [
            call(),
            response(
                content=(
                    '[{"type":"text","text":'
                    '"Error: database connection failed"}]'
                ),
            ),
            answer("查询结果为42"),
        ]

        self.assert_rejected(messages)

    def test_ea212_premature_final_answer(self):
        """最终回答不得出现在工具返回之前。"""
        messages = [
            call(),
            answer("查询结果为42"),
            response(),
            answer("查询完成"),
        ]

        self.assert_rejected(messages)


if __name__ == "__main__":
    unittest.main()
