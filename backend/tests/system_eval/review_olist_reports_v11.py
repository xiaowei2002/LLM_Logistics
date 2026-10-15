"""Audit saved Olist reports, optionally regenerate using identical saved evidence.
Does not run Cleaning, SQL, Forecast, LangGraph, or mutate original results.
"""
import argparse
import json
from datetime import datetime
from pathlib import Path
from core.graph.olist_report_contract import strengthen_olist_instruction,review_olist_report,MARKER
from core.graph.olist_workflow import report_table_matches
from core.tools.olist_weekly_sql import sha


def dump(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def replay_reports(results_dir,out_dir,regenerate=False,reporter=None):
    source,out=Path(results_dir).resolve(),Path(out_dir).resolve()
    if out.exists() or out==source or source in out.parents:
        raise ValueError('Use a new output directory outside original results')
    summary_path=source/'summary.json'
    summary=json.loads(summary_path.read_text(encoding='utf-8-sig'))
    if summary.get('evaluation_id')!='olist_workflow_v1' or summary.get('mode')!='real_report':
        raise ValueError('Expected original real-report workflow results')
    sources=[summary_path]+[source/f'OW70{i}.json' for i in [1,2,3]]
    before={p.name:sha(p) for p in sources}
    # Verify all evidence before making any new LLM calls.
    cases=[]
    for path in sources[1:]:
        case=json.loads(path.read_text(encoding='utf-8-sig'));state=case['state']
        instruction=state['report_instruction'];evidence=state['report_evidence']
        if json.loads(instruction.partition(MARKER)[2])!=evidence or evidence['forecast']!=state['forecast_result']:
            raise ValueError('Saved Report input differs from upstream payload')
        cases.append((path,case,state,evidence,instruction))
    backend=Path(__file__).resolve().parents[2]
    names=['core/graph/olist_report_contract.py','core/graph/olist_workflow.py',
           'tests/system_eval/review_olist_reports_v11.py','core/agents/report_agent.py','core/prompts/report_agent_v31.yaml']
    codes={n:sha(backend/n) for n in names if (backend/n).is_file()}
    injected_reporter=reporter is not None
    if regenerate and reporter is None:
        from tests.system_eval.eval_olist_workflow_v1 import make_real_report
        reporter=make_real_report()
    out.mkdir(parents=True)
    rows=[];calls=0
    for path,case,state,evidence,instruction in cases:
        old=review_olist_report(state['report_result'],evidence)
        result=dict(case_id=case['case_id'],source_file_sha256=before[path.name],
                    original_review=old,original_report=state['report_result'],
                    report_evidence=evidence,original_automatic_passed=case.get('automatic_passed'))
        if regenerate:
            new_instruction=strengthen_olist_instruction(instruction)
            result['report_instruction']=new_instruction
            try:
                calls+=1
                text=reporter(new_instruction)
                if not isinstance(text,str) or not text.strip():
                    raise ValueError('Empty report')
                result.update(report_result=text,new_review=review_olist_report(text,evidence),
                    report_table_passed=report_table_matches(text,evidence['forecast']),manual_review_status='pending')
                result['automatic_passed']=result['report_table_passed'] and result['new_review']['heuristic_checks_passed']
            except Exception as exc:
                result.update(error=f'{type(exc).__name__}: {exc}',automatic_passed=False,manual_review_status='generation_failed')
        else:
            result['manual_review_status']='original_report_audit_only'
        rows.append(result)
        dump(out/path.name,result)
        print(result['case_id'],'new automatic:',result.get('automatic_passed','not regenerated'),
              '| original flags:',len(old['boolean_and_scope_review_flags']),flush=True)
    if before!={p.name:sha(p) for p in sources}:
        raise RuntimeError('Original evidence changed during replay')
    if codes!={n:sha(backend/n) for n in codes}:
        raise RuntimeError('Code changed during replay')
    result=dict(evaluation_id='olist_report_v11_replay',mode='regenerate' if regenerate else 'audit_only',
        original_results_unchanged=True,source_sha256=before,file_sha256=codes,
        real_report_agent_used=regenerate and not injected_reporter,
        report_generate_calls=calls,new_reports=sum('report_result' in r for r in rows),
        automatically_passed_reports=sum(r.get('automatic_passed',False) for r in rows),
        original_reports_with_flags=sum(not r['original_review']['heuristic_checks_passed'] for r in rows),
        pending_full_text_reviews=sum('report_result' in r for r in rows),
        cleaning_rerun=False,sql_rerun=False,forecast_rerun=False,langgraph_rerun=False,
        verified_full_text_factual_accuracy=None,
        note='Regression on three saved evidence payloads with new instruction constraints. Heuristic Boolean/scope flags are not exhaustive full-text review. No new workflow or predictive accuracy measured.')
    dump(out/'summary.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2));print('Results:',out)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results-dir',required=True,type=Path)
    parser.add_argument('--output-dir',type=Path)
    parser.add_argument('--regenerate',action='store_true')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[2]
    out=args.output_dir or root/'tests'/'system_eval'/'results'/('olist_report_v11_'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    replay_reports(args.results_dir,out,args.regenerate)


if __name__=='__main__':
    main()
