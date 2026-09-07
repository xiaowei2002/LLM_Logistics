"""
LLM 配置。
"""
import os
from dataclasses import dataclass
from typing import Any
from dotenv import load_dotenv

# 读取环境变量
load_dotenv(override=True)

def _get_str(key: str, default: str | None) -> str | None:
    value = os.getenv(key)
    return value if value not in (None, "") else default


def _get_float(key: str, default: float | None) -> float | None:
    value = _get_str(key, None)
    return float(value) if value is not None else default


def _get_int(key: str, default: int | None) -> int | None:
    value = _get_str(key, None)
    return int(value) if value is not None else default


@dataclass
class LLMSettings:
    """LLM 调用配置项"""

    provider: str = "openai"
    model: str = "Qwen2.5-VL-7B-Instruct"
    base_url: str = "http://127.0.0.1:8000/v1"
    api_key: str = "EMPTY"
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    timeout: float = 300.0
    max_retries: int = 2

    @classmethod
    def from_env(cls) -> "LLMSettings":
        """从环境变量构建配置。"""
        return cls(
            provider=_get_str("LLM_PROVIDER", cls.provider) or cls.provider,
            model=_get_str("LLM_MODEL", cls.model),
            base_url=_get_str("LLM_BASE_URL", cls.base_url),
            api_key=_get_str("LLM_API_KEY", cls.api_key) or "EMPTY",
            temperature=_get_float("LLM_TEMPERATURE", cls.temperature),
            max_tokens=_get_int("LLM_MAX_TOKENS", cls.max_tokens),
            top_p=_get_float("LLM_TOP_P", cls.top_p),
            timeout=_get_float("LLM_TIMEOUT", cls.timeout) or 300.0,
            max_retries=_get_int("LLM_MAX_RETRIES", cls.max_retries) or 2,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            k: v
            for k, v in {
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "top_p": self.top_p,
            }.items()
            if v is not None
        }
