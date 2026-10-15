"""Synchronous, single-process API adapter for the evaluated Olist graph.

Checkpoints live in memory. Restarting the process invalidates run IDs.
Input locations are server configuration, never request parameters.
"""
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from uuid import uuid4
import json
import os

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from core.graph.olist_workflow import build_olist_workflow, frozen_request
from core.tools.olist_weekly_sql import VAL_END


class APIProblem(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class OlistAPISettings:
    source_dir: Path
    weekly_dir: Path
    selection_dir: Path
    output_root: Path

    @classmethod
    def from_env(cls):
        names = ('OLIST_SOURCE_DIR', 'OLIST_WEEKLY_DIR', 'OLIST_SELECTION_DIR', 'OLIST_RUN_ROOT')
        if any(not os.environ.get(name, '').strip() for name in names):
            raise APIProblem(503, '请在服务端配置 OLIST_SOURCE_DIR、OLIST_WEEKLY_DIR、OLIST_SELECTION_DIR、OLIST_RUN_ROOT。')
        return cls(*(Path(os.environ[name]).expanduser().resolve() for name in names))

    def validate(self):
        for path in (self.source_dir, self.weekly_dir, self.selection_dir):
            if not path.is_dir():
                raise APIProblem(503, 'Olist 服务端输入目录不存在。')
            if path == self.output_root or path in self.output_root.parents or self.output_root in path.parents:
                raise APIProblem(503, 'Olist 输出目录必须与输入目录分开。')
        if self.output_root.exists() and not self.output_root.is_dir():
            raise APIProblem(503, 'Olist 输出根目录不是目录。')


class OlistAPIService:
    def __init__(self, settings, *, tool_caller_factory=None, report_generator=None, max_runs=100):
        settings.validate()
        self.settings = settings
        if tool_caller_factory is None:
            from core.agents.olist_tool_caller import OlistToolCaller
            tool_caller_factory = OlistToolCaller
        if report_generator is None:
            from core.graph.olist_structured_report import real_report_plan
            report_generator = real_report_plan
        self.tool_caller_factory = tool_caller_factory
        self.report_generator = report_generator
        self.max_runs = max_runs
        self.runs = {}
        # Serializes graph operations; prevents duplicate/concurrent resume.
        self.lock = Lock()

    def _state(self, category, origin, run_id):
        s = self.settings
        value = dict(source_dir=str(s.source_dir), weekly_dir=str(s.weekly_dir),
                     selection_dir=str(s.selection_dir), run_dir=str(s.output_root / run_id),
                     category_key=category, status='running', execution_trace=[])
        if origin is not None:
            value['origin_week'] = origin
        return value

    def _validate_request(self, value):
        # The frozen protocol is checked before allocating a run or calling LLM.
        # For an absent origin, validate other inputs against the first allowed week.
        try:
            frozen_request({**value, 'origin_week': value.get('origin_week') or VAL_END})
        except Exception:
            raise APIProblem(422, '类别、周一日期或冻结数据/代码校验失败；请检查服务端冻结实验配置。') from None

    def start(self, category, origin=None):
        if not self.lock.acquire(blocking=False):
            raise APIProblem(409, 'Olist 服务正在执行任务，请稍后重试。')
        try:
            run_id = uuid4().hex
            value = self._state(category, origin, run_id)
            self._validate_request(value)
            if len(self.runs) >= self.max_runs:
                raise APIProblem(503, '当前进程的 Olist 任务容量已满，请先归档结果并重启服务。')
            graph = build_olist_workflow(self.report_generator, InMemorySaver(),
                report_mode='structured_plan', tool_caller=self.tool_caller_factory())
            record = dict(graph=graph, config={'configurable': {'thread_id': run_id}}, state=value)
            self.runs[run_id] = record
            return self._execute(run_id, record, value)
        finally:
            self.lock.release()

    def resume(self, run_id, origin):
        if not self.lock.acquire(blocking=False):
            raise APIProblem(409, 'Olist 服务正在执行任务，请稍后重试。')
        try:
            record = self._record(run_id)
            if record.get('public', {}).get('status') != 'awaiting_input':
                raise APIProblem(409, '该任务没有等待补充日期，不能重复恢复。')
            self._validate_request({**record['state'], 'origin_week': origin})
            return self._execute(run_id, record, Command(resume={'origin_week': origin}))
        finally:
            self.lock.release()

    def _record(self, run_id):
        if run_id not in self.runs:
            raise APIProblem(404, '任务不存在，或所属服务进程已重启。')
        return self.runs[run_id]

    def get(self, run_id):
        if not self.lock.acquire(blocking=False):
            raise APIProblem(409, 'Olist 服务正在执行任务，请稍后重试。')
        try:
            return self._record(run_id)['public']
        finally:
            self.lock.release()

    def _execute(self, run_id, record, payload):
        try:
            state = record['graph'].invoke(payload, record['config'])
            record['state'] = state
            paused = bool(state.get('__interrupt__'))
            status = 'awaiting_input' if paused else state.get('status', 'failed')
            public = dict(run_id=run_id, status=status, category_key=state['category_key'],
                origin_week=state.get('origin_week'), execution_trace=state.get('execution_trace', []),
                report=state.get('report_result', ''), forecast=state.get('forecast_result'),
                needs_input=paused, question='请提供预测起点周一日期（YYYY-MM-DD）。' if paused else None,
                error='工作流未完成，请检查服务端任务诊断。' if status in ('failed', 'needs_review') else None)
            # Keep path-bearing diagnostics local; do not expose raw model/provider errors.
            if not paused:
                directory = self.settings.output_root / run_id
                directory.mkdir(parents=True, exist_ok=True)
                (directory / 'api_state.json').write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
                (directory / 'api_result.json').write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception:
            public = dict(run_id=run_id, status='failed', error='工作流执行或结果归档失败。',
                          report='', forecast=None, needs_input=False)
        record['public'] = public
        return public
