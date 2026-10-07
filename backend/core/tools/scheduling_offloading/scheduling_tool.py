from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from core.agents.scheduling_offloading_agent import SchedulingOffloadingAgent


class SchedulingOffloadingInput(BaseModel):
    seed: int = Field(default=41055, ge=0, description="仿真场景随机种子")
    number_of_jobs: int = Field(
        default=20,
        ge=20,
        le=20,
        description="联合 MAPPO 检查点固定使用 20 个仿真工件",
    )


@tool("run_scheduling_offloading", args_schema=SchedulingOffloadingInput)
def run_scheduling_offloading_tool(
    seed: int = 41055,
    number_of_jobs: int = 20,
) -> dict[str, Any]:
    """运行生产调度与计算卸载联合仿真并返回真实计算结果。"""
    try:
        result = SchedulingOffloadingAgent().optimize({
            "engine": "research",
            "algorithm": "midterm",
            "seed": seed,
            "number_of_jobs": number_of_jobs,
            "population_size": 4,
            "generations": 1,
            "decision_horizon": 4,
            "time_horizon": 20.0,
        })
        # 给大模型保留完整指标和摘要，只截取少量动作，避免上下文过长。
        compact = dict(result)
        compact["decisions"] = result.get("decisions", [])[:24]
        compact["decisions_truncated"] = len(result.get("decisions", [])) > 24
        return {"tool": "run_scheduling_offloading", "status": "success", "result": compact}
    except Exception as exc:
        return {
            "tool": "run_scheduling_offloading",
            "status": "error",
            "message": str(exc),
        }


SCHEDULING_OFFLOADING_TOOLS = [run_scheduling_offloading_tool]
