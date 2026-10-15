from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field


class SchedulingOffloadingInput(BaseModel):
    machine_count: int = Field(default=6, ge=1, strict=True, description="车间机器总数")
    edge_count: int = Field(default=2, ge=1, strict=True, description="边缘计算节点数量")
    seed: int = Field(default=41055, ge=0, description="仿真场景随机种子")
    number_of_jobs: int = Field(
        default=20,
        ge=1,
        description="仿真工件总数，必须为正整数；默认20，其他数量为旧模型的跨规模推理",
    )


@tool("run_scheduling_offloading", args_schema=SchedulingOffloadingInput)
def run_scheduling_offloading_tool(
    seed: int = 41055,
    number_of_jobs: int = 20,
    machine_count: int = 6,
    edge_count: int = 2,
) -> dict[str, Any]:
    """运行生产调度与计算卸载联合仿真并返回真实计算结果。"""
    try:
        # 放到函数内部导入，避免智能体注册工具时形成循环导入。
        from core.agents.scheduling_offloading_agent import SchedulingOffloadingAgent

        result = SchedulingOffloadingAgent().optimize({
            "seed": seed,
            "number_of_jobs": number_of_jobs,
            "machine_count": machine_count,
            "edge_count": edge_count,
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
