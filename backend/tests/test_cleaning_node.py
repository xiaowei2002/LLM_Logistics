"""
Cleaning Node 单元测试。

使用 Fake CleaningAgent 验证：
data_file_path -> cleaning_result / cleaning_status / execution_trace
"""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from core.graph.nodes.cleaning_node import build_cleaning_node  # noqa: E402


class FakeCleaningAgent:
    def __init__(
        self,
        result: str = "清洗完成",
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.last_instruction: str | None = None

    async def clean(
        self,
        instruction: str,
    ) -> str:
        self.last_instruction = instruction

        if self.error is not None:
            raise self.error

        return self.result


class CleaningNodeTest(unittest.TestCase):

    def test_cleaning_node_success(self):
        agent = FakeCleaningAgent(
            result="数据清洗完成，共处理 10 行。"
        )

        node = build_cleaning_node(agent)

        with tempfile.NamedTemporaryFile(
            suffix=".csv",
            delete=False,
        ) as temp_file:
            temp_path = temp_file.name

        try:
            result = asyncio.run(
                node(
                    {
                        "user_query": "用于需求预测",
                        "data_file_path": temp_path,
                        "execution_trace": [],
                    }
                )
            )

            self.assertEqual(
                result["cleaning_status"],
                "success",
            )

            self.assertEqual(
                result["cleaning_result"],
                "数据清洗完成，共处理 10 行。",
            )

            self.assertEqual(
                result["status"],
                "running",
            )

            self.assertEqual(
                result["execution_trace"],
                ["cleaning"],
            )

            self.assertIn(
                temp_path,
                agent.last_instruction,
            )

            self.assertIn(
                "用于需求预测",
                agent.last_instruction,
            )

        finally:
            Path(temp_path).unlink(
                missing_ok=True
            )

    def test_cleaning_node_missing_file_path(self):
        agent = FakeCleaningAgent()

        node = build_cleaning_node(agent)

        result = asyncio.run(
            node(
                {
                    "user_query": "清洗数据",
                    "execution_trace": [],
                }
            )
        )

        self.assertEqual(
            result["cleaning_status"],
            "error",
        )

        self.assertEqual(
            result["status"],
            "failed",
        )

        self.assertEqual(
            result["execution_trace"],
            ["cleaning"],
        )

    def test_cleaning_node_file_not_exists(self):
        agent = FakeCleaningAgent()

        node = build_cleaning_node(agent)

        result = asyncio.run(
            node(
                {
                    "data_file_path": (
                        "not_exists_test_file.csv"
                    ),
                    "execution_trace": [],
                }
            )
        )

        self.assertEqual(
            result["cleaning_status"],
            "error",
        )

        self.assertIn(
            "数据文件不存在",
            result["cleaning_error"],
        )

    def test_cleaning_node_empty_result(self):
        agent = FakeCleaningAgent(
            result=""
        )

        node = build_cleaning_node(agent)

        with tempfile.NamedTemporaryFile(
            suffix=".csv",
            delete=False,
        ) as temp_file:
            temp_path = temp_file.name

        try:
            result = asyncio.run(
                node(
                    {
                        "data_file_path": temp_path,
                        "execution_trace": [],
                    }
                )
            )

            self.assertEqual(
                result["cleaning_status"],
                "error",
            )

            self.assertEqual(
                result["status"],
                "failed",
            )

        finally:
            Path(temp_path).unlink(
                missing_ok=True
            )

    def test_cleaning_node_exception(self):
        agent = FakeCleaningAgent(
            error=RuntimeError(
                "mcp-pandas 连接失败"
            )
        )

        node = build_cleaning_node(agent)

        with tempfile.NamedTemporaryFile(
            suffix=".csv",
            delete=False,
        ) as temp_file:
            temp_path = temp_file.name

        try:
            result = asyncio.run(
                node(
                    {
                        "data_file_path": temp_path,
                        "execution_trace": [],
                    }
                )
            )

            self.assertEqual(
                result["cleaning_status"],
                "error",
            )

            self.assertEqual(
                result["cleaning_error"],
                "mcp-pandas 连接失败",
            )

            self.assertEqual(
                result["status"],
                "failed",
            )

        finally:
            Path(temp_path).unlink(
                missing_ok=True
            )


if __name__ == "__main__":
    unittest.main()