
"""
真实 CleaningAgent 系统评估运行入口。

功能：
1. 调用真实 CleaningAgent + mcp-pandas
2. 执行现有 LangGraph Workflow
3. 执行真实 Cleaning Quality Gate
4. 记录执行轨迹、HITL、质量检查结果和耗时
5. 保存 JSON 运行证据

本脚本不会模拟人工批准，也不会调用真实 ReportAgent。
如果命中 HITL，保留暂停状态供后续评估。

运行：
python -m tests.system_eval.run_real_cleaning
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from core.agents.cleaning_agent import CleaningAgent
from core.graph.nodes.cleaning_node import build_cleaning_node
from core.graph.workflow import build_logistics_workflow


EVAL_DIR = Path(__file__).resolve().parent
DATA_DIR = EVAL_DIR / "data"
RESULT_DIR = EVAL_DIR / "results"

SOURCE_FILE = DATA_DIR / "real_dirty_orders.csv"
CLEANED_FILE = DATA_DIR / "real_dirty_orders_cleaned.csv"

EVIDENCE_FILE = RESULT_DIR / "cleaning_runtime_evidence.json"


async def unused_sql_node(state):
    raise RuntimeError("不应调用 SQLAgent")


async def unused_forecast_node(state):
    raise RuntimeError("不应调用 ForecastAgent")


async def unused_report_node(state):
    raise RuntimeError(
        "当前脚本不执行真实 ReportAgent；"
        "请单独评估报告质量。"
    )


def json_safe(value):
    return json.loads(
        json.dumps(
            value,
            ensure_ascii=False,
            default=str,
        )
    )


async def main():
    if not SOURCE_FILE.is_file():
        raise FileNotFoundError(
            f"评估原始文件不存在：{SOURCE_FILE}"
        )

    # 不自动覆盖已有评估结果。
    if CLEANED_FILE.exists():
        raise FileExistsError(
            f"清洗结果已存在：{CLEANED_FILE}\n"
            "请先运行离线评估并保存结果。"
            "如需重新运行，请手动备份或移走旧文件。"
        )

    RESULT_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 65)
    print("REAL CLEANING AGENT EVALUATION")
    print("=" * 65)
    print("原始文件:", SOURCE_FILE)
    print("预期清洗文件:", CLEANED_FILE)
    print("开始调用真实 CleaningAgent...")

    agent = CleaningAgent()
    real_cleaning_node = build_cleaning_node(agent)

    cleaning_calls = {"count": 0}

    async def counting_cleaning_node(state):
        cleaning_calls["count"] += 1
        return await real_cleaning_node(state)

    graph = build_logistics_workflow(
        sql_node=unused_sql_node,
        cleaning_node=counting_cleaning_node,
        forecast_node=unused_forecast_node,
        report_node=unused_report_node,
    )

    initial_state = {
        "user_query": (
            "请检查并清洗这份订单数据，重点处理"
            "完全重复记录、缺失数量、负数数量、"
            "极端异常值、SKU格式和仓库编码。"
            "必须保留原始时间信息；"
            "歧义日期不得猜测；"
            "无业务依据不得编造缺失仓库。"
            "请按项目 CleaningAgent 规则实际落盘并复核。"
        ),
        "task_mode": "cleaning_report",
        "data_file_path": str(SOURCE_FILE.resolve()),
        "status": "running",
        "execution_trace": [],
        "needs_human_input": False,
    }

    started = time.perf_counter()
    start_time = datetime.now(timezone.utc).isoformat()

    try:
        result = await graph.ainvoke(initial_state)
    except Exception as exc:
        print("\n真实 Workflow 执行出现异常：")
        print(type(exc).__name__, str(exc))
        raise

    elapsed = round(time.perf_counter() - started, 4)

    actual_cleaned_path = result.get("cleaned_file_path")

    cleaned_exists = bool(
        actual_cleaned_path
        and Path(actual_cleaned_path).is_file()
    )

    evidence = {
        "evaluation_type": "real_cleaning_workflow",
        "started_at_utc": start_time,
        "source_file": str(SOURCE_FILE.resolve()),
        "cleaned_file_path": actual_cleaned_path,
        "cleaned_file_exists": cleaned_exists,
        "cleaning_call_count": cleaning_calls["count"],
        "elapsed_seconds": elapsed,
        "status": result.get("status"),
        "execution_trace": result.get("execution_trace", []),
        "cleaning_status": result.get("cleaning_status"),
        "cleaning_error": result.get("cleaning_error"),
        "cleaning_result": result.get("cleaning_result"),
        "cleaning_quality_status": result.get(
            "cleaning_quality_status"
        ),
        "cleaning_quality_error": result.get(
            "cleaning_quality_error"
        ),
        "cleaning_quality": result.get("cleaning_quality"),
        "needs_human_input": result.get(
            "needs_human_input", False
        ),
        "human_reason": result.get("human_reason"),
        "human_question": result.get("human_question"),
        "report_status": result.get("report_status"),
        "report_executed": (
            "report" in result.get("execution_trace", [])
        ),
    }

    EVIDENCE_FILE.write_text(
        json.dumps(
            json_safe(evidence),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\n" + "=" * 65)
    print("REAL CLEANING EVALUATION RESULTS")
    print("=" * 65)

    for key in [
        "status",
        "execution_trace",
        "cleaning_status",
        "cleaning_call_count",
        "cleaned_file_exists",
        "cleaning_quality_status",
        "needs_human_input",
        "human_reason",
        "report_executed",
        "elapsed_seconds",
    ]:
        print(f"{key}: {evidence[key]}")

    print("\n清洗执行证据已保存：")
    print(EVIDENCE_FILE)

    if not cleaned_exists:
        print(
            "\n注意：没有确认清洗文件实际存在，"
            "本次不能进行离线数据质量评分。"
        )
    elif result.get("status") == "needs_human_input":
        print(
            "\n已进入 HITL。当前没有模拟用户批准；"
            "清洗结果不会被自动放行进入报告环节。"
        )
    else:
        print(
            "\n工作流结束。请根据执行状态与"
            "Quality Gate 检查结果判断是否成功。"
        )


if __name__ == "__main__":
    asyncio.run(main())
