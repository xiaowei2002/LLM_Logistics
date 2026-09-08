"""LLM 配置管理接口：查看 / 修改运行时配置，改动持久化且即时生效。

优先级：backend/.env（环境变量）为底，叠加 backend/data/llm_settings.json
（网页端修改保存的覆盖值）。
"""

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, field_validator

from core.llm import reset_llm
from core.llm.settings import LLMSettings

router = APIRouter(prefix="/settings", tags=["settings"])

# 网页端修改的覆盖值保存位置（backend/data/llm_settings.json）
OVERRIDES_FILE = Path(__file__).resolve().parents[2] / "data" / "llm_settings.json"


def _load_overrides() -> dict[str, Any]:
    if OVERRIDES_FILE.exists():
        try:
            return json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def effective_settings() -> LLMSettings:
    """环境变量为底，叠加运行时覆盖值。"""
    settings = LLMSettings.from_env()
    for key, value in _load_overrides().items():
        if hasattr(settings, key) and value is not None:
            setattr(settings, key, value)
    return settings


def _public_view(s: LLMSettings) -> dict[str, Any]:
    return {
        "provider": s.provider,
        "model": s.model,
        "base_url": s.base_url,
        "api_key": "" if s.api_key == "EMPTY" else (s.api_key or ""),
        "temperature": s.temperature,
        "max_tokens": s.max_tokens,
        "top_p": s.top_p,
    }


class SettingsUpdate(BaseModel):
    model: str | None = None
    base_url: str | None = None
    # 留空表示不修改（清空 Key 场景：后端保留原值）
    api_key: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None

    @field_validator("model")
    @classmethod
    def _model_not_empty(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("模型名称不能为空")
        return v.strip() if v else v

    @field_validator("base_url")
    @classmethod
    def _base_url_ok(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith(("http://", "https://")):
            raise ValueError("API 地址必须以 http:// 或 https:// 开头")
        return v.rstrip("/")

    @field_validator("temperature")
    @classmethod
    def _temp_range(cls, v: float | None) -> float | None:
        if v is not None and not 0 <= v <= 2:
            raise ValueError("温度取值范围为 0 ~ 2")
        return v

    @field_validator("top_p")
    @classmethod
    def _top_p_range(cls, v: float | None) -> float | None:
        if v is not None and not 0 < v <= 1:
            raise ValueError("top_p 取值范围为 (0, 1]")
        return v

    @field_validator("max_tokens")
    @classmethod
    def _tokens_positive(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError("max_tokens 必须为正整数")
        return v


@router.get("/llm")
async def get_llm_settings() -> dict[str, Any]:
    return _public_view(effective_settings())


@router.put("/llm")
async def update_llm_settings(update: SettingsUpdate) -> dict[str, Any]:
    provided = update.model_fields_set  # 区分"没传该字段"和"传 null（恢复默认）"
    overrides = _load_overrides()

    if (model := update.model) is not None:
        overrides["model"] = model
    if (base_url := update.base_url) is not None:
        overrides["base_url"] = base_url
    if update.api_key:  # 空值 = 不修改
        overrides["api_key"] = update.api_key.strip()
    # 采样参数：传 null 恢复模型默认（从覆盖文件里删掉），传数值则保存
    for field in ("temperature", "max_tokens", "top_p"):
        if field in provided:
            value = getattr(update, field)
            if value is None:
                overrides.pop(field, None)
            else:
                overrides[field] = value

    OVERRIDES_FILE.parent.mkdir(parents=True, exist_ok=True)
    OVERRIDES_FILE.write_text(
        json.dumps(overrides, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 立即用新配置重建客户端，无需重启后端
    reset_llm(effective_settings())
    return _public_view(effective_settings())
