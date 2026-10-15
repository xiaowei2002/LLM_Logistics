"""Saved-evidence evaluation of constrained layout-plan / deterministic-facts reports.
No Cleaning, SQL, Forecast, or LangGraph reruns. Normal mode calls a new LLM planner.
"""
import argparse
import json
from datetime import datetime
from pathlib import Path
from core.graph.olist_structured_report import (DEFAULT_PLAN,generate_structured_report,real_report_plan)
from core.graph.olist_report_contract import MARKER,review_olist_report
from core.graph.olist_workflow import report_table_matches
from core.tools.olist_weekly_sql import sha


def dump(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def run(results_dir,out_dir,preflight=False):
    source,out=Path(results_dir).resolve(),Path(out_dir).resolve()
    if out.exists() or out==source or source in out.parents:
        raise ValueError('Use a new output directory outside saved inputs')
    paths=[source/'summary.json']+[source/f'OW70{i}.json' for i in [1,2,3]]
    old=json.loads(paths[0].read_text(encoding='utf-8-sig'))
    if old.get('evaluation_id')!='olist_report_v11_replay' or old.get('mode')!='regenerate':
        raise ValueError('Expected saved V1.1 reports/evidence')
    before={p.name:sha(p) for p in paths}
    cases=[]
    for path in paths[1:]:
        case=json.loads(path.read_text(encoding='utf-8-sig'))
        if json.loads(case['report_instruction'].partition(MARKER)[2])!=case['report_evidence']:
            raise ValueError('Saved Report evidence differs from recorded input')
        cases.append((path,case))
    backend=Path(__file__).resolve().parents[2]
    names=['core/graph/olist_structured_report.py','core/graph/olist_workflow.py',
        'core/graph/olist_report_contract.py','tests/system_eval/eval_olist_structured_report_v2.py']
    codes={n:sha(backend/n) for n in names}
    out.mkdir(parents=True)
    rows=[];calls=[]
    def planner(instruction):
        # Preserve rejected raw output too; no retries or silent default fallback.
        try:
            raw=json.dumps(DEFAULT_PLAN,ensure_ascii=False) if preflight else real_report_plan(instruction)
        except Exception as exc:
            calls.append(dict(instruction=instruction,error=f'{type(exc).__name__}: {exc}'))
            raise
        calls.append(dict(instruction=instruction,raw_output=raw))
        return raw
    for path,case in cases:
        evidence=case['report_evidence'];start=len(calls)
        result=dict(case_id=case['case_id'],input_file_sha256=before[path.name],report_evidence=evidence,
                    original_report=case['report_result'],manual_review_status='pending')
        try:
            generated=generate_structured_report(evidence,planner)
            result.update(generated)
            review=review_olist_report(generated['report_result'],evidence)
            checks=dict(plan_validated=True,forecast_table_matches=report_table_matches(generated['report_result'],evidence['forecast']),
                        no_known_boolean_or_scope_flags=review['heuristic_checks_passed'],planner_calls=len(calls)-start==1)
            result.update(checks=checks,automatic_passed=all(checks.values()),review=review)
            (out/f"{case['case_id']}.md").write_text(generated['report_result'],encoding='utf-8')
        except Exception as exc:
            result.update(error=f'{type(exc).__name__}: {exc}',automatic_passed=False,manual_review_status='generation_failed')
        result['planner_attempts']=calls[start:]
        rows.append(result);dump(out/path.name,result)
        print(case['case_id'],'PASS' if result['automatic_passed'] else 'FAIL',flush=True)
    if before!={p.name:sha(p) for p in paths} or codes!={n:sha(backend/n) for n in codes}:
        raise ValueError('Input/code changed during evaluation')
    summary=dict(evaluation_id='olist_structured_report_v2',mode='preflight_layout_stub' if preflight else 'real_llm_layout_planner',
        generation_architecture='LLM selects whitelisted title/section order; code renders all factual content',
        report_cases=3,automatically_passed_reports=sum(r['automatic_passed'] for r in rows),
        planner_attempts=len(calls),real_llm_calls=0 if preflight else len(calls),
        real_existing_ReportAgent_used=False,real_llm_layout_planner_used=not preflight,
        pending_full_text_reviews=sum('report_result' in r for r in rows),source_unchanged=True,
        input_sha256=before,file_sha256=codes,cleaning_rerun=False,sql_rerun=False,forecast_rerun=False,langgraph_rerun=False,
        verified_free_text_llm_factual_accuracy=None,
        note='Different generation architecture from free-text ReportAgent. Schema/template guarantees limit claims to constrained outputs; passing is not evidence of zero LLM hallucination or novel predictive generalization.')
    dump(out/'summary.json',summary);print(json.dumps(summary,ensure_ascii=False,indent=2));print('Results:',out)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results-dir',required=True,type=Path)
    parser.add_argument('--output-dir',type=Path)
    parser.add_argument('--preflight-only',action='store_true')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[2]
    out=args.output_dir or root/'tests'/'system_eval'/'results'/('olist_structured_report_v2_'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    result=run(args.results_dir,out,args.preflight_only)
    if result['automatically_passed_reports']!=3:raise SystemExit(1)


if __name__=='__main__':main()
