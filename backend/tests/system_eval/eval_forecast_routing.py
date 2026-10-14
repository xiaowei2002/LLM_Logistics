
"""
ForecastAgent V2：真实 LLM 模型路由评估。

真实组件：
- 项目配置的 LLM
- LangChain create_agent
- DemandForecastAgent LangGraph
- 输入检查与 HITL

模拟组件：
- demand_forecast 的预测计算
- compare_demand_forecasts 的预测计算

运行：
python -m tests.system_eval.eval_forecast_routing

不会调用真实需求预测模型。
不会修改 ForecastAgent 源代码。
"""

from __future__ import annotations

import csv
import json
import time
import uuid
from pathlib import Path
from unittest.mock import patch

from langchain_core.tools import tool

from core.agents import demand_forecast_agent as agent_module
from core.llm import get_llm
from core.tools.demand_forecast.demand_forecast_tool import (
    DemandForecastInput,
    ForecastComparisonInput,
)


ROOT = Path(__file__).resolve().parent
CASE_PATH = ROOT / "cases" / "forecast_routing_cases.json"
RESULT_DIR = ROOT / "results" / "forecast_routing_v2"


def mock_model_result(variant, timestamp):
    """
    模拟预测结构，不代表真实预测性能。
    """
    values = {
        "baseline_sh": 10.0,
        "state_only": 11.0,
        "state_conditioned": 12.0,
    }

    value = values[variant]

    forecast = {}
    peaks = {}

    for index in range(1, 9):
        material = f"M{index:03d}"

        forecast[material] = {
            "t_plus_1": value,
            "t_plus_2": value + 2.0,
        }

        peaks[material] = value + 2.0

    return {
        "status": "success",
        "variant": variant,
        "forecast_timestamp": timestamp,
        "forecast": forecast,
        "two_hour_peak_demand": peaks,
    }


def make_mock_tools(call_log):
    """
    创建与原有工具同名、同参数 Schema 的模拟工具。

    call_log 记录 LLM 实际发起的工具调用，
    不根据案例预期自动指定工具或 variant。
    """

    @tool(
        "demand_forecast",
        args_schema=DemandForecastInput,
    )
    def mock_demand_forecast(
        forecast_timestamp: str,
        variant: str = "state_conditioned",
    ) -> dict:
        """使用指定预测模型预测未来两小时物料需求。"""

        call_log.append({
            "tool": "demand_forecast",
            "forecast_timestamp": forecast_timestamp,
            "variant": variant,
        })

        return {
            "tool": "demand_forecast",
            "status": "success",
            "forecast_timestamp": forecast_timestamp,
            "variant": variant,
            "result": mock_model_result(
                variant, forecast_timestamp
            ),
        }

    @tool(
        "compare_demand_forecasts",
        args_schema=ForecastComparisonInput,
    )
    def mock_compare_demand_forecasts(
        forecast_timestamp: str,
    ) -> dict:
        """比较同一预测时刻的基线模型与最终模型。"""

        call_log.append({
            "tool": "compare_demand_forecasts",
            "forecast_timestamp": forecast_timestamp,
            "variant": None,
        })

        baseline = mock_model_result(
            "baseline_sh", forecast_timestamp
        )

        final = mock_model_result(
            "state_conditioned", forecast_timestamp
        )

        difference = {
            material: (
                final["two_hour_peak_demand"][material]
                - baseline["two_hour_peak_demand"][material]
            )
            for material in baseline["two_hour_peak_demand"]
        }

        return {
            "tool": "compare_demand_forecasts",
            "status": "success",
            "forecast_timestamp": forecast_timestamp,
            "comparison_basis": (
                "Experiment 3 same data source "
                "and same forecast timestamp"
            ),
            "baseline_result": baseline,
            "state_conditioned_result": final,
            "peak_difference_state_conditioned_minus_baseline": (
                difference
            ),
        }

    return [
        mock_demand_forecast,
        mock_compare_demand_forecasts,
    ]


def extract_interrupt(result):
    interrupts = result.get("__interrupt__") or []

    if not interrupts:
        return None

    item = interrupts[0]
    payload = getattr(item, "value", item)

    return payload if isinstance(payload, dict) else {
        "question": str(payload)
    }


def evaluate_case(case, llm_client):
    calls = []

    started = time.perf_counter()

    record = {
        "case_id": case["case_id"],
        "name": case["name"],
        "expected_tool": case["expected_tool"] or "",
        "expected_variant": case["expected_variant"] or "",
        "expected_timestamp": (
            case["expected_timestamp"] or ""
        ),
        "actual_tool": "",
        "actual_variant": "",
        "actual_timestamp": "",
        "tool_call_count": 0,
        "interrupt_reason": "",
        "tool_match": False,
        "variant_match": False,
        "timestamp_match": False,
        "route_passed": False,
        "status": "unknown",
        "elapsed_seconds": 0.0,
        "detail": "",
    }

    try:
        mock_tools = make_mock_tools(calls)

        # 只在创建 ForecastAgent 时替换工具注册表。
        # LLM 本身仍然是真实模型。
        with patch.object(
            agent_module,
            "DEMAND_FORECAST_TOOLS",
            mock_tools,
        ):
            agent = agent_module.DemandForecastAgent(
                client=llm_client
            )

        thread_id = (
            f"routing_{case['case_id']}_"
            f"{uuid.uuid4().hex}"
        )

        result = agent.invoke(
            query=case["query"],
            thread_id=thread_id,
        )

        interrupt_info = extract_interrupt(result)

        if interrupt_info:
            record["interrupt_reason"] = str(
                interrupt_info.get("reason") or ""
            )

        record["tool_call_count"] = len(calls)

        if case["expected_tool"] is None:
            # 输入不完整：必须触发输入澄清，
            # 且不能提前调用任何预测工具。
            is_clarification = bool(
                interrupt_info
                and interrupt_info.get("type")
                == "clarification"
            )

            record["tool_match"] = len(calls) == 0
            record["variant_match"] = True
            record["timestamp_match"] = True

            record["route_passed"] = (
                is_clarification
                and len(calls) == 0
            )

            record["status"] = (
                "pass" if record["route_passed"]
                else "fail"
            )

            record["detail"] = (
                "输入澄清中断检查；"
                f"实际原因：{record['interrupt_reason']}"
            )

        else:
            if calls:
                first_call = calls[0]

                record["actual_tool"] = (
                    first_call["tool"]
                )
                record["actual_variant"] = (
                    first_call["variant"] or ""
                )
                record["actual_timestamp"] = (
                    first_call["forecast_timestamp"]
                )

            # 当前任务期望一次工具调用。
            # 重复调用或额外调用不视为完全正确。
            record["tool_match"] = (
                len(calls) == 1
                and calls[0]["tool"]
                == case["expected_tool"]
            )

            record["variant_match"] = (
                len(calls) == 1
                and calls[0]["variant"]
                == case["expected_variant"]
            )

            record["timestamp_match"] = (
                len(calls) == 1
                and calls[0]["forecast_timestamp"]
                == case["expected_timestamp"]
            )

            successful_answer = (
                not interrupt_info
                and result.get("tool_status") == "success"
                and bool(result.get("answer"))
            )

            record["route_passed"] = all((
                record["tool_match"],
                record["variant_match"],
                record["timestamp_match"],
                successful_answer,
            ))

            record["status"] = (
                "pass" if record["route_passed"]
                else "fail"
            )

            record["detail"] = (
                "模拟工具执行，真实 LLM 路由；"
                f"tool_status={result.get('tool_status')}; "
                f"interrupt={bool(interrupt_info)}"
            )

    except Exception as exc:
        record["status"] = "error"
        record["detail"] = (
            f"{type(exc).__name__}: {exc}"
        )
        record["tool_call_count"] = len(calls)

    record["elapsed_seconds"] = round(
        time.perf_counter() - started, 4
    )

    return record


def main():
    with CASE_PATH.open(
        "r", encoding="utf-8"
    ) as file:
        config = json.load(file)

    cases = config["cases"]

    if len(cases) != 8:
        raise ValueError(
            "V2 固定评估要求 8 个案例。"
        )

    ids = [case["case_id"] for case in cases]

    if len(ids) != len(set(ids)):
        raise ValueError(
            "案例 ID 不能重复。"
        )

    print("=== Forecast Routing V2 ===")
    print("真实 LLM：是")
    print("真实预测模型：否")
    print("预测工具：模拟")
    print("正在初始化项目 LLM 客户端...")

    # 使用项目现有 LLM 配置。
    # 不输出 API Key、base_url 或其他认证信息。
    llm_client = get_llm()

    records = []

    for case in cases:
        print(
            f"\n正在评估 {case['case_id']}："
            f"{case['name']}"
        )

        record = evaluate_case(
            case, llm_client
        )

        records.append(record)

        print(
            f"  status={record['status']}"
        )
        print(
            f"  expected_tool={record['expected_tool']}"
        )
        print(
            f"  actual_tool={record['actual_tool']}"
        )
        print(
            f"  expected_variant={record['expected_variant']}"
        )
        print(
            f"  actual_variant={record['actual_variant']}"
        )
        print(
            f"  calls={record['tool_call_count']}"
        )
        print(
            f"  elapsed={record['elapsed_seconds']}s"
        )

        if record["status"] != "pass":
            print(
                f"  detail={record['detail']}"
            )

    completed = [
        item for item in records
        if item["status"] in ("pass", "fail")
    ]

    errors = [
        item for item in records
        if item["status"] == "error"
    ]

    passed = sum(
        item["route_passed"]
        for item in completed
    )

    prediction_records = [
        item for item in completed
        if item["expected_tool"]
    ]

    tool_correct = sum(
        item["tool_match"]
        for item in prediction_records
    )
    variant_correct = sum(
        item["variant_match"]
        for item in prediction_records
    )
    timestamp_correct = sum(
        item["timestamp_match"]
        for item in prediction_records
    )

    def rate(numerator, denominator):
        return (
            round(numerator / denominator, 4)
            if denominator else None
        )

    summary = {
        "evaluation_id": config["evaluation_id"],
        "evaluation_type": config["evaluation_type"],
        "total_cases": len(records),
        "completed_cases": len(completed),
        "error_cases": len(errors),
        "passed_cases": passed,
        "case_pass_rate": rate(
            passed, len(completed)
        ),
        "tool_selection_accuracy": rate(
            tool_correct, len(prediction_records)
        ),
        "variant_accuracy": rate(
            variant_correct, len(prediction_records)
        ),
        "timestamp_accuracy": rate(
            timestamp_correct, len(prediction_records)
        ),
        "average_elapsed_seconds": round(
            sum(
                item["elapsed_seconds"]
                for item in records
            ) / len(records),
            4,
        ),
        "real_llm_used": True,
        "real_forecast_model_used": False,
        "forecast_mae": None,
        "forecast_rmse": None,
        "forecast_wmape": None,
        "note": (
            "真实 LLM 进行工具选择，"
            "预测工具执行由模拟函数替代。"
            "工具调用次数要求为 1。"
            "服务异常与路由失败分别统计。"
        ),
    }

    RESULT_DIR.mkdir(
        parents=True, exist_ok=True
    )

    csv_path = (
        RESULT_DIR /
        "forecast_routing_case_results.csv"
    )

    with csv_path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=list(records[0].keys()),
        )
        writer.writeheader()
        writer.writerows(records)

    json_path = (
        RESULT_DIR /
        "forecast_routing_summary.json"
    )

    json_path.write_text(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\n=== Evaluation Summary ===")
    print(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
        )
    )

    print("\n结果文件：")
    print(csv_path)
    print(json_path)


if __name__ == "__main__":
    main()