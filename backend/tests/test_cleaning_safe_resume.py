
"""
Safe Cleaning 事后 HITL 恢复测试。

验证：
1. 审批后恢复不重复清洗
2. 源文件变化拒绝恢复
3. 清洗结果被修改拒绝恢复
4. 缺少执行证据拒绝恢复
5. 仅设置事后审批布尔值不能绕过验证
6. 负数质量错误仍阻止 Report

运行：
python -m tests.test_cleaning_safe_resume
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import unittest
from copy import deepcopy
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


class SafeCleaningResumeTest(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

        self.source = (
            Path(self.temp.name) / "orders.csv"
        )
        self.source.write_bytes(FIXTURE.read_bytes())

        self.cleaning_calls = 0
        self.report_calls = 0
        self.generated_dirs = []

        async def sql_node(state):
            raise AssertionError("不应调用 SQL")

        async def cleaning_node(state):
            self.cleaning_calls += 1
            raise AssertionError(
                "安全流程不应调用旧 CleaningAgent"
            )

        async def forecast_node(state):
            raise AssertionError("不应调用 Forecast")

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

    def tearDown(self):
        for directory in self.generated_dirs:
            shutil.rmtree(
                directory,
                ignore_errors=True,
            )

    def initial_state(self):
        return {
            "task_mode": "cleaning_safe_report",
            "data_file_path": str(self.source),
            "user_query": "安全清洗并审核订单",
            "status": "running",
            "execution_trace": [],
        }

    def approval(self):
        proposal = build_approval_proposal(
            self.source
        )

        return {
            "approval_type": "cleaning_operation",
            "approval_status": "approved",
            "source_sha256": proposal["source_sha256"],
            "approved_operations": [
                {
                    "operation": "remove_exact_duplicates",
                    "approved_csv_lines": [3],
                }
            ],
        }

    def invoke(self, state):
        return asyncio.run(
            self.graph.ainvoke(state)
        )

    def first_run(self):
        state = self.initial_state()
        state["cleaning_approval"] = self.approval()

        result = self.invoke(state)

        self.assertEqual(
            result["status"],
            "needs_human_input",
        )
        self.assertEqual(
            result["human_reason"],
            "large_deletion",
        )

        self.assertEqual(
            result["cleaning_execution"]["deleted_rows"],
            1,
        )

        self.generated_dirs.append(
            Path(result["cleaned_file_path"]).parent
        )

        return result

    def resume_state(self, first_result):
        # 使用已有执行结果恢复，
        # 不创建任何新的清洗指令。
        state = deepcopy(first_result)

        state["cleaning_large_deletion_approved"] = True
        state["status"] = "running"

        # 本次图调用只记录恢复阶段的新轨迹。
        # 原始轨迹仍保存在 first_result 中。
        state["execution_trace"] = []

        return state

    def test_resume_does_not_reexecute(self):
        first = self.first_run()

        original_path = first["cleaned_file_path"]
        original_bytes = Path(original_path).read_bytes()

        resumed = self.invoke(
            self.resume_state(first)
        )

        self.assertEqual(
            resumed["execution_trace"],
            ["cleaning_resume", "cleaning_quality"],
        )

        self.assertEqual(
            resumed["cleaned_file_path"],
            original_path,
        )

        self.assertEqual(
            Path(original_path).read_bytes(),
            original_bytes,
        )

        self.assertEqual(self.cleaning_calls, 0)
        self.assertEqual(self.report_calls, 0)

        # 事后删除确认不解除其他质量错误。
        self.assertEqual(
            resumed["status"], "failed"
        )
        self.assertEqual(
            resumed["cleaning_quality"]["negative_qty_count"],
            1,
        )

    def test_changed_source_rejected(self):
        first = self.first_run()

        with self.source.open(
            "a", encoding="utf-8"
        ) as file:
            file.write(
                "SO9999,SKU-999,1,2026-09-04,WH-A\n"
            )

        resumed = self.invoke(
            self.resume_state(first)
        )

        self.assertEqual(resumed["status"], "failed")
        self.assertEqual(
            resumed["execution_trace"],
            ["cleaning_resume"],
        )
        self.assertEqual(self.report_calls, 0)

    def test_changed_output_rejected(self):
        first = self.first_run()

        with open(
            first["cleaned_file_path"],
            "a",
            encoding="utf-8",
        ) as file:
            file.write(
                "SO9999,SKU-999,1,2026-09-04,WH-A\n"
            )

        resumed = self.invoke(
            self.resume_state(first)
        )

        self.assertEqual(resumed["status"], "failed")
        self.assertEqual(
            resumed["execution_trace"],
            ["cleaning_resume"],
        )
        self.assertEqual(self.report_calls, 0)

    def test_missing_execution_evidence_rejected(self):
        first = self.first_run()

        state = self.resume_state(first)
        state["cleaning_execution"] = None

        resumed = self.invoke(state)

        self.assertEqual(resumed["status"], "failed")
        self.assertEqual(
            resumed["execution_trace"],
            ["cleaning_resume"],
        )

    def test_post_approval_flag_alone_rejected(self):
        state = self.initial_state()
        state["cleaning_large_deletion_approved"] = True

        result = self.invoke(state)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            result["execution_trace"],
            ["cleaning_resume"],
        )
        self.assertEqual(self.cleaning_calls, 0)

    def test_remaining_negative_qty_blocks_report(self):
        first = self.first_run()

        resumed = self.invoke(
            self.resume_state(first)
        )

        self.assertEqual(
            resumed["cleaning_quality_status"],
            "error",
        )

        self.assertTrue(any(
            "负值" in str(error)
            for error in resumed["cleaning_quality"]["errors"]
        ))

        self.assertEqual(self.report_calls, 0)


if __name__ == "__main__":
    unittest.main()