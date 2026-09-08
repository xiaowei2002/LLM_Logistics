from __future__ import annotations

from typing import Any, Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from .state_conditioned_forecast_tool import (
    run_state_conditioned_forecast,
)


# ==========================================================
# 1. Tool 输入 Schema
# ==========================================================

ForecastVariant = Literal[
    "baseline_sh",
    "state_only",
    "state_conditioned",
]


class DemandForecastInput(BaseModel):
    """
    单模型需求预测工具输入。
    """

    forecast_timestamp: str = Field(
        ...,
        description=(
            "预测基准时刻，格式为 YYYY-MM-DD HH:MM:SS。"
            "例如：2026-01-30 12:00:00。"
        ),
    )

    variant: ForecastVariant = Field(
        default="state_conditioned",
        description=(
            "预测模型类型。"
            "baseline_sh 表示 Experiment 3 Structured Hybrid 基线；"
            "state_only 表示仅使用多源状态的 Ridge 模型；"
            "state_conditioned 表示最终 State-Conditioned Structured Hybrid 模型。"
        ),
    )


class ForecastComparisonInput(BaseModel):
    """
    同数据源模型比较工具输入。
    """

    forecast_timestamp: str = Field(
        ...,
        description=(
            "预测基准时刻，格式为 YYYY-MM-DD HH:MM:SS。"
            "两个模型必须使用同一个预测时刻。"
        ),
    )


# ==========================================================
# 2. 基础结果检查
# ==========================================================

def _is_success(result: dict[str, Any]) -> bool:
    return result.get("status") == "success"


def _peak_difference(
    baseline: dict[str, Any],
    final: dict[str, Any],
) -> dict[str, float]:

    baseline_peak = baseline.get(
        "two_hour_peak_demand",
        {},
    )

    final_peak = final.get(
        "two_hour_peak_demand",
        {},
    )

    materials = sorted(
        set(baseline_peak)
        | set(final_peak)
    )

    return {
        material: round(
            float(final_peak.get(material, 0.0))
            - float(baseline_peak.get(material, 0.0)),
            4,
        )
        for material in materials
    }


# ==========================================================
# 3. 单模型需求预测 Tool
# ==========================================================

@tool(
    "demand_forecast",
    args_schema=DemandForecastInput,
)
def demand_forecast(
    forecast_timestamp: str,
    variant: ForecastVariant = "state_conditioned",
) -> dict[str, Any]:
    """
    根据指定预测基准时刻执行未来两小时物料需求预测。

    本工具只负责调用真实预测模型，不由大语言模型自行计算预测值。

    可调用三个 Experiment 3 模型：
    - baseline_sh
    - state_only
    - state_conditioned

    返回 M001-M008 在 t+1、t+2 时刻的需求预测以及未来两小时峰值需求。
    """

    try:
        result = run_state_conditioned_forecast(
            forecast_timestamp=forecast_timestamp,
            variant=variant,
        )

        return {
            "tool": "demand_forecast",
            "status": result.get(
                "status",
                "error",
            ),
            "forecast_timestamp": forecast_timestamp,
            "variant": variant,
            "result": result,
        }

    except Exception as exc:
        return {
            "tool": "demand_forecast",
            "status": "error",
            "forecast_timestamp": forecast_timestamp,
            "variant": variant,
            "message": str(exc),
        }


# ==========================================================
# 4. 同数据源模型比较 Tool
# ==========================================================

@tool(
    "compare_demand_forecasts",
    args_schema=ForecastComparisonInput,
)
def compare_demand_forecasts(
    forecast_timestamp: str,
) -> dict[str, Any]:
    """
    在 Experiment 3 同一数据源、同一预测基准时刻下，
    比较 Baseline Structured Hybrid 与最终
    State-Conditioned Structured Hybrid 模型。

    禁止跨 Experiment 数据直接比较。
    """

    try:

        baseline_result = (
            run_state_conditioned_forecast(
                forecast_timestamp=forecast_timestamp,
                variant="baseline_sh",
            )
        )

        final_result = (
            run_state_conditioned_forecast(
                forecast_timestamp=forecast_timestamp,
                variant="state_conditioned",
            )
        )

        if not _is_success(
            baseline_result
        ):
            return {
                "tool":
                    "compare_demand_forecasts",

                "status":
                    "error",

                "forecast_timestamp":
                    forecast_timestamp,

                "message":
                    "baseline_sh prediction failed",

                "baseline_result":
                    baseline_result,
            }

        if not _is_success(
            final_result
        ):
            return {
                "tool":
                    "compare_demand_forecasts",

                "status":
                    "error",

                "forecast_timestamp":
                    forecast_timestamp,

                "message":
                    "state_conditioned prediction failed",

                "state_conditioned_result":
                    final_result,
            }

        return {
            "tool":
                "compare_demand_forecasts",

            "status":
                "success",

            "forecast_timestamp":
                forecast_timestamp,

            "comparison_basis":
                (
                    "Experiment 3 same data source "
                    "and same forecast timestamp"
                ),

            "baseline_result":
                baseline_result,

            "state_conditioned_result":
                final_result,

            "peak_difference_state_conditioned_minus_baseline":
                _peak_difference(
                    baseline_result,
                    final_result,
                ),
        }

    except Exception as exc:
        return {
            "tool":
                "compare_demand_forecasts",

            "status":
                "error",

            "forecast_timestamp":
                forecast_timestamp,

            "message":
                str(exc),
        }


# ==========================================================
# 5. 对 Agent 暴露的 Tool 集合
# ==========================================================

DEMAND_FORECAST_TOOLS = [
    demand_forecast,
    compare_demand_forecasts,
]