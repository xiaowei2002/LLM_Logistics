from __future__ import annotations

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
            "请预测2026年1月30日12点之后未来两小时M001到M008的具体物料需求数量，我需要尽可能准确的预测结果。"
        ],
    )


class DemandForecastResponse(BaseModel):
    status: str
    answer: str


_cached_client = None
_cached_agent: DemandForecastAgent | None = None


def get_demand_forecast_agent() -> DemandForecastAgent:
    """
    进程内复用需求预测 Agent，避免每次请求重复创建。

    LLM 配置热更新后，reset_llm() 会重建全局客户端，这里通过
    客户端对象身份变化感知并重建 Agent，保证预测与聊天配置一致。
    """
    global _cached_client, _cached_agent
    client = get_llm()
    if _cached_agent is None or _cached_client is not client:
        _cached_agent = DemandForecastAgent(client=client)
        _cached_client = client
    return _cached_agent


def _extract_final_answer(result: dict) -> str:
    """
    从 LangChain Agent 返回的 messages 中提取最终 AI 回复。
    """

    messages = result.get("messages", [])

    if not messages:
        raise ValueError("Agent 未返回任何消息。")

    last_message = messages[-1]
    content = getattr(last_message, "content", None)

    if isinstance(content, str):
        return content

    if content is None:
        return str(last_message)

    return str(content)


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

        result = agent.invoke(
            request.query
        )

        answer = _extract_final_answer(
            result
        )

        return DemandForecastResponse(
            status="success",
            answer=answer,
        )

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc