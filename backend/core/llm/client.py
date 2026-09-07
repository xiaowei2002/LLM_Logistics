"""
LLM 统一接口封装。
"""
from collections.abc import AsyncIterator, Iterable
from typing import Any

from openai import AsyncOpenAI

from core.llm.settings import LLMSettings
from core.llm.types import LLMResponse, Message, StreamChunk


def _normalize_messages(
    messages: str | Message | Iterable[Message | dict[str, Any]],
) -> list[dict[str, Any]]:
    """统一成 OpenAI 消息列表。"""
    if isinstance(messages, str):
        return [{"role": "user", "content": messages}]

    normalized: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, Message):
            normalized.append(message.to_dict())
        else:
            normalized.append(message)
    return normalized


class LLMClient:
    """基于OpenAI兼容接口的统一LLM客户端。"""

    def __init__(self, settings: LLMSettings | None = None):
        self.settings = settings or LLMSettings.from_env()
        self._client = AsyncOpenAI(
            api_key=self.settings.api_key,
            base_url=self.settings.base_url,
            timeout=self.settings.timeout,
            max_retries=self.settings.max_retries,
        )

    def _sampling(self, temperature, max_tokens, top_p) -> dict[str, Any]:
        params: dict[str, Any] = {}
        for key, value in (
            ("temperature", temperature if temperature is not None else self.settings.temperature),
            ("max_tokens", max_tokens if max_tokens is not None else self.settings.max_tokens),
            ("top_p", top_p if top_p is not None else self.settings.top_p),
        ):
            if value is not None:
                params[key] = value
        return params

    async def chat(
        self,
        messages: str | Message | Iterable[Message | dict[str, Any]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """非流式请求，返回完整响应。"""
        response = await self._client.chat.completions.create(
            model=self.settings.model,
            messages=_normalize_messages(messages),
            stream=False,
            **self._sampling(temperature, max_tokens, top_p),
            **kwargs,
        )
        choice = response.choices[0]
        return LLMResponse(
            content=choice.message.content or "",
            finish_reason=choice.finish_reason,
            usage=response.usage.model_dump() if response.usage else None,
        )

    async def stream(
        self,
        messages: str | Message | Iterable[Message | dict[str, Any]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        """流式请求，逐 chunk产出结构化增量"""
        response = await self._client.chat.completions.create(
            model=self.settings.model,
            messages=_normalize_messages(messages),
            stream=True,
            **self._sampling(temperature, max_tokens, top_p),
            **kwargs,
        )
        async for chunk in response:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            yield StreamChunk(
                content=delta.content,
                finish_reason=choice.finish_reason,
                tool_calls=delta.tool_calls or [],
                usage=chunk.usage.model_dump() if chunk.usage else None,
                raw=chunk,
            )

    async def stream_chat(
        self,
        messages: str | Message | Iterable[Message | dict[str, Any]],
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """流式请求"""
        async for chunk in self.stream(messages, **kwargs):
            if chunk.content:
                yield chunk.content


_llm: LLMClient | None = None


def get_llm(settings: LLMSettings | None = None) -> LLMClient:
    """获取LLM客户端（"""
    global _llm
    if settings is not None:
        return LLMClient(settings)
    if _llm is None:
        _llm = LLMClient()
    return _llm


async def ask_llm(
    messages: str | Message | Iterable[Message | dict[str, Any]],
    *,
    stream: bool = True,
    only_content: bool = True,
    client: LLMClient | None = None,
    **kwargs: Any,
) -> AsyncIterator[str | StreamChunk | LLMResponse]:
    """统一 LLM 调用入口，默认流式输出文本增量。

    用法::
        async for text in ask_llm("你好"):
            print(text, end="")
    """
    client = client or get_llm()
    if stream:
        if only_content:
            async for text in client.stream_chat(messages, **kwargs):
                yield text
        else:
            async for chunk in client.stream(messages, **kwargs):
                yield chunk
    else:
        response = await client.chat(messages, **kwargs)
        yield response.content if only_content else response
