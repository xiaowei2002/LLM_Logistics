"""
外层 Multi-Agent LangGraph 集成测试。

全部使用 Fake Node，
验证：

1. SQL -> Report
2. Cleaning -> Quality Gate -> Report
3. Cleaning 大比例删行 -> HITL
4. HITL 人工确认后 -> Quality Gate -> Report
5. Forecast -> Report
6. Forecast HITL
7. 非法 task_mode

不调用真实 LLM、数据库或 MCP。
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from core.graph.workflow import build_logistics_workflow  # noqa: E402


# ==========================================================
# Fake Nodes
# ==========================================================


async def fake_sql_node(state):
    return {
        "current_agent": "sql",
        "sql_result": "查询完成",
        "sql_status": "success",
        "sql_error": None,
        "status": "running",
        "execution_trace": ["sql"],
    }


async def fake_cleaning_node(state):
    """
    模拟安全清洗。

    原始文件由测试传入。
    清洗结果保持相同行数，
    因此不会触发 >10% 删除保护。
    """

    source_path = Path(
        state["data_file_path"]
    )

    cleaned_path = source_path.with_name(
        f"{source_path.stem}_cleaned"
        f"{source_path.suffix}"
    )

    cleaned_path.write_text(
        (
            "order_no,sku,qty,warehouse,created_at_raw\n"
            "SO1001,SKU-001,10,WH-A,2026-09-01\n"
            "SO1002,SKU-002,8,WH-B,2026-09-02\n"
        ),
        encoding="utf-8",
    )

    return {
        "current_agent": "cleaning",
        "cleaning_result": "清洗完成",
        "cleaned_file_path": str(
            cleaned_path
        ),
        "cleaning_status": "success",
        "cleaning_error": None,
        "status": "running",
        "execution_trace": ["cleaning"],
    }


async def fake_large_deletion_cleaning_node(
    state,
):
    """
    模拟危险清洗：

    原始 10 行
    -> 清洗后 5 行

    删除率 50%，必须触发 HITL。
    """

    source_path = Path(
        state["data_file_path"]
    )

    cleaned_path = source_path.with_name(
        f"{source_path.stem}_cleaned"
        f"{source_path.suffix}"
    )

    cleaned_path.write_text(
        (
            "order_no,sku,qty,warehouse,created_at_raw\n"
            "SO1001,SKU-001,10,WH-A,2026-09-01\n"
            "SO1002,SKU-002,8,WH-B,2026-09-02\n"
            "SO1003,SKU-003,6,WH-A,2026-09-03\n"
            "SO1004,SKU-004,4,WH-B,2026-09-04\n"
            "SO1005,SKU-005,2,WH-A,2026-09-05\n"
        ),
        encoding="utf-8",
    )

    return {
        "current_agent": "cleaning",
        "cleaning_result": (
            "清洗完成，但删除了较多记录。"
        ),
        "cleaned_file_path": str(
            cleaned_path
        ),
        "cleaning_status": "success",
        "cleaning_error": None,
        "status": "running",
        "execution_trace": ["cleaning"],
    }


def fake_forecast_node(state):
    return {
        "current_agent": "forecast",
        "forecast_result": {
            "answer": "M001=10",
        },
        "forecast_status": "success",
        "forecast_error": None,
        "status": "running",
        "execution_trace": ["forecast"],
    }


def fake_forecast_interrupt_node(state):
    return {
        "current_agent": "forecast",
        "forecast_result": {
            "__interrupt__": [
                "请补充预测时间。"
            ],
        },
        "forecast_status": (
            "needs_human_input"
        ),
        "forecast_thread_id": (
            "test-thread-001"
        ),
        "needs_human_input": True,
        "human_reason": "missing_time",
        "human_question": (
            "请补充预测时间。"
        ),
        "status": "needs_human_input",
        "execution_trace": ["forecast"],
    }


def fake_report_node(state):
    return {
        "current_agent": "report",
        "report_result": "最终报告",
        "report_status": "success",
        "report_error": None,
        "status": "completed",
        "execution_trace": ["report"],
    }


# ==========================================================
# Tests
# ==========================================================


class MultiAgentWorkflowTest(
    unittest.TestCase
):

    def _build_graph(
        self,
        cleaning_node=fake_cleaning_node,
    ):
        return build_logistics_workflow(
            sql_node=fake_sql_node,
            cleaning_node=cleaning_node,
            forecast_node=fake_forecast_node,
            report_node=fake_report_node,
        )

    @staticmethod
    def _create_source_file(
        row_count: int = 2,
    ) -> Path:
        """
        创建测试原始 CSV。
        """

        temp_file = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".csv",
            delete=False,
            encoding="utf-8",
            newline="",
        )

        path = Path(
            temp_file.name
        )

        try:
            temp_file.write(
                "order_no,sku,qty,warehouse,created_at\n"
            )

            for index in range(
                1,
                row_count + 1,
            ):
                temp_file.write(
                    (
                        f"SO{index:04d},"
                        f"SKU-{index:03d},"
                        f"{index},"
                        "WH-A,"
                        f"2026-09-{index:02d}\n"
                    )
                )

        finally:
            temp_file.close()

        return path

    # ======================================================
    # 1. SQL -> Report
    # ======================================================

    def test_sql_report_route(self):
        graph = self._build_graph()

        result = asyncio.run(
            graph.ainvoke(
                {
                    "user_query": "查询订单",
                    "task_mode": "sql_report",
                    "execution_trace": [],
                    "status": "running",
                }
            )
        )

        self.assertEqual(
            result["execution_trace"],
            [
                "sql",
                "report",
            ],
        )

        self.assertEqual(
            result["status"],
            "completed",
        )

        self.assertEqual(
            result["sql_status"],
            "success",
        )

        self.assertEqual(
            result["report_status"],
            "success",
        )

    # ======================================================
    # 2. Cleaning -> Gate -> Report
    # ======================================================

    def test_cleaning_report_route(self):
        graph = self._build_graph()

        source_path = (
            self._create_source_file(
                row_count=2
            )
        )

        cleaned_path = source_path.with_name(
            f"{source_path.stem}_cleaned"
            f"{source_path.suffix}"
        )

        try:
            result = asyncio.run(
                graph.ainvoke(
                    {
                        "user_query": "清洗数据",
                        "task_mode": (
                            "cleaning_report"
                        ),
                        "data_file_path": str(
                            source_path
                        ),
                        "execution_trace": [],
                        "status": "running",
                    }
                )
            )

            self.assertEqual(
                result["execution_trace"],
                [
                    "cleaning",
                    "cleaning_quality",
                    "report",
                ],
            )

            self.assertEqual(
                result["status"],
                "completed",
            )

            self.assertEqual(
                result["cleaning_status"],
                "success",
            )

            self.assertEqual(
                result[
                    "cleaning_quality_status"
                ],
                "success",
            )

            self.assertTrue(
                result[
                    "cleaning_quality"
                ]["passed"]
            )

            self.assertEqual(
                result[
                    "cleaning_quality"
                ]["source_row_count"],
                2,
            )

            self.assertEqual(
                result[
                    "cleaning_quality"
                ]["cleaned_row_count"],
                2,
            )

            self.assertEqual(
                result[
                    "cleaning_quality"
                ]["removed_row_count"],
                0,
            )

            self.assertEqual(
                result["report_status"],
                "success",
            )

        finally:
            source_path.unlink(
                missing_ok=True
            )

            cleaned_path.unlink(
                missing_ok=True
            )

    # ======================================================
    # 3. 大比例删除 -> HITL
    # ======================================================

    def test_large_deletion_requires_human_input(
        self,
    ):
        graph = self._build_graph(
            cleaning_node=(
                fake_large_deletion_cleaning_node
            )
        )

        source_path = (
            self._create_source_file(
                row_count=10
            )
        )

        cleaned_path = source_path.with_name(
            f"{source_path.stem}_cleaned"
            f"{source_path.suffix}"
        )

        try:
            result = asyncio.run(
                graph.ainvoke(
                    {
                        "user_query": "清洗数据",
                        "task_mode": (
                            "cleaning_report"
                        ),
                        "data_file_path": str(
                            source_path
                        ),
                        "execution_trace": [],
                        "status": "running",
                    }
                )
            )

            self.assertEqual(
                result["status"],
                "needs_human_input",
            )

            self.assertTrue(
                result[
                    "needs_human_input"
                ]
            )

            self.assertEqual(
                result[
                    "human_reason"
                ],
                "large_deletion",
            )

            self.assertIn(
                "50.00%",
                result[
                    "human_question"
                ],
            )

            self.assertEqual(
                result[
                    "cleaning_quality_status"
                ],
                "needs_human_input",
            )

            quality = result[
                "cleaning_quality"
            ]

            self.assertFalse(
                quality["passed"]
            )

            self.assertTrue(
                quality[
                    "large_deletion_detected"
                ]
            )

            self.assertEqual(
                quality[
                    "source_row_count"
                ],
                10,
            )

            self.assertEqual(
                quality[
                    "cleaned_row_count"
                ],
                5,
            )

            self.assertEqual(
                quality[
                    "removed_row_count"
                ],
                5,
            )

            self.assertEqual(
                quality[
                    "removal_rate"
                ],
                0.5,
            )

            self.assertEqual(
                result[
                    "execution_trace"
                ],
                [
                    "cleaning",
                    "cleaning_quality",
                ],
            )

            self.assertNotIn(
                "report",
                result[
                    "execution_trace"
                ],
            )

            self.assertNotIn(
                "report_result",
                result,
            )

        finally:
            source_path.unlink(
                missing_ok=True
            )

            cleaned_path.unlink(
                missing_ok=True
            )

    # ======================================================
    # 4. HITL 确认后继续 Report
    # ======================================================

    def test_large_deletion_resume_after_approval(
        self,
    ):
        """
        两阶段测试：

        第一阶段：
        Cleaning -> Quality Gate -> HITL

        第二阶段：
        用户确认
        -> START
        -> Quality Gate
        -> Report

        第二阶段不得再次执行 CleaningAgent。
        """

        cleaning_call_count = {
            "count": 0
        }

        async def counting_cleaning_node(
            state,
        ):
            cleaning_call_count[
                "count"
            ] += 1

            return await (
                fake_large_deletion_cleaning_node(
                    state
                )
            )

        graph = self._build_graph(
            cleaning_node=(
                counting_cleaning_node
            )
        )

        source_path = (
            self._create_source_file(
                row_count=10
            )
        )

        cleaned_path = source_path.with_name(
            f"{source_path.stem}_cleaned"
            f"{source_path.suffix}"
        )

        try:
            # ------------------------------------------
            # 第一阶段：触发 HITL
            # ------------------------------------------

            first_result = asyncio.run(
                graph.ainvoke(
                    {
                        "user_query": "清洗数据",
                        "task_mode": (
                            "cleaning_report"
                        ),
                        "data_file_path": str(
                            source_path
                        ),
                        "execution_trace": [],
                        "status": "running",
                    }
                )
            )

            self.assertEqual(
                first_result["status"],
                "needs_human_input",
            )

            self.assertEqual(
                cleaning_call_count[
                    "count"
                ],
                1,
            )

            self.assertTrue(
                cleaned_path.exists()
            )

            # ------------------------------------------
            # 第二阶段：模拟用户明确确认
            # ------------------------------------------

            resume_state = dict(
                first_result
            )

            resume_state[
                "cleaning_large_deletion_approved"
            ] = True

            resume_state[
                "status"
            ] = "running"

            # 新的一次 graph invocation，
            # execution_trace 应记录本阶段新增路径。
            resume_state[
                "execution_trace"
            ] = []

            second_result = asyncio.run(
                graph.ainvoke(
                    resume_state
                )
            )

            # ------------------------------------------
            # 验证没有重新执行 CleaningAgent
            # ------------------------------------------

            self.assertEqual(
                cleaning_call_count[
                    "count"
                ],
                1,
            )

            # ------------------------------------------
            # 第二阶段应直接：
            # Quality Gate -> Report
            # ------------------------------------------

            self.assertEqual(
                second_result[
                    "execution_trace"
                ],
                [
                    "cleaning_quality",
                    "report",
                ],
            )

            self.assertEqual(
                second_result["status"],
                "completed",
            )

            self.assertEqual(
                second_result[
                    "cleaning_quality_status"
                ],
                "success",
            )

            self.assertFalse(
                second_result[
                    "needs_human_input"
                ]
            )

            self.assertIsNone(
                second_result[
                    "human_reason"
                ]
            )

            self.assertIsNone(
                second_result[
                    "human_question"
                ]
            )

            quality = second_result[
                "cleaning_quality"
            ]

            self.assertTrue(
                quality["passed"]
            )

            self.assertTrue(
                quality[
                    "large_deletion_detected"
                ]
            )

            self.assertTrue(
                quality[
                    "large_deletion_approved"
                ]
            )

            self.assertEqual(
                quality[
                    "removal_rate"
                ],
                0.5,
            )

            self.assertEqual(
                second_result[
                    "report_status"
                ],
                "success",
            )

            self.assertEqual(
                second_result[
                    "report_result"
                ],
                "最终报告",
            )

        finally:
            source_path.unlink(
                missing_ok=True
            )

            cleaned_path.unlink(
                missing_ok=True
            )

    # ======================================================
    # 5. Forecast -> Report
    # ======================================================

    def test_forecast_report_route(self):
        graph = self._build_graph()

        result = asyncio.run(
            graph.ainvoke(
                {
                    "user_query": "预测需求",
                    "task_mode": (
                        "forecast_report"
                    ),
                    "execution_trace": [],
                    "status": "running",
                }
            )
        )

        self.assertEqual(
            result["execution_trace"],
            [
                "forecast",
                "report",
            ],
        )

        self.assertEqual(
            result["status"],
            "completed",
        )

        self.assertEqual(
            result["forecast_status"],
            "success",
        )

        self.assertEqual(
            result["report_status"],
            "success",
        )

    # ======================================================
    # 6. Forecast HITL
    # ======================================================

    def test_forecast_human_review_stops_before_report(
        self,
    ):
        graph = build_logistics_workflow(
            sql_node=fake_sql_node,
            cleaning_node=fake_cleaning_node,
            forecast_node=(
                fake_forecast_interrupt_node
            ),
            report_node=fake_report_node,
        )

        result = asyncio.run(
            graph.ainvoke(
                {
                    "user_query": (
                        "预测未来需求"
                    ),
                    "task_mode": (
                        "forecast_report"
                    ),
                    "execution_trace": [],
                    "status": "running",
                }
            )
        )

        self.assertEqual(
            result["status"],
            "needs_human_input",
        )

        self.assertTrue(
            result[
                "needs_human_input"
            ]
        )

        self.assertEqual(
            result[
                "human_reason"
            ],
            "missing_time",
        )

        self.assertEqual(
            result[
                "human_question"
            ],
            "请补充预测时间。",
        )

        self.assertEqual(
            result[
                "forecast_thread_id"
            ],
            "test-thread-001",
        )

        self.assertEqual(
            result[
                "execution_trace"
            ],
            ["forecast"],
        )

        self.assertNotIn(
            "report",
            result[
                "execution_trace"
            ],
        )

        self.assertNotIn(
            "report_result",
            result,
        )

    # ======================================================
    # 7. Invalid task_mode
    # ======================================================

    def test_invalid_task_mode(self):
        graph = self._build_graph()

        result = asyncio.run(
            graph.ainvoke(
                {
                    "user_query": "未知任务",
                    "task_mode": "unknown",
                    "execution_trace": [],
                    "status": "running",
                }
            )
        )

        self.assertEqual(
            result["status"],
            "failed",
        )

        self.assertEqual(
            result["execution_trace"],
            ["invalid"],
        )

        self.assertIn(
            "不支持的 task_mode",
            result[
                "report_error"
            ],
        )


if __name__ == "__main__":
    unittest.main()