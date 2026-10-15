"""Offline recheck of existing V1.1 report replay files. No LLM or data reruns."""
import argparse
import json
from datetime import datetime
from pathlib import Path
from core.graph.olist_report_contract import review_olist_report,MARKER
from core.graph.olist_workflow import report_table_matches
from core.tools.olist_weekly_sql import sha


def recheck(results_dir,out_dir):
    source,out=Path(results_dir).resolve(),Path(out_dir).resolve()
    if out.exists() or out==source or source in out.parents:
        raise ValueError('Use a new output directory outside existing results')
    paths=[source/'summary.json']+[source/f'OW70{i}.json' for i in [1,2,3]]
    before={p.name:sha(p) for p in paths}
    prior=json.loads(paths[0].read_text(encoding='utf-8-sig'))
    if prior.get('evaluation_id')!='olist_report_v11_replay' or prior.get('mode')!='regenerate':
        raise ValueError('Expected saved V1.1 regenerated reports')
    rows=[]
    for path in paths[1:]:
        case=json.loads(path.read_text(encoding='utf-8-sig'))
        evidence=case['report_evidence']
        if json.loads(case['report_instruction'].partition(MARKER)[2])!=evidence:
            raise ValueError('Report input evidence mismatch')
        review=review_olist_report(case['report_result'],evidence)
        row=dict(case_id=case['case_id'],source_sha256=before[path.name],
            previous_automatic_passed=case['automatic_passed'],
            report_table_passed=report_table_matches(case['report_result'],evidence['forecast']),
            review=review,report_result=case['report_result'],report_evidence=evidence)
        row['heuristic_automatic_passed']=row['report_table_passed'] and review['heuristic_checks_passed']
        rows.append(row)
    if before!={p.name:sha(p) for p in paths}:
        raise ValueError('Original results changed')
    out.mkdir(parents=True)
    for row in rows:
        (out/f"{row['case_id']}.json").write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf-8')
        print(row['case_id'],'table:',row['report_table_passed'],'review flags:',[f['type'] for f in row['review']['boolean_and_scope_review_flags']])
    result=dict(evaluation_id='olist_report_v12_offline_recheck',report_generate_calls=0,
        original_results_unchanged=True,input_sha256=before,
        report_tables_passed=sum(r['report_table_passed'] for r in rows),
        reports_without_review_flags=sum(r['heuristic_automatic_passed'] for r in rows),
        full_text_review_required=True,verified_full_text_factual_accuracy=None,
        note='Offline reevaluation of the same reports after correcting heuristic false positives and false negatives. Not new generation or proof of model improvement.')
    (out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2));print('Results:',out)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results-dir',required=True,type=Path)
    parser.add_argument('--output-dir',type=Path)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[2]
    out=args.output_dir or root/'tests'/'system_eval'/'results'/('olist_report_v12_recheck_'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    recheck(args.results_dir,out)


if __name__=='__main__':main()
