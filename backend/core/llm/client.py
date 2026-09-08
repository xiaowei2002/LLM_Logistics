"""
LLM 统一接口封装。
"""

from collections.abc import AsyncIterator, Iterable
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

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


def _to_lc_messages(
    messages: str | Message | Iterable[Message | dict[str, Any]],
) -> list[Any]:
    """统一成 LangChain 消息列表，供 astream / ainvoke / structured 使用。"""
    result: list[Any] = []
    for message in _normalize_messages(messages):
        role, content = message["role"], message["content"]
        if role == "system":
            result.append(SystemMessage(content=content))
        elif role == "assistant":
            result.append(AIMessage(content=content))
        else:
            result.append(HumanMessage(content=content))
    return result


class LLMClient:
    """基于 LangChain init_chat_model 的统一 LLM 客户端。"""

    def __init__(self, settings: LLMSettings | None = None):
        self.settings = settings or LLMSettings.from_env()
        self.model = self._build_model()
        self._openai_client = getattr(self.model, "root_async_client", None)

    def _build_model(self):
        kwargs: dict[str, Any] = {
            "model_provider": self.settings.provider,
            "base_url": self.settings.base_url,
            "api_key": self.settings.api_key,
            "request_timeout": self.settings.timeout,
            "max_retries": self.settings.max_retries,
        }
        if self.settings.temperature is not None:
            kwargs["temperature"] = self.settings.temperature
        if self.settings.max_tokens is not None:
            kwargs["max_tokens"] = self.settings.max_tokens
        if self.settings.top_p is not None:
            kwargs["top_p"] = self.settings.top_p
        return init_chat_model(self.settings.model, **kwargs)

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
        if self._openai_client is not None:
            response = await self._openai_client.chat.completions.create(
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
        message = await self.model.ainvoke(_to_lc_messages(messages))
        return LLMResponse(content=message.content)

    async def stream(
        self,
        messages: str | Message | Iterable[Message | dict[str, Any]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        extra_body: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        """流式请求，逐chunk 产出增量"""
        if self._openai_client is None:
            async for chunk in self.model.astream(_to_lc_messages(messages)):
                yield StreamChunk(content=chunk.content)
            return

        create_kwargs: dict[str, Any] = {
            "model": self.settings.model,
            "messages": _normalize_messages(messages),
            "stream": True,
            **self._sampling(temperature, max_tokens, top_p),
            **kwargs,
        }
        if extra_body is not None:
            create_kwargs["extra_body"] = extra_body

        response = await self._openai_client.chat.completions.create(**create_kwargs)
        async for chunk in response:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            yield StreamChunk(
                content=delta.content,
                reasoning_content=getattr(delta, "reasoning_content", None),
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
        """流式请求，仅产出正文文本增量。"""
        async for chunk in self.stream(messages, **kwargs):
            if chunk.content:
                yield chunk.content

    async def structured(
        self,
        messages: str | Message | Iterable[Message | dict[str, Any]],
        schema: dict[str, Any] | type,
        **kwargs: Any,
    ) -> Any:
        """结构化输出：返回符合给定 schema 的对象（Pydantic 模型或 dict）。"""
        runnable = self.model.with_structured_output(schema)
        return await runnable.ainvoke(_to_lc_messages(messages), **kwargs)


_llm: LLMClient | None = None


def get_llm(settings: LLMSettings | None = None) -> LLMClient:
    """获取 LLM 客户端"""
    global _llm
    if settings is not None:
        return LLMClient(settings)
    if _llm is None:
        _llm = LLMClient()
    return _llm


def reset_llm(settings: LLMSettings | None = None) -> None:
    """丢弃缓存的客户端；配置更新后调用，下次 get_llm() 按新配置重建。"""
    global _llm
    _llm = LLMClient(settings) if settings is not None else None


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
