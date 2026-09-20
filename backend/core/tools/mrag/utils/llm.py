"""
==========================================================================
共享的大模型（qwen / DashScope OpenAI 兼容）文本聊天助手

把 loader.py 里 QwenClient 的「文本对话」调用方式抽出来，避免 query / generation
等模块各自重复写一套。只做 chat.completions（文本），视觉描述仍走 loader 的 QwenClient。

用法：
    from core.tools.mrag.utils.llm import chat, chat_stream
    answer = chat([{"role": "system", "content": "..."},
                   {"role": "user", "content": "..."}])
==========================================================================
"""
from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional, Union

from core.tools.mrag.utils.utils import config

Message = Dict[str, str]

_client = None  # 懒加载，避免无 openai 时 import 即报错


def _get_client():
    global _client
    if _client is None:
        if not config.openai_api_key:
            raise RuntimeError(
                "未配置 OPENAI_API_KEY，无法调用大模型（qwen）。请在 backend/.env 里填写 DashScope Key。"
            )
        from openai import OpenAI

        _client = OpenAI(api_key=config.openai_api_key, base_url=config.openai_base_url)
    return _client


def chat(
    messages: Union[str, List[Message]],
    model: Optional[str] = None,
    temperature: float = 0.0,
    **kwargs: Any,
) -> str:
    """一次文本对话（非流式），返回模型回复文本。缺省 temperature=0（改写/抽取等确定性任务）。"""
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]
    resp = _get_client().chat.completions.create(
        model=model or config.llm_model,
        messages=messages,
        temperature=temperature,
        **kwargs,
    )
    if not resp.choices:
        return ""
    return resp.choices[0].message.content or ""


def chat_stream(
    messages: Union[str, List[Message]],
    model: Optional[str] = None,
    temperature: float = 0.0,
    **kwargs: Any,
) -> Iterator[str]:
    """流式文本对话，逐 token 产出。"""
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]
    resp = _get_client().chat.completions.create(
        model=model or config.llm_model,
        messages=messages,
        temperature=temperature,
        stream=True,
        **kwargs,
    )
    for chunk in resp:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            yield delta
