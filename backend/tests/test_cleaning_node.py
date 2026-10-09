"""
Cleaning Node 单元测试。

使用 Fake CleaningAgent 验证：
data_file_path
-> CleaningAgent
-> cleaned_file_path
-> cleaning_result / cleaning_status / execution_trace

重点验证：
1. 成功时清洗文件必须真实落盘
2. Agent 只声称成功但没有落盘时必须判失败
3. 输入文件不存在、空结果、Agent 异常等错误路径
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from core.graph.nodes.cleaning_node import build_cleaning_node  # noqa: E402


class FakeCleaningAgent:
    """
    Fake CleaningAgent。

    create_output=True 时：
    从 Cleaning Node 提供的 instruction 中提取指定输出路径，
    并真实创建一个最小 CSV，用于模拟 MCP 落盘。
    """

    def __init__(
        self,
        result: str = "清洗完成",
        error: Exception | None = None,
        create_output: bool = False,
    ) -> None:
        self.result = result
        self.error = error
        self.create_output = create_output
        self.last_instruction: str | None = None
        self.created_output_path: Path | None = None

    @staticmethod
    def _extract_output_path(
        instruction: str,
    ) -> Path:
        marker = (
            "清洗完成后，必须将最终清洗结果保存到以下指定路径："
        )

        if marker not in instruction:
            raise AssertionError(
                "测试无法从 Cleaning instruction 中找到指定输出路径。"
            )

        remaining = instruction.split(
            marker,
            1,
        )[1].lstrip()

        output_line = remaining.splitlines()[0].strip()

        if not output_line:
            raise AssertionError(
                "Cleaning instruction 中的输出路径为空。"
            )

        return Path(output_line)

    async def clean(
        self,
        instruction: str,
    ) -> str:
        self.last_instruction = instruction

        if self.error is not None:
            raise self.error

        if self.create_output:
            output_path = self._extract_output_path(
                instruction
            )

            output_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            output_path.write_text(
                (
                    "order_no,sku,qty,warehouse,created_at_raw\n"
                    "SO1001,SKU-001,10,WH-A,2026-09-01\n"
                    "SO1002,SKU-002,8,WH-B,2026-09-02\n"
                ),
                encoding="utf-8",
            )

            self.created_output_path = output_path

        return self.result


class CleaningNodeTest(
    unittest.TestCase
):

    def test_cleaning_node_success(self):
        """
        CleaningAgent 返回成功，
        且指定输出文件真实落盘。
        """

        agent = FakeCleaningAgent(
            result="数据清洗完成，共处理 10 行。",
            create_output=True,
        )

        node = build_cleaning_node(
            agent
        )

        with tempfile.NamedTemporaryFile(
            suffix=".csv",
            delete=False,
        ) as temp_file:
            temp_path = Path(
                temp_file.name
            )

        expected_output = temp_path.with_name(
            f"{temp_path.stem}_cleaned{temp_path.suffix}"
        ).resolve()

        try:
            result = asyncio.run(
                node(
                    {
                        "user_query": "用于需求预测",
                        "data_file_path": str(
                            temp_path
                        ),
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

            self.assertEqual(
                result["cleaned_file_path"],
                str(expected_output),
            )

            self.assertTrue(
                expected_output.exists()
            )

            self.assertEqual(
                agent.created_output_path,
                expected_output,
            )

            self.assertIsNotNone(
                agent.last_instruction
            )

            self.assertIn(
                str(temp_path),
                agent.last_instruction,
            )

            self.assertIn(
                str(expected_output),
                agent.last_instruction,
            )

            self.assertIn(
                "用于需求预测",
                agent.last_instruction,
            )

        finally:
            temp_path.unlink(
                missing_ok=True
            )

            expected_output.unlink(
                missing_ok=True
            )

    def test_cleaning_node_missing_file_path(self):
        agent = FakeCleaningAgent()

        node = build_cleaning_node(
            agent
        )

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

        self.assertIn(
            "未提供待清洗数据文件路径",
            result["cleaning_error"],
        )

    def test_cleaning_node_file_not_exists(self):
        agent = FakeCleaningAgent()

        node = build_cleaning_node(
            agent
        )

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

        self.assertEqual(
            result["status"],
            "failed",
        )

        self.assertIn(
            "数据文件不存在",
            result["cleaning_error"],
        )

        self.assertEqual(
            result["execution_trace"],
            ["cleaning"],
        )

    def test_cleaning_node_empty_result(self):
        agent = FakeCleaningAgent(
            result=""
        )

        node = build_cleaning_node(
            agent
        )

        with tempfile.NamedTemporaryFile(
            suffix=".csv",
            delete=False,
        ) as temp_file:
            temp_path = Path(
                temp_file.name
            )

        expected_output = temp_path.with_name(
            f"{temp_path.stem}_cleaned{temp_path.suffix}"
        ).resolve()

        try:
            result = asyncio.run(
                node(
                    {
                        "data_file_path": str(
                            temp_path
                        ),
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

            self.assertIn(
                "未返回清洗结果",
                result["cleaning_error"],
            )

            self.assertEqual(
                result["execution_trace"],
                ["cleaning"],
            )

        finally:
            temp_path.unlink(
                missing_ok=True
            )

            expected_output.unlink(
                missing_ok=True
            )

    def test_cleaning_node_exception(self):
        agent = FakeCleaningAgent(
            error=RuntimeError(
                "mcp-pandas 连接失败"
            )
        )

        node = build_cleaning_node(
            agent
        )

        with tempfile.NamedTemporaryFile(
            suffix=".csv",
            delete=False,
        ) as temp_file:
            temp_path = Path(
                temp_file.name
            )

        expected_output = temp_path.with_name(
            f"{temp_path.stem}_cleaned{temp_path.suffix}"
        ).resolve()

        try:
            result = asyncio.run(
                node(
                    {
                        "data_file_path": str(
                            temp_path
                        ),
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

            self.assertEqual(
                result["execution_trace"],
                ["cleaning"],
            )

        finally:
            temp_path.unlink(
                missing_ok=True
            )

            expected_output.unlink(
                missing_ok=True
            )

    def test_cleaning_node_fails_when_output_not_written(
        self,
    ):
        """
        Agent 即使文字上声称成功，
        如果指定清洗文件没有真实落盘，
        Cleaning Node 也必须失败。
        """

        agent = FakeCleaningAgent(
            result="清洗成功并已保存。",
            create_output=False,
        )

        node = build_cleaning_node(
            agent
        )

        with tempfile.NamedTemporaryFile(
            suffix=".csv",
            delete=False,
        ) as temp_file:
            temp_path = Path(
                temp_file.name
            )

        expected_output = temp_path.with_name(
            f"{temp_path.stem}_cleaned{temp_path.suffix}"
        ).resolve()

        try:
            result = asyncio.run(
                node(
                    {
                        "data_file_path": str(
                            temp_path
                        ),
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

            self.assertIsNone(
                result["cleaned_file_path"]
            )

            self.assertIn(
                "没有真实落盘",
                result["cleaning_error"],
            )

            self.assertFalse(
                expected_output.exists()
            )

            self.assertEqual(
                result["execution_trace"],
                ["cleaning"],
            )

        finally:
            temp_path.unlink(
                missing_ok=True
            )

            expected_output.unlink(
                missing_ok=True
            )


if __name__ == "__main__":
    unittest.main()