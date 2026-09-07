"""LLM 消息与响应数据模型。"""
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class Message:
    """对话消息"""

    role: Role
    content: str | list[dict[str, Any]]

    @classmethod
    def system(cls, content: str) -> "Message":
        return cls(role="system", content=content)

    @classmethod
    def user(cls, content: str | list[dict[str, Any]]) -> "Message":
        return cls(role="user", content=content)

    @classmethod
    def assistant(cls, content: str) -> "Message":
        return cls(role="assistant", content=content)

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.role, "content": self.content}


@dataclass
class StreamChunk:
    """流式输出的单个增量"""

    content: str | None = None
    reasoning_content: str | None = None
    finish_reason: str | None = None
    tool_calls: list[Any] = field(default_factory=list)
    usage: dict[str, int] | None = None
    raw: Any = None  # 原始 openai chunk，便于高级场景


@dataclass
class LLMResponse:
    """非流式请求的完整响应"""

    content: str
    finish_reason: str | None = None
    usage: dict[str, int] | None = None
