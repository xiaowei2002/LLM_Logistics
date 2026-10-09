"""
真实 Cleaning -> Quality Gate -> HITL -> Report
多智能体链路集成测试。

该测试会真实调用：

1. CleaningAgent
2. mcp-pandas
3. Cleaning Node
4. Cleaning Quality Gate
5. Human-in-the-loop 大比例删行确认
6. ReportAgent
7. 外层 LangGraph Workflow

测试分为两个阶段：

第一阶段：
    CleaningAgent
    -> mcp-pandas
    -> Cleaning Node
    -> Cleaning Quality Gate
    -> needs_human_input

第二阶段：
    模拟用户确认大比例删除
    -> Cleaning Quality Gate
    -> ReportAgent
    -> completed

第二阶段不得再次调用 CleaningAgent。

默认跳过。
运行前需要设置：

RUN_INTEGRATION_TESTS=1
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from core.agents.cleaning_agent import CleaningAgent  # noqa: E402
from core.agents.report_agent import ReportAgent  # noqa: E402
from core.graph.nodes.cleaning_node import build_cleaning_node  # noqa: E402
from core.graph.nodes.report_node import build_report_node  # noqa: E402
from core.graph.workflow import build_logistics_workflow  # noqa: E402


async def unused_sql_node(state):
    """
    本测试只验证 cleaning_report 链路。

    如果错误调用 SQL Node，立即失败。
    """

    raise RuntimeError(
        "本测试不应调用 SQL Node。"
    )


def unused_forecast_node(state):
    """
    本测试只验证 cleaning_report 链路。

    如果错误调用 Forecast Node，立即失败。
    """

    raise RuntimeError(
        "本测试不应调用 Forecast Node。"
    )


@unittest.skipUnless(
    os.getenv("RUN_INTEGRATION_TESTS") == "1",
    "设置 RUN_INTEGRATION_TESTS=1 后运行真实集成测试。",
)
class RealCleaningReportWorkflowTest(
    unittest.IsolatedAsyncioTestCase
):

    async def test_real_cleaning_report_workflow(
        self,
    ):
        """
        验证真实两阶段链路：

        第一阶段：

        CleaningAgent
        -> mcp-pandas
        -> Cleaning Node
        -> Cleaning Quality Gate
        -> HITL

        第二阶段：

        人工确认
        -> Cleaning Quality Gate
        -> ReportAgent
        -> Report Node

        同时验证：
        - 清洗文件真实落盘
        - 删除比例真实计算
        - >10% 自动删除会触发 HITL
        - HITL 前 Report 不执行
        - 人工确认后不重新执行 CleaningAgent
        - 人工确认后仍重新经过 Quality Gate
        - 最终 Report 正常生成
        """

        # ==================================================
        # 1. 准备真实测试数据
        # ==================================================

        test_file = (
            BACKEND_ROOT
            / "tests"
            / "real_dirty_orders.csv"
        )

        self.assertTrue(
            test_file.exists(),
            f"测试数据不存在：{test_file}",
        )

        expected_cleaned_file = (
            test_file.with_name(
                f"{test_file.stem}_cleaned"
                f"{test_file.suffix}"
            )
        ).resolve()

        # 删除历史输出，
        # 防止旧文件导致本次结果误判。
        expected_cleaned_file.unlink(
            missing_ok=True
        )

        # ==================================================
        # 2. 创建真实 Agent
        # ==================================================

        cleaning_agent = CleaningAgent()
        report_agent = ReportAgent()

        # ==================================================
        # 3. 创建真实 Adapter Nodes
        # ==================================================

        real_cleaning_node = build_cleaning_node(
            cleaning_agent
        )

        report_node = build_report_node(
            report_agent
        )

        # ==================================================
        # 4. 包装 Cleaning Node，记录真实调用次数
        # ==================================================

        cleaning_call_count = {
            "count": 0
        }

        async def counting_cleaning_node(
            state,
        ):
            """
            包装真实 Cleaning Node。

            用于验证人工确认后的第二阶段
            不会再次执行 CleaningAgent。
            """

            cleaning_call_count[
                "count"
            ] += 1

            return await real_cleaning_node(
                state
            )

        # ==================================================
        # 5. 构建真实外层 LangGraph
        # ==================================================

        graph = build_logistics_workflow(
            sql_node=unused_sql_node,
            cleaning_node=(
                counting_cleaning_node
            ),
            forecast_node=(
                unused_forecast_node
            ),
            report_node=report_node,
        )

        # ==================================================
        # 6. 第一阶段：
        #    真实 Cleaning -> Quality Gate -> HITL
        # ==================================================

        first_result = await graph.ainvoke(
            {
                "user_query": (
                    "请检查并清洗这份订单数据，"
                    "重点处理重复记录、缺失值、"
                    "负数数量、极端异常值、"
                    "SKU格式和仓库编码不一致问题。"
                ),
                "task_mode": (
                    "cleaning_report"
                ),
                "data_file_path": str(
                    test_file
                ),
                "execution_trace": [],
                "status": "running",
            }
        )

        # ==================================================
        # 7. 打印第一阶段结果
        # ==================================================

        print("\n")
        print("=" * 78)
        print(
            "REAL CLEANING WORKFLOW - PHASE 1"
        )
        print(
            "CLEANING -> QUALITY GATE -> HITL"
        )
        print("=" * 78)

        print(
            "status =",
            first_result.get(
                "status"
            ),
        )

        print(
            "execution_trace =",
            first_result.get(
                "execution_trace"
            ),
        )

        print(
            "cleaning_call_count =",
            cleaning_call_count[
                "count"
            ],
        )

        print(
            "cleaning_status =",
            first_result.get(
                "cleaning_status"
            ),
        )

        print(
            "cleaning_error =",
            first_result.get(
                "cleaning_error"
            ),
        )

        print(
            "cleaned_file_path =",
            first_result.get(
                "cleaned_file_path"
            ),
        )

        print(
            "\n--- Cleaning Result ---"
        )

        print(
            first_result.get(
                "cleaning_result"
            )
        )

        print(
            "\ncleaning_quality_status =",
            first_result.get(
                "cleaning_quality_status"
            ),
        )

        print(
            "cleaning_quality_error =",
            first_result.get(
                "cleaning_quality_error"
            ),
        )

        print(
            "\n--- Cleaning Quality Result ---"
        )

        print(
            first_result.get(
                "cleaning_quality"
            )
        )

        print(
            "\nneeds_human_input =",
            first_result.get(
                "needs_human_input"
            ),
        )

        print(
            "human_reason =",
            first_result.get(
                "human_reason"
            ),
        )

        print(
            "human_question =",
            first_result.get(
                "human_question"
            ),
        )

        print(
            "\nreport_status =",
            first_result.get(
                "report_status"
            ),
        )

        print("=" * 78)

        # ==================================================
        # 8. 第一阶段：Cleaning Node 断言
        # ==================================================

        self.assertEqual(
            cleaning_call_count[
                "count"
            ],
            1,
        )

        self.assertEqual(
            first_result.get(
                "cleaning_status"
            ),
            "success",
        )

        self.assertIsNone(
            first_result.get(
                "cleaning_error"
            )
        )

        self.assertTrue(
            first_result.get(
                "cleaning_result"
            )
        )

        # 必须使用确定性输出路径。
        self.assertEqual(
            first_result.get(
                "cleaned_file_path"
            ),
            str(
                expected_cleaned_file
            ),
        )

        # 清洗文件必须真实存在。
        self.assertTrue(
            expected_cleaned_file.exists(),
            (
                "CleaningAgent 声称清洗成功，"
                "但清洗文件没有真实落盘："
                f"{expected_cleaned_file}"
            ),
        )

        # ==================================================
        # 9. 第一阶段：Quality Gate HITL 断言
        # ==================================================

        self.assertEqual(
            first_result.get(
                "status"
            ),
            "needs_human_input",
        )

        self.assertTrue(
            first_result.get(
                "needs_human_input"
            )
        )

        self.assertEqual(
            first_result.get(
                "human_reason"
            ),
            "large_deletion",
        )

        self.assertTrue(
            first_result.get(
                "human_question"
            )
        )

        self.assertEqual(
            first_result.get(
                "cleaning_quality_status"
            ),
            "needs_human_input",
        )

        quality_phase_1 = (
            first_result.get(
                "cleaning_quality"
            )
        )

        self.assertIsInstance(
            quality_phase_1,
            dict,
        )

        # Gate 原始结果此时应为未通过，
        # 因为还没有人工确认。
        self.assertFalse(
            quality_phase_1.get(
                "passed"
            )
        )

        self.assertTrue(
            quality_phase_1.get(
                "large_deletion_detected"
            )
        )

        self.assertEqual(
            quality_phase_1.get(
                "source_row_count"
            ),
            8,
        )

        cleaned_row_count = (
            quality_phase_1.get(
                "cleaned_row_count"
            )
        )

        removed_row_count = (
            quality_phase_1.get(
                "removed_row_count"
            )
        )

        removal_rate = (
            quality_phase_1.get(
                "removal_rate"
            )
        )

        self.assertIsInstance(
            cleaned_row_count,
            int,
        )

        self.assertIsInstance(
            removed_row_count,
            int,
        )

        self.assertIsInstance(
            removal_rate,
            (int, float),
        )

        self.assertGreater(
            removed_row_count,
            0,
        )

        self.assertGreater(
            removal_rate,
            0.10,
        )

        # 第一阶段必须停止在 Quality Gate。
        self.assertEqual(
            first_result.get(
                "execution_trace"
            ),
            [
                "cleaning",
                "cleaning_quality",
            ],
        )

        # HITL 前绝对不能生成 Report。
        self.assertNotIn(
            "report",
            first_result.get(
                "execution_trace",
                [],
            ),
        )

        self.assertFalse(
            first_result.get(
                "report_result"
            )
        )

        # ==================================================
        # 10. 模拟用户明确确认大比例删除
        # ==================================================

        resume_state = dict(
            first_result
        )

        resume_state[
            "cleaning_large_deletion_approved"
        ] = True

        resume_state[
            "status"
        ] = "running"

        # 第二次 graph invocation 是一个新的执行阶段，
        # 因此 trace 从空列表重新开始记录。
        resume_state[
            "execution_trace"
        ] = []

        # ==================================================
        # 11. 第二阶段：
        #     Quality Gate -> Report
        # ==================================================

        second_result = await graph.ainvoke(
            resume_state
        )

        # ==================================================
        # 12. 打印第二阶段结果
        # ==================================================

        print("\n")
        print("=" * 78)
        print(
            "REAL CLEANING WORKFLOW - PHASE 2"
        )
        print(
            "HUMAN APPROVAL -> QUALITY GATE -> REPORT"
        )
        print("=" * 78)

        print(
            "status =",
            second_result.get(
                "status"
            ),
        )

        print(
            "execution_trace =",
            second_result.get(
                "execution_trace"
            ),
        )

        print(
            "cleaning_call_count =",
            cleaning_call_count[
                "count"
            ],
        )

        print(
            "cleaning_large_deletion_approved =",
            second_result.get(
                "cleaning_large_deletion_approved"
            ),
        )

        print(
            "cleaning_quality_status =",
            second_result.get(
                "cleaning_quality_status"
            ),
        )

        print(
            "cleaning_quality_error =",
            second_result.get(
                "cleaning_quality_error"
            ),
        )

        print(
            "\n--- Cleaning Quality Result After Approval ---"
        )

        print(
            second_result.get(
                "cleaning_quality"
            )
        )

        print(
            "\nneeds_human_input =",
            second_result.get(
                "needs_human_input"
            ),
        )

        print(
            "human_reason =",
            second_result.get(
                "human_reason"
            ),
        )

        print(
            "\nreport_status =",
            second_result.get(
                "report_status"
            ),
        )

        print(
            "report_error =",
            second_result.get(
                "report_error"
            ),
        )

        print(
            "\n--- Final Report ---"
        )

        print(
            second_result.get(
                "report_result"
            )
        )

        print("=" * 78)

        # ==================================================
        # 13. 第二阶段：不能重新执行 CleaningAgent
        # ==================================================

        self.assertEqual(
            cleaning_call_count[
                "count"
            ],
            1,
            (
                "人工确认后不应再次调用 CleaningAgent。"
                "应该直接复用第一次已经落盘的清洗文件。"
            ),
        )

        # ==================================================
        # 14. 第二阶段：Workflow 终态
        # ==================================================

        self.assertEqual(
            second_result.get(
                "status"
            ),
            "completed",
        )

        # ==================================================
        # 15. 第二阶段：Quality Gate 放行
        # ==================================================

        self.assertEqual(
            second_result.get(
                "cleaning_quality_status"
            ),
            "success",
        )

        self.assertIsNone(
            second_result.get(
                "cleaning_quality_error"
            )
        )

        quality_phase_2 = (
            second_result.get(
                "cleaning_quality"
            )
        )

        self.assertIsInstance(
            quality_phase_2,
            dict,
        )

        self.assertTrue(
            quality_phase_2.get(
                "passed"
            )
        )

        # 大比例删除事实仍然必须保留，
        # 不能因为人工确认就把风险事实抹掉。
        self.assertTrue(
            quality_phase_2.get(
                "large_deletion_detected"
            )
        )

        # 必须记录已经得到人工确认。
        self.assertTrue(
            quality_phase_2.get(
                "large_deletion_approved"
            )
        )

        self.assertGreater(
            quality_phase_2.get(
                "removal_rate",
                0,
            ),
            0.10,
        )

        # ==================================================
        # 16. 第二阶段：HITL 状态必须清除
        # ==================================================

        self.assertFalse(
            second_result.get(
                "needs_human_input"
            )
        )

        self.assertIsNone(
            second_result.get(
                "human_reason"
            )
        )

        self.assertIsNone(
            second_result.get(
                "human_question"
            )
        )

        # ==================================================
        # 17. 第二阶段：Report 必须成功
        # ==================================================

        self.assertEqual(
            second_result.get(
                "report_status"
            ),
            "success",
        )

        self.assertIsNone(
            second_result.get(
                "report_error"
            )
        )

        self.assertTrue(
            second_result.get(
                "report_result"
            )
        )

        # ==================================================
        # 18. 第二阶段执行轨迹
        # ==================================================

        self.assertEqual(
            second_result.get(
                "execution_trace"
            ),
            [
                "cleaning_quality",
                "report",
            ],
        )

        # ==================================================
        # 19. 输出文件最终仍然存在
        # ==================================================

        self.assertTrue(
            expected_cleaned_file.exists()
        )


if __name__ == "__main__":
    unittest.main()