from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.agents.scheduling_offloading_agent import SchedulingOffloadingAgent
from core.agents.scheduling_offloading_chat_agent import SchedulingOffloadingChatAgent
from core.llm import LLMClient
from core.llm.api_key_store import load_api_key
from core.llm.settings import LLMSettings
from core.tools.scheduling_offloading.engine import ALGORITHM_OPTIONS, ENGINE_OPTIONS


router = APIRouter(
    prefix="/scheduling-offloading",
    tags=["Scheduling and Offloading"],
)


class OptimizationRequest(BaseModel):
    engine: str = Field(default="research", pattern="^(auto|research|demo)$")
    algorithm: str = Field(
        default="midterm", pattern="^(midterm|rolling_ga|rolling_nsga2)$"
    )
    seed: int = Field(default=41055, ge=0, le=2_147_483_647)
    number_of_jobs: int = Field(default=20, ge=1, le=100)
    population_size: int = Field(default=4, ge=1, le=64)
    generations: int = Field(default=1, ge=0, le=50)
    decision_horizon: int = Field(default=4, ge=1, le=20)
    time_horizon: float = Field(default=20.0, gt=0, le=300)


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1, description="用户的自然语言调度请求")


class ChatResponse(BaseModel):
    status: str
    answer: str
    run_result: dict[str, Any] | None = None


_agent = SchedulingOffloadingAgent()
_chat_agent: SchedulingOffloadingChatAgent | None = None
_chat_client: LLMClient | None = None
_chat_saved_key: str | None = None


def get_chat_agent() -> SchedulingOffloadingChatAgent:
    global _chat_agent, _chat_client, _chat_saved_key
    saved_key = load_api_key()
    if _chat_agent is None or saved_key != _chat_saved_key:
        settings = LLMSettings.from_env()
        if saved_key:
            settings.api_key = saved_key
        _chat_client = LLMClient(settings)
        _chat_agent = SchedulingOffloadingChatAgent(client=_chat_client)
        _chat_saved_key = saved_key
    return _chat_agent


@router.get("/capabilities", summary="查看联合调度功能")
def capabilities():
    return {
        "data_source": "simulation",
        "engines": ENGINE_OPTIONS,
        "algorithms": ALGORITHM_OPTIONS,
        "fixed_scenario": {
            "number_of_work_centers": 3,
            "machines_per_work_center": 2,
            "machine_flexibility": "partial3",
            "compute_nodes": ["LOCAL", "EDGE1", "EDGE2", "CLOUD"],
        },
    }


@router.post("/optimize", summary="运行生产调度与计算卸载仿真")
def optimize(request: OptimizationRequest):
    try:
        return _agent.optimize(request.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/chat", response_model=ChatResponse, summary="自然语言联合调度交互")
def chat(request: ChatRequest) -> ChatResponse:
    try:
        result = get_chat_agent().invoke(request.query)
        return ChatResponse(status="success", **result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
