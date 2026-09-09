from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.agents.demand_forecast_agent import DemandForecastAgent
from core.llm import get_llm


router = APIRouter(
    prefix="/api/demand-forecast",
    tags=["Demand Forecast"],
)


class DemandForecastRequest(BaseModel):
    query: str = Field(
        ...,
        min_length=1,
        description="用户的自然语言需求预测请求。",
        examples=[
            "请预测2026年1月30日12点之后未来两小时M001到M008的具体物料需求数量。"
        ],
    )

    thread_id: str | None = Field(
        default=None,
        description=(
            "会话线程 ID。首次请求可不传，"
            "Human-in-the-loop 续接时必须使用同一个 thread_id。"
        ),
    )


class DemandForecastResumeRequest(BaseModel):
    thread_id: str = Field(
        ...,
        min_length=1,
        description="需要恢复的 Human-in-the-loop 线程 ID。",
    )

    answer: str = Field(
        ...,
        min_length=1,
        description="用户对人工澄清问题的补充回答。",
        examples=[
            "2026-01-30 12:00:00"
        ],
    )


class DemandForecastResponse(BaseModel):
    status: str
    thread_id: str
    answer: str | None = None
    reason: str | None = None
    question: str | None = None


_cached_client = None
_cached_agent: DemandForecastAgent | None = None


def get_demand_forecast_agent() -> DemandForecastAgent:
    """
    进程内复用需求预测 Agent。

    LLM 配置热更新后，reset_llm() 会重建全局客户端；
    这里通过客户端对象变化感知并重建 Agent。
    """
    global _cached_client, _cached_agent

    client = get_llm()

    if (
        _cached_agent is None
        or _cached_client is not client
    ):
        _cached_agent = DemandForecastAgent(
            client=client
        )
        _cached_client = client

    return _cached_agent


def _extract_interrupt_payload(
    result: dict[str, Any],
) -> dict[str, Any] | None:
    """
    从 LangGraph 返回结果中提取 interrupt payload。
    """

    interrupts = result.get(
        "__interrupt__"
    )

    if not interrupts:
        return None

    interrupt_item = interrupts[0]

    value = getattr(
        interrupt_item,
        "value",
        None,
    )

    if isinstance(value, dict):
        return value

    return {
        "question": str(value),
    }


def _extract_answer(
    result: dict[str, Any],
) -> str:
    """
    从 Human-in-the-loop 图状态中提取最终回答。
    """

    answer = result.get(
        "answer"
    )

    if answer:
        return str(answer)

    raise ValueError(
        "Agent 未返回最终回答。"
    )


@router.post(
    "/predict",
    response_model=DemandForecastResponse,
    summary="自然语言物料需求预测",
)
def predict_demand(
    request: DemandForecastRequest,
) -> DemandForecastResponse:

    try:

        agent = get_demand_forecast_agent()

        thread_id = (
            request.thread_id
            or str(uuid.uuid4())
        )

        result = agent.invoke(
            query=request.query,
            thread_id=thread_id,
        )

        interrupt_payload = (
            _extract_interrupt_payload(
                result
            )
        )

        if interrupt_payload:

            return DemandForecastResponse(
                status="needs_human_input",
                thread_id=thread_id,
                reason=interrupt_payload.get(
                    "reason"
                ),
                question=interrupt_payload.get(
                    "question"
                ),
            )

        return DemandForecastResponse(
            status="success",
            thread_id=thread_id,
            answer=_extract_answer(
                result
            ),
        )

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc


@router.post(
    "/resume",
    response_model=DemandForecastResponse,
    summary="恢复 Human-in-the-loop 需求预测",
)
def resume_demand_forecast(
    request: DemandForecastResumeRequest,
) -> DemandForecastResponse:

    try:

        agent = get_demand_forecast_agent()

        result = agent.resume(
            thread_id=request.thread_id,
            human_answer=request.answer,
        )

        interrupt_payload = (
            _extract_interrupt_payload(
                result
            )
        )

        if interrupt_payload:

            return DemandForecastResponse(
                status="needs_human_input",
                thread_id=request.thread_id,
                reason=interrupt_payload.get(
                    "reason"
                ),
                question=interrupt_payload.get(
                    "question"
                ),
            )

        return DemandForecastResponse(
            status="success",
            thread_id=request.thread_id,
            answer=_extract_answer(
                result
            ),
        )

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc