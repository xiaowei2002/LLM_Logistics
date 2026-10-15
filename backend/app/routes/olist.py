"""Olist workflow HTTP interface; inputs are configured by the server."""
from functools import lru_cache
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, StrictStr, field_validator

from core.graph.olist_api_service import APIProblem, OlistAPIService, OlistAPISettings

router = APIRouter(prefix='/olist', tags=['olist'])


class StartRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    category_key: StrictStr
    origin_week: StrictStr | None = None

    @field_validator('category_key')
    @classmethod
    def category(cls, value):
        if not value or len(value) > 100:
            raise ValueError('类别不能为空且最多 100 个字符。')
        return value

    @field_validator('origin_week')
    @classmethod
    def monday(cls, value):
        if value is not None:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value or parsed.weekday() != 0:
                raise ValueError('请输入 YYYY-MM-DD 格式的周一日期。')
        return value


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    origin_week: StrictStr

    _monday = field_validator('origin_week')(StartRequest.monday.__func__)


@lru_cache(maxsize=1)
def get_olist_service():
    try:
        return OlistAPIService(OlistAPISettings.from_env())
    except APIProblem as exc:
        raise HTTPException(exc.status_code, exc.detail) from None


def _call(function, *args):
    try:
        return function(*args)
    except APIProblem as exc:
        raise HTTPException(exc.status_code, exc.detail) from None


@router.post('/runs')
def start(request: StartRequest, service=Depends(get_olist_service)):
    return _call(service.start, request.category_key, request.origin_week)


@router.get('/runs/{run_id}')
def result(run_id: str, service=Depends(get_olist_service)):
    return _call(service.get, run_id)


@router.post('/runs/{run_id}/resume')
def resume(run_id: str, request: ResumeRequest, service=Depends(get_olist_service)):
    return _call(service.resume, run_id, request.origin_week)
