"""Real Forecast -> existing graph -> Report integration; CPU model tool.
Run from backend: python -m tests.system_eval.eval_forecast_report_integration_v1
Default selects two eligible timestamps from the final 15% holdout window.
HITL recovery is separately exercised by an automated controlled clarification.
It is not evidence that the outer workflow implements automatic resume.
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
import os
import re
import uuid
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

os.environ['PYTHON_DOTENV_DISABLED'] = '1'

from core.agents.demand_forecast_agent import DemandForecastAgent
from core.agents.human_review import inspect_forecast_query
from core.graph.nodes.forecast_node import build_forecast_node
from core.graph.nodes.report_node_v3 import build_report_node
from core.graph.workflow import build_logistics_workflow
from core.tools.demand_forecast import state_conditioned_forecast_tool as model_tool
from core.utils.yaml_loader import load_yaml
from tests.system_eval.eval_report_fidelity_v3 import CapturedReportAgent, save_json

ROOT = Path(__file__).resolve().parents[2]
REPORT_PROMPT = ROOT / 'core/prompts/report_agent_v31.yaml'
MATERIALS = ('M001', 'M003')


class ObservedBaseAgent:
    def __init__(self, base):
        self.base = base
        self.states = []
    def invoke(self, *args, **kwargs):
        result = self.base.invoke(*args, **kwargs)
        self.states.append(result)
        return result


def new_forecast_agent():
    agent = DemandForecastAgent()
    observed = ObservedBaseAgent(agent.base_agent)
    agent.base_agent = observed
    return agent, observed


def unused_node(state):
    raise AssertionError('Unexpected SQL/Cleaning branch.')


def outer_workflow(forecast_node, report_node):
    return build_logistics_workflow(sql_node=unused_node, cleaning_node=unused_node,
        forecast_node=forecast_node, report_node=report_node)


def initial(query, thread):
    return dict(user_query=query, task_mode='forecast_report',
                forecast_thread_id=thread, status='running', execution_trace=[])


def question(timestamp, variant):
    return (f'预测基准时刻为 {timestamp}。请调用 demand_forecast，明确使用 '
            f'{variant} 模型，只整理 M001、M003 的 t+1、t+2 需求及 two_hour_peak_demand。'
            '以 Markdown 表格输出，列顺序为物料、t+1、t+2、两小时峰值，每物料一行，'
            '保留工具原值至四位小数；不要自行计算或新增数值。')


def table_audit(text, reference):
    """Requested table format: material followed by h1,h2,peak.
    This verifies row binding, not the factual accuracy of all surrounding prose.
    """
    found = {m: [] for m in MATERIALS}
    for line in text.splitlines():
        if not line.strip().startswith('|'):
            continue
        cells = [re.sub(r'[*`]', '', c).strip() for c in line.strip().strip('|').split('|')]
        for i, cell in enumerate(cells):
            if cell not in found:
                continue
            if len(cells) < i + 4:
                continue
            values = []
            for raw in cells[i+1:i+4]:
                cleaned = raw.replace(',', '').replace('，', '')
                if not re.fullmatch(r'-?\d+(?:\.\d+)?', cleaned):
                    break
                values.append(Decimal(cleaned))
            if len(values) == 3:
                found[cell].append(values)
    issues = []
    for m in MATERIALS:
        expected = [Decimal(str(reference['forecast']['t+1']['material_demand'][m])),
                    Decimal(str(reference['forecast']['t+2']['material_demand'][m])),
                    Decimal(str(reference['two_hour_peak_demand'][m]))]
        # Allow only decimal display precision to four places.
        if len(found[m]) != 1 or any(abs(a-b) > Decimal('0.00005')
                                     for a,b in zip(found[m][0] if found[m] else [], expected)):
            issues.append(f'{m}: required table values do not bind to tool reference; '
                          f'observed={found[m]}, expected={expected}')
    return not issues, issues


def reference_payload(payload):
    if isinstance(payload, dict) and payload.get('tool') == 'demand_forecast':
        result = payload.get('result')
        if payload.get('status') == 'success' and isinstance(result, dict) and result.get('status') == 'success':
            return result
    return None


def observed_evidence(observed):
    tools = []
    calls = []
    for state in observed.states:
        for message in state.get('messages', []):
            for call in getattr(message, 'tool_calls', []) or []:
                calls.append(call)
            if getattr(message, 'type', '') == 'tool':
                parsed = DemandForecastAgent._parse_tool_result(getattr(message,'content',None))
                tools.append(dict(name=getattr(message,'name',None),
                    tool_call_id=getattr(message,'tool_call_id',None),
                    status=getattr(message,'status',None), payload=parsed))
    return calls, tools


def eligible_reference(tools, calls, timestamp, variant):
    matches = []
    for tool in tools:
        p = tool['payload']
        ref = reference_payload(p)
        if ref is None or tool['status'] == 'error':
            continue
        if tool['name'] not in (None, 'demand_forecast'):
            continue
        linked = [c for c in calls if c.get('id') == tool['tool_call_id']
                  and c.get('name') == 'demand_forecast']
        if len(linked) != 1:
            continue
        args = linked[0].get('args',{})
        expected_version = {'baseline_sh':'e3_baseline_sh',
                            'state_conditioned':'e3_state_conditioned_final'}[variant]
        if (args.get('forecast_timestamp') == timestamp
            and args.get('variant','state_conditioned') == variant
            and p.get('forecast_timestamp') == timestamp
            and p.get('variant') == variant
            and ref.get('forecast_timestamp') == timestamp
            and ref.get('model',{}).get('version') == expected_version):
            matches.append(ref)
    return matches[-1] if matches else None


def numeric_core(result):
    return dict(forecast_timestamp=result['forecast_timestamp'],
        model_version=result['model']['version'],
        forecast={h:dict(timestamp=result['forecast'][h]['timestamp'],
                         material_demand=result['forecast'][h]['material_demand'])
                  for h in ('t+1','t+2')},
        peak=result['two_hour_peak_demand'])


def dump_observed(observed, path):
    messages = []
    for state in observed.states:
        messages.extend(x.model_dump(mode='json') if hasattr(x,'model_dump') else str(x)
                        for x in state.get('messages',[]))
    save_json(path,messages)


def ready_cases():
    # The tool computes a frozen Train/Val model in memory on first use.
    # Its CSV is operational input; no new training protocol is introduced.
    if not model_tool.PRODUCTION_FILE.is_file():
        raise FileNotFoundError(f'Prediction input missing: {model_tool.PRODUCTION_FILE}')
    model_tool._ensure_cache()
    metadata = model_tool._CACHE['metadata']
    if len(metadata) < 2:
        raise ValueError('Need at least two eligible forecast samples.')
    # Last two sample rows are in the holdout segment for this tool's 70/15 split.
    if len(metadata) < 20:
        raise ValueError('Dataset too small for this holdout integration selection.')
    stamps = [str(x) for x in metadata['history_end'].iloc[-2:].tolist()]
    cases = [dict(id='FR501',timestamp=stamps[0],variant='state_conditioned'),
             dict(id='FR502',timestamp=stamps[1],variant='baseline_sh')]
    for c in cases:
        c['question'] = question(c['timestamp'],c['variant'])
        inspection = inspect_forecast_query(c['question'])
        if not inspection['ready']:
            raise ValueError(f"Complete test question fails input inspection: {inspection}")
    return cases


def frozen_hashes():
    if hashlib.sha256(REPORT_PROMPT.read_bytes()).hexdigest() != 'da29d4c1a48c3bfa175a14dd7204f2f3571028d429752da2521f6fb9e096ad8b':
        raise RuntimeError('Frozen Report V3.1 prompt differs from the verified run.')
    paths = [Path(__file__),REPORT_PROMPT,
        ROOT/'core/agents/demand_forecast_agent.py', ROOT/'core/agents/human_review.py',
        ROOT/'core/prompts/demand_forecast.py', ROOT/'core/graph/nodes/forecast_node.py',
        ROOT/'core/graph/nodes/report_node_v3.py', ROOT/'core/graph/state.py',
        ROOT/'core/graph/router.py',ROOT/'core/graph/workflow.py',
        ROOT/'core/tools/demand_forecast/demand_forecast_tool.py',
        ROOT/'core/tools/demand_forecast/state_conditioned_forecast_tool.py',
        model_tool.PRODUCTION_FILE]
    if model_tool.METRICS_FILE.is_file():
        paths.append(model_tool.METRICS_FILE)
    return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def check_files(hashes):
    for name, expected in hashes.items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest() != expected:
            raise RuntimeError(f'File changed during run: {name}')


async def real_chain(case, prompt, directory):
    ref = model_tool.run_state_conditioned_forecast(case['timestamp'],case['variant'])
    if ref.get('status') != 'success':
        raise RuntimeError(f'Reference prediction failed: {ref}')
    agent, observed = new_forecast_agent()
    reports = CapturedReportAgent(prompt)
    forecast_updates = []
    report_inputs = []
    original_forecast = build_forecast_node(agent)
    original_report = build_report_node(reports)
    def fnode(state):
        update = original_forecast(state)
        forecast_updates.append(deepcopy(dict(update)))
        return update
    def rnode(state):
        report_inputs.append(deepcopy(dict(state)))
        return original_report(state)
    final = await outer_workflow(fnode,rnode).ainvoke(
        initial(case['question'], str(uuid.uuid4())))
    calls, tools = observed_evidence(observed)
    actual = eligible_reference(tools,calls,case['timestamp'],case['variant'])
    matches = actual is not None and numeric_core(actual) == numeric_core(ref)
    answer = str(final.get('forecast_result',{}).get('answer',''))
    report = str(final.get('report_result',''))
    answer_ok,answer_issues = table_audit(answer,ref)
    report_ok,report_issues = table_audit(report,ref)
    forward = (len(forecast_updates)==1 and len(report_inputs)==1
               and report_inputs[0].get('forecast_result') == forecast_updates[0].get('forecast_result')
               and report_inputs[0].get('forecast_status')=='success')
    route = final.get('execution_trace')==['forecast','report']
    status = (final.get('forecast_status')=='success' and final.get('report_status')=='success'
              and final.get('status')=='completed')
    save_json(directory/f"{case['id']}_reference.json",ref)
    save_json(directory/f"{case['id']}_tool_evidence.json",dict(calls=calls,tools=tools))
    save_json(directory/f"{case['id']}_node_states.json",dict(forecast_updates=forecast_updates,report_inputs=report_inputs))
    dump_observed(observed,directory/f"{case['id']}_messages.json")
    (directory/f"{case['id']}_report.txt").write_text(report,encoding='utf-8')
    (directory/f"{case['id']}_instruction.txt").write_text('\n\n'.join(reports.instructions),encoding='utf-8')
    return dict(case,evaluation_type='real_forecast_report_graph',final_state=final,
        tool_routing_and_values_match=matches,forecast_answer_table_passed=answer_ok,
        forecast_answer_issues=answer_issues,report_table_passed=report_ok,report_issues=report_issues,
        state_forwarding_correct=forward,route_correct=route,status_correct=status,
        raw_tool_payload_forwarded_to_report=('tool_result' in final.get('forecast_result',{})),
        report_calls=len(reports.instructions),manual_review_status='pending',
        automatic_passed=bool(matches and answer_ok and report_ok and forward and route and status
                              and len(reports.instructions)==1))


async def hitl_probe(case,prompt,directory):
    agent,observed = new_forecast_agent()
    reports = CapturedReportAgent(prompt)
    missing = '请使用 state_conditioned 模型预测 M001、M003 的未来两小时需求。'
    inspection = inspect_forecast_query(missing)
    if inspection['ready']:
        raise RuntimeError('Missing-time probe unexpectedly passes input inspection.')
    thread = str(uuid.uuid4())
    paused = await outer_workflow(build_forecast_node(agent),build_report_node(reports)).ainvoke(
        initial(missing,thread))
    before_calls = len(observed.states)
    pause_ok = (paused.get('status')=='needs_human_input'
                and paused.get('forecast_status')=='needs_human_input'
                and paused.get('execution_trace')==['forecast']
                and len(reports.instructions)==0 and before_calls==0)
    if not pause_ok:
        save_json(directory/'FR503_paused_state.json',paused)
        return dict(id='FR503',evaluation_type='real_interrupt_probe',automatic_passed=False,
                    paused_state=paused,error='Clarification interruption did not stop before prediction/Report.')
    # Controlled test clarification, not a real user's live approval.
    supplied = question(case['timestamp'],'state_conditioned')
    resumed = agent.resume(thread_id=thread,human_answer=supplied)
    calls,tools = observed_evidence(observed)
    actual = eligible_reference(tools,calls,case['timestamp'],'state_conditioned')
    ref = model_tool.run_state_conditioned_forecast(case['timestamp'],'state_conditioned')
    resume_ok = ('__interrupt__' not in resumed and resumed.get('tool_status')=='success'
                 and actual is not None and numeric_core(actual)==numeric_core(ref))
    result = None
    report_ok = False
    report_issues = []
    if resume_ok:
        state = dict(user_query=supplied,forecast_status='success',forecast_result=resumed,
                     forecast_thread_id=thread,needs_human_input=False)
        # The adapter lacks a resume entrypoint; exercise direct Report only.
        result = build_report_node(reports)(state)
        report_text = str(result.get('report_result',''))
        report_ok,report_issues = table_audit(report_text,ref)
        (directory/'FR503_report.txt').write_text(report_text,encoding='utf-8')
    save_json(directory/'FR503_tool_evidence.json',dict(calls=calls,tools=tools))
    save_json(directory/'FR503_states.json',dict(paused=paused,resumed=resumed,direct_report=result))
    dump_observed(observed,directory/'FR503_messages.json')
    (directory/'FR503_instruction.txt').write_text('\n\n'.join(reports.instructions),encoding='utf-8')
    return dict(id='FR503',evaluation_type='real_agent_resume_with_controlled_clarification',
        clarification= supplied,clarification_source='automated_test_input',
        real_human_approval_occurred=False,outer_workflow_resume_tested=False,
        interruption_correct=pause_ok,prediction_calls_before_resume=before_calls,
        resumed_tool_values_match=resume_ok,direct_report_table_passed=report_ok,
        report_issues=report_issues,manual_review_status='pending',
        automatic_passed=bool(pause_ok and resume_ok and report_ok))


async def main(preflight_only=False):
    print('Production file:',model_tool.PRODUCTION_FILE,flush=True)
    print('Input exists:',model_tool.PRODUCTION_FILE.is_file(),flush=True)
    if not model_tool.PRODUCTION_FILE.is_file():
        raise FileNotFoundError(f'Prediction input missing: {model_tool.PRODUCTION_FILE}')
    hashes = frozen_hashes()
    cases = ready_cases()
    check_files(hashes)
    print('Reference preflight passed; eligible cases:',json.dumps(cases,ensure_ascii=False),flush=True)
    if preflight_only:
        return
    if not os.getenv('LLM_API_KEY') or os.getenv('LLM_MODEL')!='glm-5.2':
        raise RuntimeError('Terminal LLM_API_KEY and LLM_MODEL=glm-5.2 are required.')
    directory = ROOT/'tests/system_eval/results'/('forecast_report_integration_v1_'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    directory.mkdir(parents=True,exist_ok=False)
    save_json(directory/'manifest.json',dict(file_sha256=hashes,llm_model=os.getenv('LLM_MODEL'),
        real_prediction_tool_used=True,reference_type='same CPU prediction implementation called directly',
        evaluates_predictive_accuracy_against_actual_demand=False,
        report_prompt=REPORT_PROMPT.name,raw_tool_payload_missing_from_current_agent_state=True,
        production_workflow_modified=False))
    save_json(directory/'input_cases_snapshot.json',cases)
    prompt = load_yaml(REPORT_PROMPT)['system_prompt']
    records=[]
    for case in cases:
        print('\nRunning',case['id'],case['variant'],case['timestamp'],flush=True)
        try:
            record = await real_chain(case,prompt,directory)
        except Exception as exc:
            record=dict(case,evaluation_type='real_forecast_report_graph',automatic_passed=False,
                        error=f'{type(exc).__name__}: {exc}',manual_review_status='not_evaluable')
        records.append(record);save_json(directory/'case_results.json',records)
        print('PASS' if record['automatic_passed'] else 'FAIL',
              '| Tool match:',record.get('tool_routing_and_values_match'),
              '| Forwarding:',record.get('state_forwarding_correct'),flush=True)
        if record.get('error'):print('Error:',record['error'],flush=True)
        check_files(hashes)
    print('\nRunning FR503: missing-time interruption and controlled clarification',flush=True)
    try:
        hitl=await hitl_probe(cases[0],prompt,directory)
    except Exception as exc:
        hitl=dict(id='FR503',automatic_passed=False,error=f'{type(exc).__name__}: {exc}',manual_review_status='not_evaluable')
    records.append(hitl);save_json(directory/'case_results.json',records)
    check_files(hashes)
    summary=dict(evaluation_id='forecast_report_integration_v1',real_success_chain_cases=2,
        real_success_chain_automatic_passed=sum(r['automatic_passed'] for r in records[:2]),
        controlled_clarification_resume_passed=hitl['automatic_passed'],
        pending_full_text_reviews=sum(r.get('manual_review_status')=='pending' for r in records),
        verified_full_text_factual_accuracy=None,
        note='Same-tool reference checks routing/value transport, not forecasting accuracy. '
             'Requested table-format checks may fail on different presentation; inspect full text. '
             'FR503 uses the real inner Agent resume and then a direct Report call; '
             'outer-workflow automatic resume is not implemented or tested here.')
    save_json(directory/'summary.json',summary)
    print('\n=== SUMMARY ===\n'+json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
    print('Results:',directory,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--preflight-only',action='store_true')
    args=parser.parse_args()
    asyncio.run(main(args.preflight_only))
