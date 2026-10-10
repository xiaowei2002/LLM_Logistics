
"""
受限 Cleaning Workflow 集成测试。

不调用真实 LLM。
使用临时 CSV 验证：

1. 未审批时禁止执行
2. 有效操作审批后仅删除获批重复行
3. 旧布尔审批无法绕过 Precheck
4. 原始文件变化后旧审批失效
5. 旧版 Cleaning 模式仍保持兼容
6. 当前 Quality Gate 可以阻止异常数据进入 Report
"""

from __future__ import annotations

import asyncio
import csv
import tempfile
import unittest
from pathlib import Path

from core.graph.cleaning_approval import (
    build_approval_proposal,
)
from core.graph.workflow import (
    build_logistics_workflow,
)


FIXTURE = (
    Path(__file__).resolve().parent
    / "real_dirty_orders.csv"
)


class CleaningPrecheckWorkflowTest(unittest.TestCase):

    def setUp(self):

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

        self.source = (
            Path(self.temp.name) / "orders.csv"
        )
        self.source.write_bytes(
            FIXTURE.read_bytes()
        )

        self.old_cleaning_calls = 0
        self.report_calls = 0

        async def sql_node(state):
            raise AssertionError(
                "不应调用 SQL"
            )

        async def cleaning_node(state):
            self.old_cleaning_calls += 1

            return {
                "current_agent": "cleaning",
                "cleaning_status": "error",
                "cleaning_error": (
                    "测试旧 Cleaning 路径主动结束"
                ),
                "status": "failed",
                "execution_trace": ["cleaning"],
            }

        async def forecast_node(state):
            raise AssertionError(
                "不应调用 Forecast"
            )

        async def report_node(state):
            self.report_calls += 1
            return {
                "current_agent": "report",
                "report_status": "success",
                "status": "completed",
                "execution_trace": ["report"],
            }

        self.graph = build_logistics_workflow(
            sql_node=sql_node,
            cleaning_node=cleaning_node,
            forecast_node=forecast_node,
            report_node=report_node,
        )

    def initial_state(self):
        return {
            "task_mode": "cleaning_safe_report",
            "user_query": "安全清洗订单数据",
            "data_file_path": str(self.source),
            "status": "running",
            "execution_trace": [],
        }

    def build_approval(self):
        proposal = build_approval_proposal(
            self.source
        )

        return {
            "approval_type": "cleaning_operation",
            "approval_status": "approved",
            "source_sha256": (
                proposal["source_sha256"]
            ),
            "approved_operations": [
                {
                    "operation": (
                        "remove_exact_duplicates"
                    ),
                    "approved_csv_lines": [3],
                }
            ],
        }

    def invoke(self, state):
        return asyncio.run(
            self.graph.ainvoke(state)
        )

    def read_cleaned(self, path):
        with open(
            path,
            "r",
            encoding="utf-8-sig",
            newline="",
        ) as file:
            return list(csv.DictReader(file))

    def test_unapproved_stops_before_execution(self):
        result = self.invoke(
            self.initial_state()
        )

        self.assertEqual(
            result["status"],
            "needs_human_input",
        )
        self.assertEqual(
            result["human_reason"],
            "precleaning_approval",
        )
        self.assertEqual(
            result["execution_trace"],
            ["cleaning_precheck"],
        )
        self.assertEqual(
            self.old_cleaning_calls, 0
        )
        self.assertEqual(
            self.report_calls, 0
        )

    def test_approved_uses_restricted_executor(self):
        state = self.initial_state()
        state["cleaning_approval"] = (
            self.build_approval()
        )

        result = self.invoke(state)

        trace = result["execution_trace"]

        self.assertEqual(
            trace[:3],
            [
                "cleaning_precheck",
                "restricted_cleaning",
                "cleaning_quality",
            ],
        )

        self.assertNotIn("cleaning", trace)
        self.assertEqual(
            self.old_cleaning_calls, 0
        )

        execution = result[
            "cleaning_execution"
        ]

        self.assertEqual(
            execution["source_rows"], 8
        )
        self.assertEqual(
            execution["output_rows"], 7
        )
        self.assertEqual(
            execution["deleted_rows"], 1
        )

        rows = self.read_cleaned(
            result["cleaned_file_path"]
        )

        ids = [
            row["order_no"]
            for row in rows
        ]

        self.assertEqual(
            ids.count("SO1001"), 1
        )
        self.assertIn("SO1003", ids)
        self.assertIn("SO1005", ids)

        self.assertEqual(
            self.report_calls, 0
        )

    def test_boolean_approval_cannot_bypass(self):
        state = self.initial_state()
        state[
            "cleaning_precheck_approved"
        ] = True

        result = self.invoke(state)

        self.assertEqual(
            result["status"],
            "needs_human_input",
        )
        self.assertEqual(
            result["execution_trace"],
            ["cleaning_precheck"],
        )
        self.assertEqual(
            self.old_cleaning_calls, 0
        )

    def test_changed_source_rejected(self):
        approval = self.build_approval()

        with self.source.open(
            "a", encoding="utf-8"
        ) as file:
            file.write(
                "SO9999,SKU-999,1,2026-09-04,WH-A\n"
            )

        state = self.initial_state()
        state["cleaning_approval"] = approval

        result = self.invoke(state)

        self.assertEqual(
            result["status"], "failed"
        )
        self.assertEqual(
            result["execution_trace"],
            ["cleaning_precheck"],
        )
        self.assertEqual(
            self.old_cleaning_calls, 0
        )
        self.assertEqual(
            self.report_calls, 0
        )

    def test_old_mode_unchanged(self):
        state = self.initial_state()
        state["task_mode"] = "cleaning_report"

        result = self.invoke(state)

        self.assertEqual(
            result["status"], "failed"
        )
        self.assertEqual(
            result["execution_trace"],
            ["cleaning"],
        )
        self.assertEqual(
            self.old_cleaning_calls, 1
        )

    def test_quality_gate_blocks_report(self):
        state = self.initial_state()
        state["cleaning_approval"] = (
            self.build_approval()
        )

        result = self.invoke(state)

        quality = result["cleaning_quality"]

        self.assertTrue(
            quality["large_deletion_detected"]
        )
        self.assertEqual(
            quality["negative_qty_count"], 1
        )
        self.assertEqual(
            self.report_calls, 0
        )
        self.assertTrue(
            result["needs_human_input"]
        )


if __name__ == "__main__":
    unittest.main()
