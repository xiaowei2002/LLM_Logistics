"""
Report Node 单元测试。

验证：

1. Report Node 正常生成报告
2. 没有上游结果时失败
3. ReportAgent 返回空结果时失败
4. ReportAgent 抛出异常时失败
5. Cleaning Quality Gate + HITL 人工确认事实
   能够完整传递给 ReportAgent
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from core.graph.nodes.report_node import build_report_node  # noqa: E402


class FakeReportAgent:
    def __init__(
        self,
        result: str = "最终报告",
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.last_instruction: str | None = None

    def generate(
        self,
        instruction: str,
    ) -> str:
        self.last_instruction = instruction

        if self.error is not None:
            raise self.error

        return self.result


class ReportNodeTest(
    unittest.TestCase
):

    # ======================================================
    # 1. 正常报告生成
    # ======================================================

    def test_report_node_success(self):
        agent = FakeReportAgent(
            result="物流分析报告生成完成。"
        )

        node = build_report_node(
            agent
        )

        result = node(
            {
                "user_query": (
                    "预测物料需求"
                ),
                "sql_result": (
                    "查询到订单数据"
                ),
                "cleaning_result": (
                    "清洗完成"
                ),
                "forecast_result": {
                    "answer": "M001=10"
                },
                "execution_trace": [
                    "sql",
                    "cleaning",
                    "forecast",
                ],
            }
        )

        self.assertEqual(
            result["report_status"],
            "success",
        )

        self.assertEqual(
            result["status"],
            "completed",
        )

        self.assertEqual(
            result["report_result"],
            "物流分析报告生成完成。",
        )

        # Report Node 单独调用时，
        # 只返回当前节点新增的执行轨迹。
        # 完整历史轨迹由 LangGraph
        # State reducer 负责累计。
        self.assertEqual(
            result["execution_trace"],
            ["report"],
        )

        self.assertIsNotNone(
            agent.last_instruction
        )

        self.assertIn(
            "查询到订单数据",
            agent.last_instruction,
        )

        self.assertIn(
            "清洗完成",
            agent.last_instruction,
        )

        self.assertIn(
            "M001=10",
            agent.last_instruction,
        )

    # ======================================================
    # 2. 无上游结果
    # ======================================================

    def test_report_node_without_upstream_results(
        self,
    ):
        agent = FakeReportAgent()

        node = build_report_node(
            agent
        )

        result = node(
            {
                "execution_trace": [],
            }
        )

        self.assertEqual(
            result["report_status"],
            "error",
        )

        self.assertEqual(
            result["status"],
            "failed",
        )

        self.assertEqual(
            result["execution_trace"],
            ["report"],
        )

    # ======================================================
    # 3. ReportAgent 返回空结果
    # ======================================================

    def test_report_node_empty_result(self):
        agent = FakeReportAgent(
            result=""
        )

        node = build_report_node(
            agent
        )

        result = node(
            {
                "user_query": (
                    "生成报告"
                ),
                "execution_trace": [],
            }
        )

        self.assertEqual(
            result["report_status"],
            "error",
        )

        self.assertEqual(
            result["status"],
            "failed",
        )

        self.assertEqual(
            result["execution_trace"],
            ["report"],
        )

    # ======================================================
    # 4. ReportAgent 执行异常
    # ======================================================

    def test_report_node_exception(self):
        agent = FakeReportAgent(
            error=RuntimeError(
                "报告生成失败"
            )
        )

        node = build_report_node(
            agent
        )

        result = node(
    {
        "user_query": "生成查询结果报告",
        "sql_result": {
            "rows": [
                {
                    "sku": "SKU-001",
                    "quantity": 42,
                }
            ]
        },
        "sql_status": "success",
        "execution_trace": [],
    }
)

        self.assertEqual(
            result["report_status"],
            "error",
        )

        self.assertEqual(
            result["report_error"],
            "报告生成失败",
        )

        self.assertEqual(
            result["status"],
            "failed",
        )

        self.assertEqual(
            result["execution_trace"],
            ["report"],
        )

    # ======================================================
    # 5. Cleaning Quality Gate + HITL 事实传递
    # ======================================================

    def test_report_node_includes_cleaning_quality_and_hitl(
        self,
    ):
        """
        验证 Report Node 会把这些关键事实
        明确传给 ReportAgent：

        - 原始文件路径
        - 清洗文件路径
        - source_row_count = 8
        - cleaned_row_count = 4
        - removed_row_count = 4
        - removal_rate = 0.5
        - large_deletion_detected = true
        - large_deletion_approved = true
        - Human-in-the-loop 已发生
        """

        agent = FakeReportAgent(
            result="清洗分析报告生成完成。"
        )

        node = build_report_node(
            agent
        )

        result = node(
            {
                "user_query": (
                    "检查并清洗订单数据"
                ),

                "data_file_path": (
                    r"D:\data\orders.csv"
                ),

                "cleaned_file_path": (
                    r"D:\data\orders_cleaned.csv"
                ),

                "cleaning_result": (
                    "CleaningAgent 清洗完成。"
                ),

                "cleaning_quality": {
                    "passed": True,
                    "file_path": (
                        r"D:\data\orders_cleaned.csv"
                    ),
                    "source_file_path": (
                        r"D:\data\orders.csv"
                    ),
                    "row_count": 4,
                    "column_count": 6,
                    "source_row_count": 8,
                    "cleaned_row_count": 4,
                    "removed_row_count": 4,
                    "removal_rate": 0.5,
                    "large_deletion_detected": True,
                    "large_deletion_approved": True,
                    "missing_values": {
                        "created_at": 3,
                    },
                    "duplicate_rows": 0,
                    "negative_qty_count": 0,
                    "time_raw_preserved": True,
                    "errors": [],
                    "warnings": [
                        (
                            "清洗后仍存在缺失值；"
                            "是否允许需要结合业务规则判断。"
                        ),
                        (
                            "自动删行比例超过 10%，"
                            "但已经获得用户或业务方明确确认。"
                        ),
                    ],
                },

                "cleaning_quality_status": (
                    "success"
                ),

                "cleaning_quality_error": None,

                "cleaning_large_deletion_approved": (
                    True
                ),

                # 第二阶段恢复后，
                # 当前 HITL 状态已经被清除。
                "needs_human_input": False,
                "human_reason": None,
                "human_question": None,

                "execution_trace": [
                    "cleaning_quality",
                ],

                "status": "running",
            }
        )

        # --------------------------------------------------
        # Report Node 自身应正常完成
        # --------------------------------------------------

        self.assertEqual(
            result["report_status"],
            "success",
        )

        self.assertEqual(
            result["status"],
            "completed",
        )

        self.assertEqual(
            result["report_result"],
            "清洗分析报告生成完成。",
        )

        self.assertEqual(
            result["execution_trace"],
            ["report"],
        )

        self.assertIsNotNone(
            agent.last_instruction
        )

        instruction = (
            agent.last_instruction
        )

        # --------------------------------------------------
        # 文件路径必须传入
        # --------------------------------------------------

        self.assertIn(
            r"D:\data\orders.csv",
            instruction,
        )

        self.assertIn(
            r"D:\data\orders_cleaned.csv",
            instruction,
        )

        # --------------------------------------------------
        # Cleaning Quality Gate 数值必须传入
        # --------------------------------------------------

        self.assertIn(
            '"source_row_count": 8',
            instruction,
        )

        self.assertIn(
            '"cleaned_row_count": 4',
            instruction,
        )

        self.assertIn(
            '"removed_row_count": 4',
            instruction,
        )

        self.assertIn(
            '"removal_rate": 0.5',
            instruction,
        )

        self.assertIn(
            '"large_deletion_detected": true',
            instruction,
        )

        self.assertIn(
            '"large_deletion_approved": true',
            instruction,
        )

        # --------------------------------------------------
        # HITL 已发生这一事实必须明确传入
        # --------------------------------------------------

        self.assertIn(
            "已发生 Human-in-the-loop 人工确认",
            instruction,
        )

        self.assertIn(
            "用户或业务方已明确批准",
            instruction,
        )

        self.assertIn(
            "人工确认后未重新执行 CleaningAgent",
            instruction,
        )

        # --------------------------------------------------
        # 事实优先级规则必须传入
        # --------------------------------------------------

        self.assertIn(
            "Cleaning Quality Gate 是 Python",
            instruction,
        )

        self.assertIn(
            "必须以 Cleaning Quality Gate 为准",
            instruction,
        )

        self.assertIn(
            "不得写成“无人工确认”或“全自动执行”",
            instruction,
        )


if __name__ == "__main__":
    unittest.main()