"""Report V3.2 new controlled cases, using the frozen V3.1 prompt.
Run from backend: python -m tests.system_eval.eval_report_fidelity_v32
Requires existing ReportAgent, V3 Node and V2 evaluation helpers.
Saves states, exact node instructions, reports and file fingerprints.
Automatic token rules and review flags still require full-text review.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

os.environ['PYTHON_DOTENV_DISABLED'] = '1'

from langchain.agents import create_agent
from core.agents.report_agent import ReportAgent
from core.graph.nodes.report_node_v3 import build_report_node
from core.utils.yaml_loader import load_yaml
from tests.system_eval.eval_report_fidelity import evaluate_case

ROOT = Path(__file__).resolve().parents[2]
CASE_PATH = ROOT / 'tests/system_eval/cases/report_fidelity_v32_cases.json'
PROMPT_PATH = ROOT / 'core/prompts/report_agent_v31.yaml'
RESULT_ROOT = ROOT / 'tests/system_eval/results'


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_json(path: Path, obj: Any) -> None:
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2,
                               default=str) + '\n', encoding='utf-8')
    temp.replace(path)


class CapturedReportAgent:
    """Use the real ReportAgent with an independent V3 system prompt."""
    def __init__(self, prompt: str) -> None:
        self.base = ReportAgent()
        self.base.agent = create_agent(
            model=self.base.client.model, tools=[], system_prompt=prompt,
        )
        self.instructions: list[str] = []

    def generate(self, instruction: str) -> str:
        self.instructions.append(instruction)
        return self.base.generate(instruction)


def boundary_review_flags(report: str, state: dict) -> list[str]:
    """Screen suspicious claims for review, not semantic verification.

    Flags can be false positives, especially if a report quotes a prohibited
    claim in order to reject it. They are kept separate from V2 pass/fail.
    """
    source = json.dumps(state, ensure_ascii=False, default=str)
    flags = []
    if ('quantity' in source and '库存' not in source
            and re.search(r'库存数量|可用库存|在途库存', report)):
        flags.append('quantity 的库存语义未在 state 中提供，请核对。')
    for number in sorted(set(re.findall(r'(?<![\d.])(\d+(?:\.\d+)?)\s*[%％]', report))):
        if not re.search(r'(?<![\d.])' + re.escape(number) + r'(?![\d.])', source):
            flags.append(f'报告百分比 {number}% 未在 state 中找到对应数值，请核对。')
    patterns = [
        (r'未重新(执行|运行).{0,12}(CleaningAgent|清洗)|复用.{0,12}(落盘|清洗文件)|再次通过.{0,12}(验证|Gate)',
         '报告描述了清洗重跑/复用/再次校验过程，请核对实际输入。'),
        (r'(整个|全部|所有).{0,12}(链路|模块|任务).{0,12}(中断|终止|未启动|未执行)|不存在人工确认|没有发生人工确认',
         '报告对工作流或人工确认作了整体断言，请核对实际输入。'),
        (r'无缺失值|没有缺失值|无重复记录|没有重复记录|未检出.{0,8}问题',
         '报告声称未发现质量问题，请核对是否提供了对应检查项。'),
        (r'可直接.{0,8}(预测|训练|生产)|不可用于任何',
         '报告扩大了下游可用性结论，请核对。'),
    ]
    for pattern, issue in patterns:
        if re.search(pattern, report):
            flags.append(issue)
    return flags


def main() -> None:
    config = json.loads(CASE_PATH.read_text(encoding='utf-8-sig'))
    cases = config['cases']
    if len(cases) != 10 or len({c['case_id'] for c in cases}) != 10:
        raise ValueError('Expected ten unique new cases.')
    if not os.getenv('LLM_API_KEY') or not os.getenv('LLM_MODEL'):
        raise RuntimeError('LLM_API_KEY and LLM_MODEL are required in the terminal environment.')
    expected_frozen = {
        PROMPT_PATH: 'da29d4c1a48c3bfa175a14dd7204f2f3571028d429752da2521f6fb9e096ad8b',
        ROOT / 'core/graph/nodes/report_node_v3.py':
            '1fc33b531fddd9ec3635b7e8a938f1e9052489b0013c00affb9116790a859715',
    }
    for path, expected in expected_frozen.items():
        if digest(path) != expected:
            raise RuntimeError(f'Frozen file differs from V3.1 run: {path}. '
                               'Check contents/encoding/line endings; do not edit the expected hash.')
    baseline_path = CASE_PATH.with_name('report_fidelity_cases.json')
    baseline = json.loads(baseline_path.read_text(encoding='utf-8-sig'))['cases']
    if {c['case_id'] for c in cases} & {c['case_id'] for c in baseline}:
        raise ValueError('New case IDs overlap the repair cases.')
    prompt = load_yaml(PROMPT_PATH)['system_prompt']
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError('Empty V3 system prompt.')
    paths = [CASE_PATH, PROMPT_PATH, Path(__file__),
             ROOT / 'core/graph/nodes/report_node_v3.py',
             ROOT / 'core/agents/report_agent.py',
             ROOT / 'tests/system_eval/eval_report_fidelity.py']
    hashes = {str(p.relative_to(ROOT)): digest(p) for p in paths}
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    result_dir = RESULT_ROOT / f'report_fidelity_v32_{stamp}'
    result_dir.mkdir(parents=True, exist_ok=False)
    save_json(result_dir / 'input_cases_snapshot.json', config)
    save_json(result_dir / 'manifest.json', dict(
        evaluated_at=datetime.now().isoformat(), file_sha256=hashes,
        llm_model=os.getenv('LLM_MODEL'), llm_provider=os.getenv('LLM_PROVIDER'),
        prompt=PROMPT_PATH.name, case_path=str(CASE_PATH),
        real_llm_used=True, controlled_upstream_inputs=True,
        changed_components=['evaluation_cases'],
        frozen_v31_prompt_and_v3_node_hash_verified=True,
    ))
    records = []
    print('=== Report Fidelity V3.2: NEW CONTROLLED CASES ===', flush=True)
    print('Cases:', len(cases), '| LLM:', os.getenv('LLM_MODEL'), flush=True)
    print('Upstream: controlled inputs; full-text review required.', flush=True)
    for case in cases:
        # Verify source files have not changed before each invocation.
        for name, expected in hashes.items():
            if digest(ROOT / name) != expected:
                raise RuntimeError(f'File changed during evaluation: {name}')
        print(f"\nRunning {case['case_id']}: {case['name']}", flush=True)
        started_agent_error = None
        # A new real agent per case gives independent conversation state.
        try:
            agent = CapturedReportAgent(prompt)
            row, report = evaluate_case(deepcopy(case), build_report_node(agent))
            calls = len(agent.instructions)
            instruction = '\n\n'.join(agent.instructions)
        except Exception as exc:
            started_agent_error = f'{type(exc).__name__}: {exc}'
            row = dict(case_id=case['case_id'], name=case['name'],
                expected_status=case['expected_report_status'], actual_status='exception',
                status_match=False, fidelity_rules_passed=False, automatic_passed=False,
                manual_review_status='not_evaluable', elapsed_seconds=0.0,
                missing_required='[]', missing_any_groups='[]', found_forbidden='[]',
                error=started_agent_error)
            report, calls, instruction = '', 0, ''
        # V2 inferred no model call from the result alone; V3 measures it.
        if case['expected_report_status'] == 'error':
            row['automatic_passed'] = row['automatic_passed'] and calls == 0
            if calls:
                row['error'] = 'Expected no-evidence rejection, but generate() was called.'
        flags = boundary_review_flags(report, case['state'])
        row['llm_generate_calls'] = calls
        row['boundary_review_flags'] = json.dumps(flags, ensure_ascii=False)
        row['review_flag_count'] = len(flags)
        row['manual_review_status'] = ('pending' if report.strip() else
                                       'not_applicable' if row['automatic_passed'] else
                                       'not_evaluable')
        records.append(row)
        case_id = case['case_id']
        (result_dir / f'{case_id}_report.txt').write_text(report, encoding='utf-8')
        (result_dir / f'{case_id}_instruction.txt').write_text(instruction, encoding='utf-8')
        save_json(result_dir / 'case_results.json', records)
        print('Automatic rules:', 'PASS' if row['automatic_passed'] else 'FAIL',
              '| generate calls:', calls, '| review flags:', len(flags), flush=True)
        for flag in flags:
            print('REVIEW:', flag, flush=True)
        if row['error']:
            print('Error:', row['error'], flush=True)
    for name, expected in hashes.items():
        if digest(ROOT / name) != expected:
            raise RuntimeError(f'File changed during evaluation: {name}')
    with (result_dir / 'report_fidelity_results.csv').open(
            'w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]))
        writer.writeheader(); writer.writerows(records)
    passed = sum(r['automatic_passed'] is True for r in records)
    summary = dict(evaluation_id='report_fidelity_v32', total_cases=len(cases),
        completed_cases=sum(r['actual_status'] in ('success', 'error') for r in records),
        exception_cases=sum(r['actual_status'] == 'exception' for r in records),
        automatically_passed_cases=passed, automatic_pass_rate=passed / len(cases),
        generated_reports=sum(r['actual_status'] == 'success' for r in records),
        pending_manual_reviews=sum(r['manual_review_status'] == 'pending' for r in records),
        reports_with_review_flags=sum(r['review_flag_count'] > 0 for r in records),
        real_llm_used=True, real_report_agent_used=True, real_report_node_used=True,
        real_sql_used=False, real_cleaning_used=False, real_forecasting_used=False,
        verified_full_text_factual_accuracy=None, verified_hallucination_rate=None,
        file_sha256=hashes,
        note='Original V2 token rules are reused. Review flags are heuristic and '
             'do not change the automatic pass rate. Compare complete reports '
             'against saved states and instructions. This is controlled regression, '
             'new-case evaluation with a frozen prompt and node, not real upstream integration.')
    save_json(result_dir / 'report_fidelity_summary.json', summary)
    print('\n=== SUMMARY ===\n' + json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print('Results:', result_dir, flush=True)


if __name__ == '__main__':
    main()
