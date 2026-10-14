"""Safe Cleaning -> real files/Gate -> existing graph -> frozen Report.

Run from backend with --preflight-only first, then without it.
Fixtures and approvals are controlled test inputs, not production/HITL identity
evidence. Safe cleaning is deterministic; the legacy CleaningAgent is not used.
"""
from __future__ import annotations
import argparse
import asyncio
import csv
import hashlib
import json
import os
import re
import shutil
from copy import deepcopy
from datetime import datetime
from pathlib import Path

os.environ['PYTHON_DOTENV_DISABLED'] = '1'
ROOT = Path(__file__).resolve().parents[2]
PROMPT = ROOT / 'core/prompts/report_agent_v31.yaml'
FROZEN_PROMPT = 'da29d4c1a48c3bfa175a14dd7204f2f3571028d429752da2521f6fb9e096ad8b'
FROZEN_NODE = '1fc33b531fddd9ec3635b7e8a938f1e9052489b0013c00affb9116790a859715'
QUERY = ('请总结本次受限清洗与质量门禁记录。以 Markdown 表格给出原始行数、'
         '清洗后行数、删除行数三列，每列对应实际记录，只写一行数值。'
         '说明门禁状态、保留的警告以及输入提供的审批标记。'
         '本次是评估：审批记录及大比例删行确认均由测试脚本自动构造，'
         '不代表真实用户参与，也不验证审批人身份。'
         '不新增业务结论，不把门禁通过扩大为可以直接训练、预测或投产。')
COLS = ['order_no', 'sku', 'qty', 'created_at', 'warehouse']


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2,
                                   default=str) + '\n', encoding='utf-8')


def rows(unique, duplicates=0, negative=False):
    data = [dict(zip(COLS, [f'O{i:03d}', 'M001', str(10+i),
             f'2026-01-01 {i:02d}:00:00', 'WH01'])) for i in range(unique)]
    if negative:
        data[0]['qty'] = '-2'
    return data + [deepcopy(data[-1]) for _ in range(duplicates)]


def fixture(folder, name, data):
    path = (folder / f'{name}.csv').resolve()
    with path.open('x', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=COLS)
        writer.writeheader()
        writer.writerows(data)
    return path


def read_rows(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        return reader.fieldnames, list(reader)


def approval(path):
    from core.graph.cleaning_approval import build_approval_proposal
    proposal = build_approval_proposal(path)
    return dict(approval_type='cleaning_operation', approval_status='approved',
        source_sha256=proposal['source_sha256'], approved_operations=[dict(
        operation='remove_exact_duplicates', approved_csv_lines=
        proposal['allowed_candidate_operations'][0]['candidate_csv_lines'])])


def initial(path, approved=False):
    state = dict(user_query=QUERY, task_mode='cleaning_safe_report',
                 data_file_path=str(path), status='running', execution_trace=[])
    if approved:
        state['cleaning_approval'] = approval(path)
    return state


def resume(state):
    value = deepcopy(state)
    value.update(cleaning_large_deletion_approved=True, status='running',
                 execution_trace=[])
    return value


def deterministic_preflight(state):
    """Actual node functions and router, no LangGraph or simulated Report.
    This is only node preflight; full graph is exercised in the normal run.
    """
    from core.graph.router import route_from_start, route_after_agent
    from core.graph.nodes.cleaning_precheck_node import cleaning_precheck_node
    from core.graph.nodes.restricted_cleaning_node import restricted_cleaning_node
    from core.graph.nodes.cleaning_quality_node import cleaning_quality_node
    from core.graph.nodes.cleaning_resume_node import cleaning_resume_node
    nodes = dict(cleaning_precheck=cleaning_precheck_node,
                 restricted_cleaning=restricted_cleaning_node,
                 cleaning_quality=cleaning_quality_node,
                 cleaning_resume=cleaning_resume_node)
    following = dict(cleaning_precheck='restricted_cleaning',
                     restricted_cleaning='cleaning_quality',
                     cleaning_resume='cleaning_quality', cleaning_quality='report')
    value = deepcopy(state)
    name = route_from_start(value)
    while name != 'report':
        update = nodes[name](value)
        trace = value.get('execution_trace', []) + update.get('execution_trace', [])
        value.update(update)
        value['execution_trace'] = trace
        if route_after_agent(value) != 'report':
            return value
        name = following[name]
    value['status'] = 'ready_for_report'
    return value


def table_check(text, expected):
    """Requires the requested three-column table, not unrelated token hits."""
    matches = []
    for line in text.splitlines():
        if not line.strip().startswith('|'):
            continue
        cells = [re.sub(r'[*`]', '', c).strip() for c in
                 line.strip().strip('|').split('|')]
        if len(cells) == 3 and all(re.fullmatch(r'\d+', c) for c in cells):
            matches.append([int(c) for c in cells])
    return matches == [expected], matches


def disk_check(state):
    execution = state.get('cleaning_execution')
    if not execution:
        return None
    source = state['data_file_path']
    output = state['cleaned_file_path']
    cols, original = read_rows(source)
    outcols, cleaned = read_rows(output)
    lines = execution['approved_csv_lines']
    expected = [r for i, r in enumerate(original, 2) if i not in lines]
    return dict(source_unchanged=sha(source) == execution['source_sha256'],
                output_hash_matches=sha(output) == execution['output_sha256'],
                exact_retained_rows=cols == outcols and cleaned == expected,
                counts_match=execution['source_rows'] == len(original) and
                execution['output_rows'] == len(cleaned) and
                execution['deleted_rows'] == len(original)-len(cleaned) == len(lines))


async def main(preflight):
    paths = [ROOT / 'core/graph'/f for f in (
        'cleaning_precheck.py', 'cleaning_approval.py', 'cleaning_executor.py',
        'cleaning_quality.py', 'router.py', 'state.py', 'workflow.py')]
    paths += [ROOT/'core/graph/nodes'/f for f in (
        'cleaning_precheck_node.py', 'restricted_cleaning_node.py',
        'cleaning_quality_node.py', 'cleaning_resume_node.py', 'report_node_v3.py')]
    paths += [PROMPT, Path(__file__)]
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)
    if sha(PROMPT) != FROZEN_PROMPT or sha(ROOT/'core/graph/nodes/report_node_v3.py') != FROZEN_NODE:
        raise RuntimeError('Frozen Report prompt/node differs from previous evaluations.')
    agent_type = build_report = build_workflow = prompt = None
    if not preflight:
        if not os.getenv('LLM_API_KEY') or not os.getenv('LLM_MODEL'):
            raise RuntimeError('Set LLM_API_KEY and LLM_MODEL in this terminal.')
        from core.graph.workflow import build_logistics_workflow
        from core.graph.nodes.report_node_v3 import build_report_node
        from core.utils.yaml_loader import load_yaml
        from tests.system_eval.eval_report_fidelity_v3 import CapturedReportAgent
        agent_type, build_report, build_workflow = CapturedReportAgent, build_report_node, build_logistics_workflow
        prompt = load_yaml(PROMPT)['system_prompt']
        paths += [ROOT/'core/agents/report_agent.py',
                  ROOT/'tests/system_eval/eval_report_fidelity_v3.py']
    fingerprints = {str(p.relative_to(ROOT)):sha(p) for p in paths}
    label = 'preflight' if preflight else 'real'
    folder = ROOT/'tests/system_eval/results'/('cleaning_report_integration_v1_'+
                label+'_'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    folder.mkdir(parents=True, exist_ok=False)
    fixtures = folder/'fixtures'
    fixtures.mkdir()
    clean = fixture(fixtures, 'unique', rows(10))
    boundary = fixture(fixtures, 'one_of_ten', rows(9, 1))
    large = fixture(fixtures, 'three_of_ten', rows(7, 3))
    invalid = fixture(fixtures, 'negative_three_of_ten', rows(7, 3, True))
    source_hashes = {str(p):sha(p) for p in fixtures.glob('*.csv')}
    save(folder/'manifest.json', dict(file_sha256=fingerprints,
        fixture_source_sha256=source_hashes, real_llm_used=not preflight,
        actual_langgraph_used=not preflight, legacy_cleaning_agent_used=False,
        controlled_approvals=True, real_human_participation=False,
        llm_model=os.getenv('LLM_MODEL') if not preflight else None))
    records = []

    async def run(cid, name, state, expected_trace, expected_status,
                  expected_counts=None, expected_reason=None, rejected_output=False):
        for rel, digest in fingerprints.items():
            if sha(ROOT/rel) != digest:
                raise RuntimeError(f'Source changed during evaluation: {rel}')
        save(folder/f'{cid}_input.json', state)
        print(f'Running {cid}: {name}', flush=True)
        instructions = []
        report_inputs = []
        if preflight:
            final = deterministic_preflight(state)
        else:
            agent = agent_type(prompt)
            report_node = build_report(agent)
            def observed_report(value):
                report_inputs.append(deepcopy(dict(value)))
                return report_node(value)
            def unused(value):
                raise AssertionError('Unexpected legacy Cleaning/SQL/Forecast branch.')
            graph = build_workflow(sql_node=unused, cleaning_node=unused,
                forecast_node=unused, report_node=observed_report)
            final = dict(await graph.ainvoke(deepcopy(state)))
            instructions = agent.instructions
        save(folder/f'{cid}_final_state.json', final)
        report = final.get('report_result') or ''
        (folder/f'{cid}_report.txt').write_text(report, encoding='utf-8')
        save(folder/f'{cid}_report_input_states.json', report_inputs)
        save(folder/f'{cid}_instructions.json', instructions)
        for field in ('data_file_path', 'cleaned_file_path'):
            if final.get(field) and Path(final[field]).is_file():
                shutil.copyfile(final[field], folder/f'{cid}_{field}.csv')
        target_trace = expected_trace[:-1] if preflight and expected_status == 'completed' else expected_trace
        target_status = 'ready_for_report' if preflight and expected_status == 'completed' else expected_status
        checks = dict(route=final.get('execution_trace') == target_trace,
                      status=final.get('status') == target_status,
                      original_sources_unchanged=all(sha(p)==h for p,h in source_hashes.items()))
        if expected_reason:
            checks['interruption_reason'] = final.get('human_reason') == expected_reason
        expected_calls = 0 if preflight or expected_status != 'completed' else 1
        checks['report_calls'] = len(instructions) == len(report_inputs) == expected_calls
        if not preflight and expected_status == 'completed':
            checks['report_status'] = final.get('report_status') == 'success'
        if target_trace == ['cleaning_precheck']:
            checks['no_output_created'] = not final.get('cleaned_file_path') and not final.get('cleaning_execution')
        disk = disk_check(final)
        if disk is not None and not rejected_output:
            checks['disk_execution_evidence'] = all(disk.values())
        table_pass = None
        table_observed = []
        if expected_counts is not None:
            quality = final.get('cleaning_quality', {})
            checks['quality_counts'] = [quality.get(k) for k in
                ('source_row_count', 'cleaned_row_count', 'removed_row_count')] == expected_counts
            if expected_status == 'completed':
                checks['quality_passed'] = quality.get('passed') is True
            if not preflight and expected_status == 'completed':
                table_pass, table_observed = table_check(report, expected_counts)
                checks['report_requested_table'] = table_pass
                checks['structured_quality_forwarded'] = json.dumps(quality, ensure_ascii=False,
                    indent=2, default=str) in instructions[0] if instructions else False
                checks['execution_forwarded'] = final['cleaning_result'] in instructions[0] if instructions else False
        record = dict(id=cid, name=name, checks=checks, passed=all(checks.values()),
            disk_audit=disk, rejected_output_probe=rejected_output,
            report_table_observed=table_observed, report_calls=len(instructions),
            generated_report=bool(report) and final.get('report_status') == 'success',
            manual_review_status='pending' if report else 'not_applicable')
        records.append(record)
        save(folder/'case_results.json', records)
        print(('PASS' if record['passed'] else 'FAIL')+' | '+json.dumps(checks), flush=True)
        return final

    first = ['cleaning_precheck', 'restricted_cleaning', 'cleaning_quality']
    await run('CR601', 'zero deletion without fabricated approval', initial(clean),
              first+['report'], 'completed', [10,10,0])
    await run('CR602', 'duplicates without operation approval', initial(boundary),
              ['cleaning_precheck'], 'needs_human_input', expected_reason='precleaning_approval')
    await run('CR603', 'approved deletion exactly 10 percent', initial(boundary, True),
              first+['report'], 'completed', [10,9,1])
    paused = await run('CR604', 'approved deletion above 10 percent pauses', initial(large, True),
              first, 'needs_human_input', [10,7,3], 'large_deletion')
    output_hash = sha(paused['cleaned_file_path'])
    restored = await run('CR605', 'controlled confirmation and existing-file resume', resume(paused),
              ['cleaning_resume','cleaning_quality','report'], 'completed', [10,7,3])
    records[-1]['checks']['output_unchanged_on_resume'] = sha(restored['cleaned_file_path']) == output_hash
    records[-1]['checks']['resume_validation'] = restored.get('cleaning_approval_validation',{}).get('valid') is True
    records[-1]['passed'] = all(records[-1]['checks'].values())
    altered = folder/'tampered_output.csv'
    shutil.copyfile(paused['cleaned_file_path'], altered)
    content = altered.read_text(encoding='utf-8-sig').replace(',10,', ',999,', 1)
    if content == altered.read_text(encoding='utf-8-sig'):
        raise RuntimeError('Tampering probe failed to alter an existing value.')
    altered.write_text(content, encoding='utf-8-sig')
    tampered = resume(paused)
    tampered['cleaned_file_path'] = str(altered.resolve())
    tampered['cleaning_execution']['output_file'] = str(altered.resolve())
    await run('CR606', 'changed output hash rejected before Gate/Report', tampered,
              ['cleaning_resume'], 'failed', rejected_output=True)
    bad_pause = await run('CR607a', 'large deletion and retained negative qty', initial(invalid, True),
              first, 'needs_human_input', [10,7,3], 'large_deletion')
    bad_final = await run('CR607b', 'confirmation cannot waive negative qty', resume(bad_pause),
              ['cleaning_resume','cleaning_quality'], 'failed', [10,7,3])
    records[-1]['checks']['negative_error_retained'] = (bad_final['cleaning_quality'].get('passed') is False
        and bad_final['cleaning_quality'].get('negative_qty_count') == 1
        and any('负值' in e for e in bad_final['cleaning_quality'].get('errors', [])))
    records[-1]['passed'] = all(records[-1]['checks'].values())
    bad_approval = initial(boundary, True)
    bad_approval['cleaning_approval']['approved_operations'][0]['approved_csv_lines'] = [2]
    await run('CR608', 'nonduplicate row approval rejected', bad_approval,
              ['cleaning_precheck'], 'failed')
    save(folder/'case_results.json', records)
    summary = dict(evaluation_id='cleaning_report_integration_v1', mode=label,
        stage_checks=len(records), automatically_passed_stages=sum(r['passed'] for r in records),
        report_generate_calls=sum(r['report_calls'] for r in records),
        generated_reports=sum(r['generated_report'] for r in records),
        pending_full_text_reviews=sum(r['manual_review_status']=='pending' for r in records),
        verified_full_text_factual_accuracy=None,
        note='Eight scenarios, nine stages; deterministic safe cleaning on controlled CSV fixtures. '
        'Approvals are automated test records, not authentic human approvals. '
        'No legacy CleaningAgent/MCP evaluation. Normal run uses existing LangGraph and real Report LLM; '
        'preflight only executes actual deterministic nodes/router. Table format checks are not full-text review.')
    save(folder/'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print('Results:', folder)
    if not all(r['passed'] for r in records):
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--preflight-only', action='store_true')
    asyncio.run(main(parser.parse_args().preflight_only))
