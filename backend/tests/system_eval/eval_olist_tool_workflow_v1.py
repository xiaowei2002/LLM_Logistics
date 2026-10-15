"""Real LangGraph Olist chain. Preflight uses a labelled deterministic report stub.
Normal mode invokes real one-hop SQL/forecast tool calling and constrained LLM layout planning.
"""
from __future__ import annotations
import argparse
import csv
import json
import sys
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from core.graph.olist_workflow import build_olist_workflow,expected_report_row,report_table_matches
from core.tools.olist_weekly_sql import sha
from core.tools.olist_weekly_forecast import forecast_olist_weekly
from core.agents.olist_tool_caller import OlistToolCaller,ControlledToolModel
from core.graph.olist_structured_report import DEFAULT_PLAN,real_report_plan

PROMPT_SHA = 'da29d4c1a48c3bfa175a14dd7204f2f3571028d429752da2521f6fb9e096ad8b'
TRACE = ['request','cleaning','sql','forecast','report']


def dump(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str,allow_nan=False),encoding='utf-8')


def preflight_report(instruction):
    return json.dumps(DEFAULT_PLAN,ensure_ascii=False)


def make_real_report():
    return real_report_plan


def manifest():
    root = Path(__file__).resolve().parents[2]
    names = ['core/agents/olist_tool_caller.py','core/graph/olist_structured_report.py','core/graph/olist_report_contract.py','core/graph/olist_workflow.py','core/graph/olist_preparation.py',
        'core/tools/olist_weekly_sql.py','core/tools/olist_weekly_forecast.py',
        'tests/system_eval/eval_olist_forecast_v1.py','tests/system_eval/eval_olist_tool_workflow_v1.py',
        'core/agents/report_agent.py','core/prompts/report_agent_v31.yaml']
    return {name:sha(root/name) for name in names if (root/name).is_file()}


def verify_success(state,reference_weekly_dir):
    forecast = state.get('forecast_result',{})
    # Reference comes from saved weekly CSVs, not the graph's SQL rows.
    observations = []
    for split in ['train','validation','test']:
        with (Path(reference_weekly_dir)/f'weekly_{split}.csv').open(encoding='utf-8-sig',newline='') as file:
            for r in csv.DictReader(file):
                if r['category_key']==state['category_key'] and r['week_start']<state['origin_week']:
                    observations.append(dict(category_key=r['category_key'],week_start=r['week_start'],observed_item_count=int(r['observed_item_count'])))
    observations.sort(key=lambda r:r['week_start'])
    expected = forecast_olist_weekly(observations,state['category_key'],state['origin_week'],state['model'],2)
    return dict(route=state.get('execution_trace')==TRACE,status=state.get('status')=='completed',
        history_rows_match=state.get('sql_result',{}).get('history_rows')==observations,
        forecast_reference_match=forecast==expected,
        structured_payload_forwarded=state.get('report_evidence',{}).get('forecast')==forecast,
        source_version_bound=state.get('preparation',{}).get('facts_sha256')==state.get('expected_facts_sha256'),
        no_future_history=all(r['week_start']<state['origin_week'] for r in state.get('sql_result',{}).get('history_rows',[])),
        report_requested_table=report_table_matches(state.get('report_result',''),expected))


def dirty_fixture(folder):
    folder.mkdir()
    data = {
      'olist_orders_dataset.csv':(["order_id","order_status","order_purchase_timestamp"], [['o','delivered','2017-01-02 12:00:00']]),
      'olist_products_dataset.csv':(['product_id','product_category_name'],[['p','cama_mesa_banho']]),
      'olist_order_items_dataset.csv':(['order_id','order_item_id','product_id','price','freight_value'],[['o','1','p','1','0'],['o','1','p','1','0']])}
    for name,(columns,rows) in data.items():
        with (folder/name).open('x',encoding='utf-8',newline='') as file:
            writer=csv.writer(file);writer.writerow(columns);writer.writerows(rows)


def run_evaluation(source_dir,weekly_dir,selection_dir,out,preflight):
    out=Path(out).resolve()
    inputs=[Path(p).resolve() for p in [source_dir,weekly_dir,selection_dir]]
    if out.exists() or any(out==p or p in out.parents for p in inputs):
        raise ValueError('Use a new evaluation output directory outside all inputs')
    out.mkdir(parents=True)
    before=manifest()
    backend_report=preflight_report if preflight else make_real_report()
    calls=[]
    def reporter(instruction):
        calls.append(instruction)
        return backend_report(instruction)
    caller=OlistToolCaller(ControlledToolModel(),controlled=True) if preflight else OlistToolCaller()
    graph=build_olist_workflow(reporter,InMemorySaver(),report_mode='structured_plan',tool_caller=caller)
    base=dict(source_dir=str(Path(source_dir).resolve()),weekly_dir=str(Path(weekly_dir).resolve()),
              selection_dir=str(Path(selection_dir).resolve()),status='running',execution_trace=[])
    cases=[]
    for case,category,origin in [('OW701','cama_mesa_banho','2018-05-21'),('OW702','esporte_lazer','2018-05-28')]:
        print('Running',case,flush=True)
        cfg={'configurable':{'thread_id':str(uuid4())}}
        call_before=len(calls)
        state=graph.invoke(dict(base,category_key=category,origin_week=origin,run_dir=str(out/case)),cfg)
        checks=verify_success(state,weekly_dir) if state.get('status')=='completed' else dict(status=False)
        checks['report_adapter_calls']=len(calls)-call_before==1
        checks['sql_function_call']=state.get('sql_tool_call',{}).get('arguments_validated') is True
        checks['forecast_function_call']=state.get('forecast_tool_call',{}).get('arguments_validated') is True
        cases.append(dict(case_id=case,checks=checks,automatic_passed=all(checks.values()),state=state,manual_review_status='not_applicable_stub' if preflight else 'pending'))
        dump(out/f'{case}.json',cases[-1])
        print(case,'PASS' if all(checks.values()) else 'FAIL',flush=True)
    print('Running OW703: native missing-origin pause/resume (controlled input)',flush=True)
    cfg={'configurable':{'thread_id':str(uuid4())}}
    call_before=len(calls)
    initial=dict(base,category_key='moveis_decoracao',run_dir=str(out/'OW703'))
    paused=graph.invoke(initial,cfg)
    paused_ok=bool(paused.get('__interrupt__')) and len(calls)==call_before and not (out/'OW703').exists()
    state=graph.invoke(Command(resume={'origin_week':'2018-05-21'}),cfg) if paused_ok else paused
    checks=verify_success(state,weekly_dir) if state.get('status')=='completed' else dict(status=False)
    checks['sql_function_call']=state.get('sql_tool_call',{}).get('arguments_validated') is True
    checks['forecast_function_call']=state.get('forecast_tool_call',{}).get('arguments_validated') is True
    checks['paused_before_side_effects']=paused_ok
    checks['report_adapter_calls']=len(calls)-call_before==1
    controlled_resume=dict(case_id='OW703',checks=checks,automatic_passed=all(checks.values()),
        clarification_source='automated evaluation input, not authentic human participation',state=state,
        manual_review_status='not_applicable_stub' if preflight else 'pending')
    dump(out/'OW703.json',controlled_resume)
    print('OW703','PASS' if all(checks.values()) else 'FAIL',flush=True)
    dirty_fixture(out/'duplicate_source')
    failures=[]
    for case,override,trace in [
        ('OW704',dict(category_key='__UNKNOWN_CATEGORY__'),['request']),
        ('OW705',dict(origin_week='2018-05-22'),['request']),
        ('OW706',dict(source_dir=str(out/'duplicate_source')),['request','cleaning'])]:
        call_before=len(calls)
        state=graph.invoke(dict(base,category_key='cama_mesa_banho',origin_week='2018-05-21',run_dir=str(out/case),**{}) | override,
            {'configurable':{'thread_id':str(uuid4())}})
        ok=state.get('status')=='failed' and state.get('execution_trace')==trace and len(calls)==call_before
        failures.append(dict(case_id=case,automatic_passed=ok,state=state))
        dump(out/f'{case}.json',failures[-1])
        print(case,'PASS' if ok else 'FAIL',flush=True)
    after=manifest()
    result=dict(evaluation_id='olist_tool_workflow_v1',mode='controlled_models_preflight' if preflight else 'real_llm_tools_and_layout',
        real_langgraph_used=True,real_file_preparation_used=True,real_sqlite_used=True,real_forecast_tool_used=True,
        real_report_agent_used=False,real_llm_layout_planner_used=not preflight,real_legacy_cleaning_agent_used=False,real_legacy_sql_agent_used=False,
        real_legacy_forecast_agent_used=False,real_llm_tool_calling_tested=not preflight,controlled_tool_model_used=preflight,
        real_success_cases=2,real_success_automatic_passed=sum(r['automatic_passed'] for r in cases),
        controlled_clarification_resume_passed=controlled_resume['automatic_passed'],
        controlled_failure_probes=3,controlled_failure_probes_passed=sum(r['automatic_passed'] for r in failures),
        report_adapter_calls=len(calls),real_report_generate_calls=0,real_layout_llm_calls=0 if preflight else len(calls),
        tool_model_invocations=len(caller.events),real_tool_llm_calls=0 if preflight else len(caller.events),
        sql_tool_executions=sum(r['stage']=='sql_history' and r['tool_executions']==1 for r in caller.events),
        forecast_tool_executions=sum(r['stage']=='forecast' and r['tool_executions']==1 for r in caller.events),
        total_real_llm_calls=0 if preflight else len(caller.events)+len(calls),
        pending_full_text_reviews=0 if preflight else len(calls),verified_full_text_factual_accuracy=None,
        code_unchanged=before==after,file_sha256=before,
        runtime=dict(python=sys.version.split()[0],langgraph=version('langgraph'),pandas=version('pandas')),
        note='Bounded Olist function calling and actual graph integration. Workflow supplies structured category/origin/model; this does not test unrestricted NL understanding, arbitrary SQL generation, autonomous model selection or legacy Agents. Tool outputs forwarded structurally; LLM report planner only controls layout. Three controlled probes/automatic clarification are not authentic human approval. Reuses existing periods; default app entry unchanged.')
    dump(out/'tool_call_audit.json',caller.events)
    dump(out/'summary.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    print('Results:',out)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir',required=True,type=Path)
    parser.add_argument('--weekly-dir',required=True,type=Path)
    parser.add_argument('--selection-dir',required=True,type=Path)
    parser.add_argument('--output-dir',type=Path)
    parser.add_argument('--preflight-only',action='store_true')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[2]
    out=args.output_dir or root/'tests'/'system_eval'/'results'/('olist_tool_workflow_v1_'+('preflight_' if args.preflight_only else 'real_')+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    result=run_evaluation(args.source_dir,args.weekly_dir,args.selection_dir,out,args.preflight_only)
    if result['real_success_automatic_passed']!=2 or not result['controlled_clarification_resume_passed'] or result['controlled_failure_probes_passed']!=3 or not result['code_unchanged']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
