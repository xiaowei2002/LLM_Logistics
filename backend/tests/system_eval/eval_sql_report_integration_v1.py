"""Integration: real SQLAgent -> real SQL Node -> existing graph -> Report V3.

Four positive chains use real GLM, guarded MCP and read-only Olist MySQL.
Two separate fault probes use a controlled SQL error: graph routing (no LLM)
and direct real Report Node error rendering (real LLM, no MySQL failure).
Run from backend: python -m tests.system_eval.eval_sql_report_integration_v1
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import os
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

os.environ['PYTHON_DOTENV_DISABLED'] = '1'

from core.agents.sql_agent import SQLAgent
from core.graph.nodes.sql_node import build_sql_node
from core.graph.workflow import build_logistics_workflow
from core.graph.nodes.report_node_v3 import build_report_node
from core.tools.sql_readonly_guard import validate_readonly_sql
from core.utils.yaml_loader import load_yaml
from tests.system_eval.eval_sql_v33_real import (
    audit_answer, build_gold_tool, require_safe_environment, run_gold_query,
)
from tests.system_eval.eval_sql_v3_real import (
    extract_execute_sql_call, extract_execute_result, parse_mcp_text,
)
from tests.system_eval.eval_report_fidelity_v3 import (
    CapturedReportAgent, boundary_review_flags, save_json,
)

ROOT = Path(__file__).resolve().parents[2]
CASE_PATH = ROOT / 'tests/system_eval/cases/sql_report_integration_v1_cases.json'
SQL_PROMPT = ROOT / 'core/prompts/sql_agent_olist_v33.yaml'
REPORT_PROMPT = ROOT / 'core/prompts/report_agent_v31.yaml'


class RecordedSQLAgent(SQLAgent):
    def __init__(self, prompt: str):
        super().__init__()
        self.system_prompt = prompt
        self.last_raw_state: dict[str, Any] | None = None
        self.last_evidence: dict[str, Any] | None = None

    async def _invoke_agent(self, question: str):
        state = await super()._invoke_agent(question)
        self.last_raw_state = state
        return state

    async def query_with_evidence(self, question: str):
        result = await super().query_with_evidence(question)
        self.last_evidence = result
        return result


class ControlledFailureAgent:
    calls = 0
    async def query_with_evidence(self, question: str):
        self.calls += 1
        return dict(status='error', answer='', query_verified=False,
                    tool_name=None, tool_call_id=None,
                    error='受控测试异常：未获得查询结果')


class NeverReport:
    calls = 0
    def generate(self, instruction: str):
        self.calls += 1
        raise AssertionError('Failed SQL must not call Report in the existing workflow.')


def unused_node(state):
    raise AssertionError('Unexpected non-SQL branch executed.')


def make_workflow(sql_node, report_node):
    return build_logistics_workflow(sql_node=sql_node, report_node=report_node,
                                    cleaning_node=unused_node, forecast_node=unused_node)


def input_state(question: str):
    return dict(user_query=question, task_mode='sql_report', status='running',
                execution_trace=[])


async def routing_probe() -> dict[str, Any]:
    """Uses the existing graph, node and router, with controlled error only."""
    fake = ControlledFailureAgent()
    report = NeverReport()
    final = await make_workflow(build_sql_node(fake), build_report_node(report)).ainvoke(
        input_state('受控失败路由验证'))
    passed = (fake.calls == 1 and report.calls == 0
              and final.get('sql_status') == 'error'
              and final.get('sql_result') == ''
              and final.get('status') == 'failed'
              and final.get('execution_trace') == ['sql'])
    return dict(id='IR405', evaluation_type='controlled_sql_failure_graph_routing',
                real_llm_used=False, real_mysql_used=False,
                sql_calls=fake.calls, report_calls=report.calls,
                final_state=final, automatic_passed=passed)


def preflight():
    cases = json.loads(CASE_PATH.read_text(encoding='utf-8-sig'))
    if len(cases) != 4 or len({c['id'] for c in cases}) != 4:
        raise ValueError('Expected four unique real integration cases.')
    for case in cases:
        validate_readonly_sql(case['gold_sql'])
    frozen = {
        SQL_PROMPT: 'c78da43aff7692aedc1f494f569c144d4f14b03063865786d097273d72d5568d',
        REPORT_PROMPT: 'da29d4c1a48c3bfa175a14dd7204f2f3571028d429752da2521f6fb9e096ad8b',
        ROOT / 'core/graph/nodes/report_node_v3.py':
            '1fc33b531fddd9ec3635b7e8a938f1e9052489b0013c00affb9116790a859715',
    }
    for path, expected in frozen.items():
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError(f'Frozen file changed: {path}; verify before continuing.')
    paths = [CASE_PATH, SQL_PROMPT, REPORT_PROMPT, Path(__file__),
             ROOT / 'core/agents/sql_agent.py', ROOT / 'core/agents/report_agent.py',
             ROOT / 'core/graph/nodes/sql_node.py', ROOT / 'core/graph/nodes/report_node_v3.py',
             ROOT / 'core/graph/workflow.py', ROOT / 'core/graph/router.py',
             ROOT / 'core/graph/state.py', ROOT / 'core/tools/sql_readonly_guard.py',
             ROOT / 'tests/system_eval/eval_sql_v33_real.py',
             ROOT / 'tests/system_eval/eval_sql_v3_real.py',
             ROOT / 'tests/system_eval/audit_sql_v311_fidelity.py',
             ROOT / 'tests/system_eval/eval_report_fidelity_v3.py']
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in paths}
    return cases, load_yaml(SQL_PROMPT)['system_prompt'], load_yaml(REPORT_PROMPT)['system_prompt'], hashes


def check_hashes(hashes):
    for name, expected in hashes.items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError(f'File changed during run: {name}')


async def real_case(case, gold_tool, sql_prompt, report_prompt, directory):
    agent = RecordedSQLAgent(sql_prompt)
    reports = CapturedReportAgent(report_prompt)
    sql_updates = []
    report_inputs = []
    actual_sql_node = build_sql_node(agent)
    actual_report_node = build_report_node(reports)

    async def observed_sql_node(state):
        update = await actual_sql_node(state)
        sql_updates.append(deepcopy(update))
        return update

    def observed_report_node(state):
        report_inputs.append(deepcopy(dict(state)))
        return actual_report_node(state)

    record = dict(case, evaluation_type='real_sql_report_graph',
                  automatic_passed=False, manual_review_status='pending',
                  gold_status='error', report_calls=0)
    try:
        content, gold_rows = await run_gold_query(gold_tool, case['id'], case['gold_sql'])
        record.update(gold_status='success', gold_content=content, gold_rows=gold_rows)
        final = await make_workflow(observed_sql_node, observed_report_node).ainvoke(
            input_state(case['question']))
        raw = agent.last_raw_state or {}
        messages = list(raw.get('messages', []))
        evidence = agent.last_evidence or {}
        call = extract_execute_sql_call(messages)
        tool_result = extract_execute_result(messages, evidence.get('tool_call_id'))
        rows = parse_mcp_text(tool_result.content) if tool_result is not None else []
        matched = (evidence.get('query_verified') is True
                   and tool_result is not None and tool_result.status == 'success'
                   and call is not None and call.get('id') == evidence.get('tool_call_id')
                   and rows == gold_rows)
        sql_answer = str(evidence.get('answer') or '')
        sql_fidelity, sql_issues = audit_answer(category=case['category'],
            gold_rows=gold_rows, actual_rows=rows, answer=sql_answer, result_match=matched)
        report_text = str(final.get('report_result') or '')
        report_fidelity, report_issues = audit_answer(category=case['category'],
            gold_rows=gold_rows, actual_rows=rows, answer=report_text, result_match=matched)
        forwarded = (len(sql_updates) == 1 and len(report_inputs) == 1
                     and report_inputs[0].get('sql_result') == sql_updates[0].get('sql_result')
                     and report_inputs[0].get('sql_result') == sql_answer.strip()
                     and report_inputs[0].get('sql_status') == 'success')
        route_ok = final.get('execution_trace') == ['sql', 'report']
        status_ok = (final.get('sql_status') == 'success'
                     and final.get('report_status') == 'success'
                     and final.get('status') == 'completed')
        record.update(final_state=final, sql_evidence=evidence,
            generated_sql=call.get('query') if call else None, actual_rows=rows,
            query_verified=evidence.get('query_verified') is True,
            result_match=matched, sql_answer_fidelity=sql_fidelity,
            sql_answer_issues=sql_issues, report_fidelity_rules_passed=report_fidelity,
            report_issues=report_issues, state_forwarding_correct=forwarded,
            execution_route_correct=route_ok, status_correct=status_ok,
            report_calls=len(reports.instructions),
            raw_database_rows_forwarded_to_report=False,
            boundary_review_flags=boundary_review_flags(report_text,
                report_inputs[0] if report_inputs else {}),
            automatic_passed=bool(matched and sql_fidelity and report_fidelity
                                  and forwarded and route_ok and status_ok
                                  and len(reports.instructions) == 1))
        (directory / f"{case['id']}_report.txt").write_text(report_text, encoding='utf-8')
    except Exception as exc:
        record.update(error=f'{type(exc).__name__}: {exc}',
                      manual_review_status='not_evaluable')
    finally:
        record['report_calls'] = len(reports.instructions)
        (directory / f"{case['id']}_instruction.txt").write_text(
            '\n\n'.join(reports.instructions), encoding='utf-8')
        save_json(directory / f"{case['id']}_node_states.json",
                  dict(sql_updates=sql_updates, report_inputs=report_inputs))
        if agent.last_raw_state:
            messages = agent.last_raw_state.get('messages', [])
            save_json(directory / f"{case['id']}_sql_messages.json",
                [x.model_dump(mode='json') if hasattr(x, 'model_dump') else str(x)
                 for x in messages])
    return record


def direct_failure_report_probe(report_prompt, directory):
    """Controlled upstream error; direct Report invocation outside the graph."""
    agent = CapturedReportAgent(report_prompt)
    state = dict(user_query='请整理该查询异常记录。', sql_status='error',
                 sql_result='', sql_error='受控测试异常：未获得查询结果')
    final = build_report_node(agent)(state)
    report = str(final.get('report_result') or '')
    passed = (final.get('report_status') == 'success' and bool(report.strip())
              and '受控测试异常' in report and '未获得查询结果' in report
              and len(agent.instructions) == 1)
    (directory / 'IR406_report.txt').write_text(report, encoding='utf-8')
    (directory / 'IR406_instruction.txt').write_text('\n\n'.join(agent.instructions), encoding='utf-8')
    return dict(id='IR406', evaluation_type='controlled_error_direct_real_report',
        real_llm_used=True, real_mysql_used=False, is_workflow_failure_report=False,
        input_state=state, final_state=final, report_calls=len(agent.instructions),
        automatic_passed=passed, manual_review_status='pending',
        boundary_review_flags=boundary_review_flags(report, state))


async def main():
    cases, sql_prompt, report_prompt, hashes = preflight()
    require_safe_environment()
    # Verify failure routing before making any LLM/MySQL calls. If this fails,
    # preserve the probe and stop; never pretend the graph renders errors.
    directory = ROOT / 'tests/system_eval/results' / (
        'sql_report_integration_v1_' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    directory.mkdir(parents=True, exist_ok=False)
    save_json(directory / 'manifest.json', dict(file_sha256=hashes,
        llm_model=os.getenv('LLM_MODEL'), mysql_user=os.getenv('MYSQL_USER'),
        database=os.getenv('MYSQL_DATABASE'), mcp_transport=os.getenv('MYSQL_MCP_TRANSPORT'),
        main_chain='SQLAgent -> SQL Node -> existing LangGraph -> Report Node V3',
        production_workflow_modified=False, report_input_type='SQLAgent natural-language answer',
        scope='four real success chains; two separately labelled controlled-error probes'))
    save_json(directory / 'input_cases_snapshot.json', cases)
    probe = await routing_probe()
    records = [probe]
    save_json(directory / 'case_results.json', records)
    if not probe['automatic_passed']:
        raise RuntimeError(f'Existing failure routing differs from expected. See {directory}')
    print('IR405 controlled failure routing: PASS; Report calls: 0', flush=True)
    gold_tool = await build_gold_tool()
    for case in cases:
        check_hashes(hashes)
        print(f"\nRunning {case['id']}: {case['question']}", flush=True)
        try:
            record = await real_case(case, gold_tool, sql_prompt, report_prompt, directory)
        except Exception as exc:
            record = dict(id=case['id'], evaluation_type='real_sql_report_graph',
                automatic_passed=False, error=f'{type(exc).__name__}: {exc}',
                manual_review_status='not_evaluable')
        records.append(record)
        save_json(directory / 'case_results.json', records)
        print('PASS' if record['automatic_passed'] else 'FAIL',
              '| DB match:', record.get('result_match'),
              '| Forwarding:', record.get('state_forwarding_correct'),
              '| Route:', record.get('execution_route_correct'), flush=True)
        if record.get('error'):
            print('Error:', record['error'], flush=True)
    try:
        direct = direct_failure_report_probe(report_prompt, directory)
    except Exception as exc:
        direct = dict(id='IR406', evaluation_type='controlled_error_direct_real_report',
                      automatic_passed=False, error=f'{type(exc).__name__}: {exc}',
                      manual_review_status='not_evaluable')
    records.append(direct)
    save_json(directory / 'case_results.json', records)
    check_hashes(hashes)
    real = [r for r in records if r['evaluation_type'] == 'real_sql_report_graph']
    summary = dict(evaluation_id='sql_report_integration_v1',
        real_sql_report_cases=len(real),
        real_sql_report_automatic_passed=sum(r['automatic_passed'] for r in real),
        controlled_failure_routing_passed=probe['automatic_passed'],
        direct_controlled_error_report_passed=direct['automatic_passed'],
        pending_full_text_reviews=sum(r.get('manual_review_status') == 'pending' for r in records),
        verified_full_text_factual_accuracy=None,
        note='No combined accuracy across real chains and controlled probes. '
             'SQL failure in the existing workflow ends before Report. '
             'IR406 is a separate direct report call, not a graph failure report. '
             'SQL Node forwards the answer text, not database rows. '
             'These reused validated questions test integration, not new-query generalization.')
    save_json(directory / 'summary.json', summary)
    print('\n=== SUMMARY ===\n' + json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print('Results:', directory, flush=True)


if __name__ == '__main__':
    asyncio.run(main())
