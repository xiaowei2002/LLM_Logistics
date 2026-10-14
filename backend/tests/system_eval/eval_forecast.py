
"""
ForecastAgent 专项评估 V1。

真实测试：
- DemandForecastAgent 内部 LangGraph
- 输入检查
- ToolMessage 审核
- HITL interrupt / resume
- 执行状态判断

模拟部分：
- LangChain Agent 的 LLM 调用
- 预测工具返回

不评估真实 LLM 模型路由准确率或预测误差。

运行：
python -m tests.system_eval.eval_forecast
"""

from __future__ import annotations

import csv
import json
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage, ToolMessage

from core.agents.demand_forecast_agent import DemandForecastAgent


ROOT = Path(__file__).resolve().parent
CASE_FILE = ROOT / "cases" / "forecast_cases.json"
RESULT_DIR = ROOT / "results" / "forecast_v1"


def _mock_forecast_result():
    """
    模拟有效结果结构。

    数值仅用于检查结果传递，
    不代表任何真实预测结果。
    """
    return {
        "status": "success",
        "forecast": {
            "M001": {
                "t_plus_1": 10.0,
                "t_plus_2": 12.0,
            }
        },
        "two_hour_peak_demand": {
            "M001": 12.0
        },
    }


def _tool_message(payload):
    return ToolMessage(
        content=json.dumps(
            payload,
            ensure_ascii=False,
        ),
        tool_call_id="mock_forecast_call",
        name=payload.get("tool", "demand_forecast"),
    )


def _build_messages(scenario):
    """
    针对不同受控场景生成 ToolMessage。

    这些消息是测试桩，不是实际模型输出。
    """

    success = _mock_forecast_result()

    if scenario in (
        "single_success",
        "clarification_resume",
    ):
        payload = {
            "tool": "demand_forecast",
            "status": "success",
            "variant": "state_conditioned",
            "result": success,
        }

    elif scenario == "compare_success":
        payload = {
            "tool": "compare_demand_forecasts",
            "status": "success",
            "baseline_result": success,
            "state_conditioned_result": success,
        }

    elif scenario == "tool_error":
        payload = {
            "tool": "demand_forecast",
            "status": "error",
            "message": "模拟预测服务错误",
        }

    elif scenario == "nested_error":
        payload = {
            "tool": "demand_forecast",
            "status": "success",
            "result": {
                "status": "error",
                "message": "模拟内部模型错误",
            },
        }

    elif scenario == "missing_forecast_field":
        payload = {
            "tool": "demand_forecast",
            "status": "success",
            "result": {
                "status": "success",
                "two_hour_peak_demand": {
                    "M001": 12.0
                },
            },
        }

    elif scenario == "no_tool_message":
        return [
            AIMessage(
                content=(
                    "预测结果：M001 未来两小时需求"
                    "分别为 10 和 12。"
                )
            )
        ]

    else:
        raise ValueError(
            f"不支持的模拟场景：{scenario}"
        )

    return [
        _tool_message(payload),
        AIMessage(
            content="模拟预测工具调用已经结束。"
        ),
    ]


class FakeLangChainAgent:
    """
    只替换外部 LLM / 工具执行阶段。

    DemandForecastAgent 内部逻辑保持真实。
    """

    def __init__(self, scenario):
        self.scenario = scenario
        self.call_count = 0

    def invoke(self, payload):
        self.call_count += 1

        scenario = self.scenario

        if scenario == "missing_time":
            raise AssertionError(
                "缺少时间时不应该调用预测 Agent。"
            )

        return {
            "messages": _build_messages(scenario)
        }


def _create_agent(scenario):
    """
    创建真实 DemandForecastAgent，但注入模拟
    LangChain Agent，避免调用外部 LLM。

    每个测试案例独立创建实例与 checkpointer。
    """
    fake = FakeLangChainAgent(scenario)

    dummy_client = SimpleNamespace(
        model=object()
    )

    with patch(
        "core.agents.demand_forecast_agent.create_agent",
        return_value=fake,
    ):
        agent = DemandForecastAgent(
            client=dummy_client
        )

    return agent, fake


def _interrupt_payload(result):
    interrupts = result.get("__interrupt__") or []

    if not interrupts:
        return None

    first = interrupts[0]

    value = getattr(first, "value", first)

    if isinstance(value, dict):
        return value

    return {
        "question": str(value)
    }


def _evaluate_case(case):
    case_id = case["case_id"]
    scenario = case["scenario"]
    expected = case["expected"]
    query = case["query"]

    start = time.perf_counter()

    observed = "unknown"
    detail = ""
    tool_calls = 0
    interrupt_reason = ""
    thread_preserved = False

    try:
        agent, fake = _create_agent(scenario)

        thread_id = (
            f"forecast_eval_{case_id}_"
            f"{uuid.uuid4().hex[:8]}"
        )

        result = agent.invoke(
            query=query,
            thread_id=thread_id,
        )

        interrupt_info = _interrupt_payload(result)

        if scenario == "clarification_resume":
            if not interrupt_info:
                observed = "no_input_interrupt"
                detail = (
                    "首次调用没有触发输入补充中断。"
                )
            else:
                first_reason = interrupt_info.get(
                    "reason"
                )

                resumed = agent.resume(
                    thread_id=thread_id,
                    human_answer=case["human_answer"],
                )

                resumed_interrupt = (
                    _interrupt_payload(resumed)
                )

                if resumed_interrupt:
                    observed = "resume_still_interrupted"
                    detail = str(resumed_interrupt)

                elif (
                    resumed.get("tool_status") == "success"
                    and bool(resumed.get("answer"))
                    and fake.call_count == 1
                ):
                    observed = "resume_success"
                    thread_preserved = True
                    detail = (
                        "先触发输入 HITL，"
                        "同一 thread_id 恢复后"
                        "完成一次模拟工具调用；"
                        f"首次原因={first_reason}"
                    )
                else:
                    observed = "resume_invalid_result"
                    detail = str({
                        "tool_status": resumed.get(
                            "tool_status"
                        ),
                        "answer": resumed.get(
                            "answer"
                        ),
                        "calls": fake.call_count,
                    })

        elif interrupt_info:
            reason = str(
                interrupt_info.get("reason") or ""
            )
            interrupt_reason = reason

            if scenario == "missing_time":
                observed = "input_interrupt"
            elif scenario == "no_tool_message":
                if reason == "tool_execution_error":
                    observed = "reject_unverified_answer"
                else:
                    observed = "incorrect_interrupt_reason"
            else:
                observed = "result_interrupt"

            detail = json.dumps(
                interrupt_info,
                ensure_ascii=False,
                default=str,
            )

        elif scenario == "no_tool_message":
            # 对照安全要求：
            # 没有 ToolMessage 的预测回答应被拒绝。
            #
            # 若当前 Agent 直接接受，则属于已发现缺口。
            if (
                result.get("tool_status") == "success"
                and bool(result.get("answer"))
            ):
                observed = "unverified_answer_accepted"
                detail = (
                    "未检测到 ToolMessage，"
                    "但 Agent 仍返回成功回答。"
                )
            else:
                observed = "reject_unverified_answer"
                detail = (
                    "无工具证据的回答未被接受。"
                )

        elif (
            result.get("tool_status") == "success"
            and bool(result.get("answer"))
        ):
            observed = "success"
            detail = (
                "真实 Agent 控制逻辑接受了"
                "模拟工具成功结果。"
            )

        else:
            observed = "unexpected_result"
            detail = json.dumps(
                {
                    "tool_status": result.get(
                        "tool_status"
                    ),
                    "tool_error": result.get(
                        "tool_error"
                    ),
                    "answer": result.get("answer"),
                },
                ensure_ascii=False,
                default=str,
            )

        tool_calls = fake.call_count

        # 对 HITL 中断做额外严格校验
        if scenario == "missing_time":
            if tool_calls != 0:
                observed = "unexpected_tool_call"

        if scenario in (
            "tool_error",
            "nested_error",
            "missing_forecast_field",
        ):
            if interrupt_reason != "tool_execution_error":
                observed = "incorrect_interrupt_reason"

        passed = observed == expected

    except Exception as exc:
        passed = False
        observed = "exception"
        detail = f"{type(exc).__name__}: {exc}"

    elapsed = round(
        time.perf_counter() - start,
        4,
    )

    return {
        "case_id": case_id,
        "name": case["name"],
        "scenario": scenario,
        "expected": expected,
        "observed": observed,
        "passed": passed,
        "tool_call_count": tool_calls,
        "interrupt_reason": interrupt_reason,
        "thread_preserved": thread_preserved,
        "elapsed_seconds": elapsed,
        "detail": detail,
    }


def main():
    if not CASE_FILE.is_file():
        raise FileNotFoundError(
            f"找不到评估案例：{CASE_FILE}"
        )

    with CASE_FILE.open(
        "r",
        encoding="utf-8",
    ) as file:
        config = json.load(file)

    cases = config["cases"]

    if not cases:
        raise ValueError(
            "Forecast 评估案例不能为空。"
        )

    ids = [case["case_id"] for case in cases]

    if len(ids) != len(set(ids)):
        raise ValueError(
            "Forecast 评估案例 ID 存在重复。"
        )

    RESULT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    records = []

    for case in cases:
        record = _evaluate_case(case)
        records.append(record)

        label = (
            "PASS"
            if record["passed"]
            else "FAIL"
        )

        print(
            f"[{label}] {record['case_id']} "
            f"{record['name']}"
        )
        print(
            f"  expected={record['expected']}"
        )
        print(
            f"  observed={record['observed']}"
        )

        if not record["passed"]:
            print(
                f"  detail={record['detail']}"
            )

    total = len(records)
    passed_count = sum(
        int(record["passed"])
        for record in records
    )

    known_risk_cases = {
        case["case_id"]
        for case in cases
        if case.get("known_risk_probe")
    }

    standard_records = [
        record
        for record in records
        if record["case_id"] not in known_risk_cases
    ]

    standard_passed = sum(
        int(record["passed"])
        for record in standard_records
    )

    summary = {
        "evaluation_id": config["evaluation_id"],
        "evaluation_type": config["evaluation_type"],
        "total_cases": total,
        "passed_cases": passed_count,
        "failed_cases": total - passed_count,
        "overall_case_pass_rate": round(
            passed_count / total, 4
        ),
        "standard_cases": len(standard_records),
        "standard_passed": standard_passed,
        "standard_pass_rate": round(
            standard_passed / len(standard_records),
            4,
        ) if standard_records else None,
        "known_risk_probe_cases": sorted(
            known_risk_cases
        ),
        "real_llm_used": False,
        "real_forecast_model_used": False,
        "model_routing_accuracy": None,
        "forecast_mae": None,
        "forecast_rmse": None,
        "forecast_wmape": None,
        "note": (
            "仅评价真实 Agent 控制逻辑与"
            "受控模拟工具返回的交互。"
            "不代表真实预测准确率或模型路由正确率。"
        ),
    }

    csv_path = (
        RESULT_DIR / "forecast_case_results.csv"
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
        RESULT_DIR / "forecast_summary.json"
    )

    json_path.write_text(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\n=== Forecast Evaluation V1 ===")
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