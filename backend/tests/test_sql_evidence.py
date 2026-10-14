
"""
SQL 执行证据测试。

运行：
python -m tests.test_sql_evidence
"""

import unittest

from langchain_core.messages import (
    AIMessage,
    ToolMessage,
)

from core.tools.sql_evidence import inspect_sql_evidence


def make_call(
    name="execute_sql",
    call_id="call_001",
):
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": name,
                "args": {
                    "query": "SELECT 42"
                },
                "id": call_id,
                "type": "tool_call",
            }
        ],
    )


def make_result(
    content='[{"quantity":42}]',
    name="execute_sql",
    call_id="call_001",
    status="success",
):
    return ToolMessage(
        content=content,
        name=name,
        tool_call_id=call_id,
        status=status,
    )


def final_answer(text="查询结果为42"):
    return AIMessage(content=text)


class SQLEvidenceTest(unittest.TestCase):

    def check(self, messages, expected):
        result = inspect_sql_evidence(messages)
        self.assertEqual(
            result["status"],
            expected,
        )
        self.assertEqual(
            result["query_verified"],
            expected == "success",
        )
        return result

    def test_successful_query(self):
        result = self.check(
            [
                make_call(),
                make_result(),
                final_answer(),
            ],
            "success",
        )
        self.assertEqual(
            result["tool_call_id"],
            "call_001",
        )

    def test_no_tool_call(self):
        self.check(
            [final_answer()],
            "error",
        )

    def test_schema_only(self):
        self.check(
            [
                make_call(
                    "get_schema_info"
                ),
                make_result(
                    "schema",
                    name="get_schema_info",
                ),
                final_answer(),
            ],
            "error",
        )

    def test_tool_status_error(self):
        self.check(
            [
                make_call(),
                make_result(
                    "数据库连接失败",
                    status="error",
                ),
                final_answer("无法查询"),
            ],
            "error",
        )

    def test_json_error(self):
        self.check(
            [
                make_call(),
                make_result(
                    '{"status":"error",'
                    '"error":"表不存在"}'
                ),
                final_answer("查询失败"),
            ],
            "error",
        )

    def test_mcp_is_error_payload(self):
        self.check(
            [
                make_call(),
                make_result(
                    '{"isError":true,'
                    '"error":"连接失败"}'
                ),
                final_answer("查询失败"),
            ],
            "error",
        )

    def test_plaintext_error(self):
        self.check(
            [
                make_call(),
                make_result(
                    "Error: Table inventory does not exist"
                ),
                final_answer("查询失败"),
            ],
            "error",
        )

    def test_unmatched_call_id(self):
        self.check(
            [
                make_call(call_id="call_001"),
                make_result(call_id="call_002"),
                final_answer(),
            ],
            "error",
        )

    def test_wrong_tool_name(self):
        self.check(
            [
                make_call(),
                make_result(
                    name="get_schema_info"
                ),
                final_answer(),
            ],
            "error",
        )

    def test_empty_rows_success(self):
        self.check(
            [
                make_call(),
                make_result("[]"),
                final_answer(
                    "查询成功，结果为0条记录"
                ),
            ],
            "success",
        )

    def test_empty_tool_content(self):
        self.check(
            [
                make_call(),
                make_result(""),
                final_answer(),
            ],
            "error",
        )

    def test_missing_final_answer(self):
        self.check(
            [
                make_call(),
                make_result(),
            ],
            "error",
        )

    def test_unfinished_tool_call(self):
        self.check(
            [
                make_call(),
                make_result(),
                make_call(
                    call_id="call_002"
                ),
            ],
            "error",
        )

    def test_duplicate_query_id(self):
        self.check(
            [
                make_call(),
                make_call(),
                make_result(),
                final_answer(),
            ],
            "error",
        )

    def test_multiple_queries_one_fails(self):
        self.check(
            [
                make_call(call_id="q1"),
                make_result(call_id="q1"),
                make_call(call_id="q2"),
                make_result(
                    '{"status":"error",'
                    '"error":"SQL执行失败"}',
                    call_id="q2",
                ),
                final_answer(),
            ],
            "error",
        )

    def test_normal_query_text_not_error(self):
        self.check(
            [
                make_call(),
                make_result(
                    '[{"message":"订单包含错误记录"}]'
                ),
                final_answer(),
            ],
            "success",
        )


if __name__ == "__main__":
    unittest.main()
