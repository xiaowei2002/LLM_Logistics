"""V3.4 new-case evaluation with the frozen V3.3 prompt.

Run from backend: python -m tests.system_eval.eval_sql_v34_real
Requires the existing V3.3 evaluator and its project dependencies.
Gold SQL is never passed to the agent. Automatic fidelity checks are
necessary checks; full-text human review remains pending for every case.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

os.environ['PYTHON_DOTENV_DISABLED'] = '1'

from core.agents.sql_agent import SQLAgent
from core.tools.sql_evidence import inspect_sql_evidence
from core.tools.sql_readonly_guard import validate_readonly_sql
from core.utils.yaml_loader import load_yaml
from tests.system_eval.eval_sql_v33_real import (
    audit_answer, build_gold_tool, require_safe_environment,
)
from tests.system_eval.eval_sql_v3_real import (
    extract_execute_result, extract_execute_sql_call, parse_mcp_text,
)

ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = ROOT / 'tests/system_eval/cases/sql_v34_real_cases.json'
PROMPT_PATH = ROOT / 'core/prompts/sql_agent_olist_v33.yaml'
GUARD_PATH = ROOT / 'core/tools/sql_readonly_guard.py'
RESULT_ROOT = ROOT / 'tests/system_eval/results'


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                               default=str) + '\n', encoding='utf-8')
    temp.replace(path)


def preflight() -> tuple[list[dict[str, Any]], str, dict[str, str]]:
    cases = json.loads(CASES_PATH.read_text(encoding='utf-8-sig'))
    if not isinstance(cases, list) or len(cases) != 20:
        raise ValueError('V3.4 requires exactly 20 cases.')
    ids: set[str] = set()
    old_path = CASES_PATH.with_name('sql_v32_real_cases.json')
    old = json.loads(old_path.read_text(encoding='utf-8-sig'))
    old_questions = {x['question'] for x in old}
    old_sql = {x['gold_sql'] for x in old}
    for case in cases:
        for key in ('id', 'category', 'coverage', 'question', 'gold_sql'):
            if not isinstance(case.get(key), str) or not case[key].strip():
                raise ValueError(f'Missing/invalid {key}: {case}')
        if case['id'] in ids:
            raise ValueError(f"Duplicate case: {case['id']}")
        ids.add(case['id'])
        if case['question'] in old_questions or case['gold_sql'] in old_sql:
            raise ValueError(f"Reused V3.2 question/SQL: {case['id']}")
        if case['category'] not in {'filter_count', 'aggregation',
                                    'group_by', 'empty_result'}:
            raise ValueError(f"Unsupported category: {case['category']}")
        validate_readonly_sql(case['gold_sql'])
    prompt = load_yaml(PROMPT_PATH)['system_prompt']
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError('V3.3 system_prompt is empty.')
    hashes = {str(p.relative_to(ROOT)): sha256(p) for p in
              (PROMPT_PATH, GUARD_PATH, CASES_PATH, Path(__file__),
               ROOT / 'tests/system_eval/eval_sql_v33_real.py')}
    return cases, prompt, hashes


def assert_unchanged(hashes: dict[str, str]) -> None:
    for name, expected in hashes.items():
        if sha256(ROOT / name) != expected:
            raise RuntimeError(f'File changed during evaluation: {name}')


def empty_answer_audit(answer: str) -> tuple[bool, list[str]]:
    # An explicit empty-result statement is required. This is still a rule
    # check; additions or contradictions require full-text human review.
    patterns = (r'(没有|未找到|未查询到|不存在|无)\s*(任何)?\s*(符合|满足)',
                r'(未找到|未查询到)\s*(任何)?\s*记录',
                r'(结果|记录|明细).{0,8}(为空|为\s*0\s*条)',
                r'\bno\s+(matching\s+)?(records|rows|results)\b')
    passed = any(re.search(p, answer, re.I) for p in patterns)
    return passed, [] if passed else ['No explicit empty-result statement.']


def failure(error: Exception) -> dict[str, Any]:
    return dict(status='error', query_verified=False, generated_sql=None,
                tool_call_id=None, tool_result_status=None, actual_rows=[],
                result_match=False, answer='', fidelity_pass=False,
                fidelity_problems=[], overall_correct=False,
                error=f'{type(error).__name__}: {error}')


async def agent_case(case: dict[str, Any], gold_rows: list[list[str]],
                     prompt: str) -> dict[str, Any]:
    # A fresh agent per case avoids cross-case conversation contamination.
    agent = SQLAgent()
    agent.system_prompt = prompt
    try:
        state = await agent._invoke_agent(case['question'])
        messages = list(state.get('messages', []))
        evidence = inspect_sql_evidence(messages)
        call = extract_execute_sql_call(messages)
        tool_result = extract_execute_result(
            messages, call.get('id') if call else None)
        rows = parse_mcp_text(tool_result.content) if tool_result is not None else []
        verified = evidence.get('query_verified') is True
        tool_success = (tool_result is not None and tool_result.status == 'success')
        # Empty lists alone cannot establish successful execution.
        match = verified and tool_success and rows == gold_rows
        answer = str(evidence.get('answer', '') or '')
        if case['category'] == 'empty_result':
            fidelity, issues = empty_answer_audit(answer)
            fidelity = match and fidelity
            if not match:
                issues = ['Database result does not match verified gold result.'] + issues
        else:
            fidelity, issues = audit_answer(category=case['category'],
                gold_rows=gold_rows, actual_rows=rows, answer=answer,
                result_match=match)
        correct = (evidence.get('status') == 'success' and verified
                   and match and fidelity)
        return dict(status=evidence.get('status'), query_verified=verified,
            generated_sql=call.get('query') if call else None,
            tool_call_id=call.get('id') if call else None,
            tool_result_status=tool_result.status if tool_result is not None else None,
            actual_rows=rows, result_match=match, answer=answer,
            fidelity_pass=fidelity, fidelity_problems=issues,
            overall_correct=correct, error=evidence.get('error'))
    except Exception as exc:
        return failure(exc)


async def main() -> None:
    # All gold statements are checked locally before any network call.
    cases, prompt, hashes = preflight()
    require_safe_environment()
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    result_dir = RESULT_ROOT / f'sql_v34_real_{stamp}'
    result_dir.mkdir(parents=True, exist_ok=False)
    manifest = dict(evaluation='SQLAgent V3.4 new-case generalization',
        timestamp=stamp, file_sha256=hashes, prompt=PROMPT_PATH.name,
        model=os.getenv('LLM_MODEL'), database=os.getenv('MYSQL_DATABASE'),
        mysql_user=os.getenv('MYSQL_USER'),
        mcp_transport=os.getenv('MYSQL_MCP_TRANSPORT'),
        new_agent_per_case=True, prompt_modified_by_script=False,
        secrets_recorded=False, expected_cases=len(cases))
    write_json(result_dir / 'manifest.json', manifest)
    print('=== SQLAgent V3.4 NEW-CASE GENERALIZATION ===', flush=True)
    print('Cases:', len(cases), '| Frozen prompt:', PROMPT_PATH.name, flush=True)
    print('Prompt SHA256:', hashes[str(PROMPT_PATH.relative_to(ROOT))], flush=True)
    records: list[dict[str, Any]] = []
    gold_tool = await build_gold_tool()
    for index, case in enumerate(cases, 1):
        assert_unchanged(hashes)
        print(f"\n[{index}/{len(cases)}] {case['id']} {case['question']}", flush=True)
        record = dict(case, gold_status='error', gold_content=None,
                      gold_rows=None, manual_review_status='pending')
        try:
            gold = await gold_tool.ainvoke(dict(type='tool_call',
                id=f"v34-gold-{case['id']}", name='execute_sql',
                args={'query': case['gold_sql']}))
            if gold.status != 'success':
                raise RuntimeError(f'Gold SQL failed: {gold.content}')
            rows = parse_mcp_text(gold.content)
            if case['category'] == 'empty_result' and rows != []:
                raise RuntimeError('Expected-empty case is not empty in this database.')
            if case['category'] != 'empty_result' and not rows:
                raise RuntimeError('Gold query unexpectedly returned no rows.')
            record.update(gold_status='success', gold_content=gold.content,
                          gold_rows=rows)
            record.update(await agent_case(case, rows, prompt))
        except Exception as exc:
            record.update(failure(exc))
        records.append(record)
        # Save after every case, including gold-query errors.
        write_json(result_dir / 'case_results.json', records)
        print('Gold rows:', record['gold_rows'], flush=True)
        print('Generated SQL:', record['generated_sql'], flush=True)
        print('Verified:', record['query_verified'], '| DB match:',
              record['result_match'], '| Automatic fidelity:',
              record['fidelity_pass'], flush=True)
        print('Overall:', 'PASS' if record['overall_correct'] else 'FAIL', flush=True)
        if record['error']:
            print('Error:', record['error'], flush=True)
        for issue in record['fidelity_problems']:
            print('Fidelity issue:', issue, flush=True)
    assert_unchanged(hashes)
    counts = {k: sum(r[k] is True for r in records) for k in
              ('query_verified', 'result_match', 'fidelity_pass', 'overall_correct')}
    summary = dict(manifest, completed_cases=len(records),
        gold_error_cases=sum(r['gold_status'] != 'success' for r in records),
        query_verified_cases=counts['query_verified'],
        result_correct_cases=counts['result_match'],
        fidelity_pass_cases=counts['fidelity_pass'],
        overall_correct_cases=counts['overall_correct'],
        overall_accuracy=counts['overall_correct'] / len(cases),
        pending_manual_reviews=len(records),
        verified_full_text_factual_accuracy=None,
        note='New questions on the same Olist database; not cross-dataset validation. '
             'Automatic fidelity rules do not establish full-text factual accuracy. '
             'Gold-query failures are evaluator/data errors, not model errors. '
             'File hashes establish within-run consistency; compare the prompt '
             'hash with the V3.3 file manually for historical freezing.')
    write_json(result_dir / 'summary.json', summary)
    print('\n=== SUMMARY ===', flush=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print('Results:', result_dir, flush=True)


if __name__ == '__main__':
    asyncio.run(main())
