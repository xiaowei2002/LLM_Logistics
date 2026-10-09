"""
真实 Forecast -> Report 多智能体链路集成测试。

该测试会真实调用：
1. DemandForecastAgent
2. demand_forecast Tool
3. Experiment 3 预测数据与模型
4. ReportAgent
5. 外层 LangGraph Workflow

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

from core.agents.demand_forecast_agent import DemandForecastAgent  # noqa: E402
from core.agents.report_agent import ReportAgent  # noqa: E402
from core.graph.nodes.forecast_node import build_forecast_node  # noqa: E402
from core.graph.nodes.report_node import build_report_node  # noqa: E402
from core.graph.workflow import build_logistics_workflow  # noqa: E402


async def unused_sql_node(state):
    raise RuntimeError(
        "本测试不应调用 SQL Node。"
    )


async def unused_cleaning_node(state):
    raise RuntimeError(
        "本测试不应调用 Cleaning Node。"
    )


@unittest.skipUnless(
    os.getenv("RUN_INTEGRATION_TESTS") == "1",
    "设置 RUN_INTEGRATION_TESTS=1 后运行真实集成测试。",
)
class RealForecastReportWorkflowTest(
    unittest.IsolatedAsyncioTestCase
):

    async def test_real_forecast_report_workflow(self):

        # ==================================================
        # 1. 创建真实 Agent
        # ==================================================

        forecast_agent = DemandForecastAgent()
        report_agent = ReportAgent()

        # ==================================================
        # 2. 创建 Adapter Nodes
        # ==================================================

        forecast_node = build_forecast_node(
            forecast_agent
        )

        report_node = build_report_node(
            report_agent
        )

        # ==================================================
        # 3. 构建外层 LangGraph
        # ==================================================

        graph = build_logistics_workflow(
            sql_node=unused_sql_node,
            cleaning_node=unused_cleaning_node,
            forecast_node=forecast_node,
            report_node=report_node,
        )

        # ==================================================
        # 4. 执行真实 forecast_report
        # ==================================================

        result = await graph.ainvoke(
            {
                "user_query": (
                    "请预测2026年1月30日12点之后"
                    "未来两小时M001到M008的具体物料需求数量。"
                ),
                "task_mode": "forecast_report",
                "execution_trace": [],
                "status": "running",
            }
        )

        # ==================================================
        # 5. 打印真实执行结果
        # ==================================================

        print("\n")
        print("=" * 70)
        print("REAL FORECAST -> REPORT WORKFLOW")
        print("=" * 70)

        print(
            "status =",
            result.get("status"),
        )

        print(
            "execution_trace =",
            result.get("execution_trace"),
        )

        print(
            "forecast_status =",
            result.get("forecast_status"),
        )

        print(
            "forecast_error =",
            result.get("forecast_error"),
        )

        print(
            "forecast_thread_id =",
            result.get("forecast_thread_id"),
        )

        forecast_result = result.get(
            "forecast_result"
        )

        if isinstance(
            forecast_result,
            dict,
        ):
            print("\n--- Forecast Answer ---")
            print(
                forecast_result.get(
                    "answer"
                )
            )

        print(
            "\nreport_status =",
            result.get("report_status"),
        )

        print(
            "report_error =",
            result.get("report_error"),
        )

        print("\n--- Final Report ---")
        print(
            result.get("report_result")
        )

        print("=" * 70)

        # ==================================================
        # 6. 结果断言
        # ==================================================

        self.assertEqual(
            result.get("status"),
            "completed",
        )

        self.assertEqual(
            result.get("forecast_status"),
            "success",
        )

        self.assertEqual(
            result.get("report_status"),
            "success",
        )

        self.assertEqual(
            result.get("execution_trace"),
            [
                "forecast",
                "report",
            ],
        )

        self.assertIsInstance(
            forecast_result,
            dict,
        )

        self.assertTrue(
            forecast_result.get(
                "answer"
            )
        )

        self.assertTrue(
            result.get(
                "report_result"
            )
        )


if __name__ == "__main__":
    unittest.main()