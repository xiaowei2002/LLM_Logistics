from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.agents.scheduling_offloading_agent import SchedulingOffloadingAgent
from core.llm import LLMClient
from core.llm.api_key_store import load_api_key
from core.llm.settings import LLMSettings


router = APIRouter(prefix="/scheduling-offloading", tags=["Scheduling and Offloading"])


class OptimizationRequest(BaseModel):
    seed: int = Field(default=41055, ge=0, le=2_147_483_647)
    number_of_jobs: int = Field(default=20, ge=1)
    machine_count: int = Field(default=6, ge=1, strict=True)
    edge_count: int = Field(default=2, ge=1, strict=True)


class AnalysisRequest(BaseModel):
    run_result: dict[str, Any] = Field(..., description="本次已完成的算法结果，仅用于分析，不重复计算")


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1, description="用户的自然语言调度请求")
    thread_id: str | None = Field(default=None, description="固定业务流程的线程ID")


class ResumeRequest(BaseModel):
    thread_id: str = Field(..., min_length=1)
    answer: str = Field(..., min_length=1)


class ChatResponse(BaseModel):
    status: str
    thread_id: str
    answer: str | None = None
    reason: str | None = None
    question: str | None = None
    run_result: dict[str, Any] | None = None


_agent = SchedulingOffloadingAgent()
_chat_agent: SchedulingOffloadingAgent | None = None
_chat_client: LLMClient | None = None
_chat_saved_key: str | None = None


def get_chat_agent() -> SchedulingOffloadingAgent:
    global _chat_agent, _chat_client, _chat_saved_key
    saved_key = load_api_key()
    if _chat_agent is None or saved_key != _chat_saved_key:
        settings = LLMSettings.from_env()
        if saved_key:
            settings.api_key = saved_key
        _chat_client = LLMClient(settings)
        _chat_agent = SchedulingOffloadingAgent(client=_chat_client)
        _chat_saved_key = saved_key
    return _chat_agent


def _interrupt_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    interrupts = result.get("__interrupt__")
    if not interrupts:
        return None
    value = getattr(interrupts[0], "value", None)
    return value if isinstance(value, dict) else {"question": str(value)}


def _response(result: dict[str, Any], thread_id: str) -> ChatResponse:
    payload = _interrupt_payload(result)
    if payload:
        return ChatResponse(
            status="needs_human_input",
            thread_id=thread_id,
            reason=payload.get("reason"),
            question=payload.get("question"),
        )
    answer = result.get("answer")
    if not answer:
        raise ValueError("智能体未返回最终回答。")
    return ChatResponse(
        status="success",
        thread_id=thread_id,
        answer=str(answer),
        run_result=result.get("run_result"),
    )


@router.get("/capabilities", summary="查看联合调度功能")
def capabilities():
    return {
        "data_source": "simulation",
        "policy": "MAPPO_ROUTING_OFFLOADING+SPT",
        "supported_intents": ["生成方案", "重新运行", "查询结果", "解释指标"],
        "job_count": {"variable": True, "minimum": 1, "default": 20, "training_jobs": 20},
        "machine_count": {"variable": True, "minimum": 1, "default": 6},
        "edge_count": {"variable": True, "minimum": 1, "default": 2},
        "default_scenario": {
            "machine_count": 6,
            "edge_count": 2,
            "machine_topology": "operation_candidates",
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


@router.post("/analyze", summary="解释已生成的调度结果，不重新运行算法")
async def analyze(request: AnalysisRequest):
    result = request.run_result
    if result.get("status") != "success" or not result.get("run_id") or not isinstance(result.get("metrics"), dict) or not result["metrics"]:
        raise HTTPException(status_code=422, detail="请先生成有效调度方案，再分析结果。")
    try:
        answer = await get_chat_agent().analyze_result(result)
        return {"status": "success", "run_id": result["run_id"], "answer": answer}
    except Exception as exc:
        raise HTTPException(status_code=502, detail="文字分析暂时不可用，已生成的调度方案不受影响。") from exc


@router.post("/chat", response_model=ChatResponse, summary="固定流程调度交互")
def chat(request: ChatRequest) -> ChatResponse:
    try:
        thread_id = request.thread_id or str(uuid.uuid4())
        result = get_chat_agent().invoke(request.query, thread_id)
        return _response(result, thread_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/resume", response_model=ChatResponse, summary="恢复调度流程")
def resume(request: ResumeRequest) -> ChatResponse:
    try:
        result = get_chat_agent().resume(request.thread_id, request.answer)
        return _response(result, request.thread_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
