"""One discriminating nonzero Forecast -> Report integration supplement.
Run: python -m tests.system_eval.eval_forecast_report_integration_v11
Reuses V1 helpers and frozen production components. Does not rerun FR503.
"""
from __future__ import annotations
import asyncio
import hashlib
import itertools
import json
from datetime import datetime
from pathlib import Path
import os

os.environ['PYTHON_DOTENV_DISABLED']='1'

from tests.system_eval import eval_forecast_report_integration_v1 as base


def select_case():
    tool = base.model_tool
    tool._ensure_cache()
    metadata = tool._CACHE['metadata']
    n = len(metadata)
    if n < 20:
        raise ValueError('Insufficient eligible forecast samples.')
    # Restrict selection to the actual final 15% sample block.
    heldout_start = int(n * tool.TRAIN_RATIO) + int(n * tool.VAL_RATIO)
    for timestamp in reversed(metadata['history_end'].iloc[heldout_start:].tolist()):
        stamp = str(timestamp)
        ref = tool.run_state_conditioned_forecast(stamp,'state_conditioned')
        if ref.get('status') != 'success':
            continue
        h1 = ref['forecast']['t+1']['material_demand']
        h2 = ref['forecast']['t+2']['material_demand']
        peak = ref['two_hour_peak_demand']
        available = [m for m in tool.MATERIALS
                     if float(h1[m]) > 0 and float(h2[m]) > 0]
        for pair in itertools.combinations(available,2):
            a,b = pair
            # Require material-value discrimination at both horizons and
            # horizon discrimination for at least one requested material.
            if (abs(float(h1[a])-float(h1[b])) <= 0.001 or
                abs(float(h2[a])-float(h2[b])) <= 0.001 or
                all(abs(float(h1[m])-float(h2[m])) <= 0.001 for m in pair)):
                continue
            query = (f'预测基准时刻为 {stamp}。请调用 demand_forecast，明确使用 '
                f'state_conditioned 模型，只整理 {a}、{b} 的 t+1、t+2 需求及 '
                'two_hour_peak_demand。以 Markdown 表格输出，列顺序为物料、t+1、t+2、'
                '两小时峰值，每物料一行，保留工具原值至四位小数；'
                '不要自行计算，不增加其他业务事实。')
            inspection = base.inspect_forecast_query(query)
            if not inspection['ready']:
                raise ValueError(f'Complete test question failed input inspection: {inspection}')
            return dict(id='FR504',timestamp=stamp,variant='state_conditioned',
                        materials=list(pair),question=query),ref,heldout_start
    raise RuntimeError('No nonzero, material-distinct and horizon-distinct pair in the holdout block; no LLM call made.')


async def main(preflight_only=False):
    if not base.model_tool.PRODUCTION_FILE.is_file():
        raise FileNotFoundError(base.model_tool.PRODUCTION_FILE)
    hashes = base.frozen_hashes()
    expected_frozen = {
        base.ROOT/'core/agents/demand_forecast_agent.py':
            '4b48ae281943317856bdac4cb91ecfea1e1497955c605d06b7168f31e0b6c937',
        base.ROOT/'core/graph/nodes/forecast_node.py':
            '00bdbf802901bbd7c49c5781f4464a8bfb6de1126ecb5c6edbb2d27f038ae005',
        base.ROOT/'core/graph/nodes/report_node_v3.py':
            '1fc33b531fddd9ec3635b7e8a938f1e9052489b0013c00affb9116790a859715',
        base.ROOT/'core/tools/demand_forecast/state_conditioned_forecast_tool.py':
            '0714d9df673b2338a8fc6215a265bcc598e4e067f82f54d655293b22741922a9',
        base.model_tool.PRODUCTION_FILE:
            '52146549e096d5cdcab2a11aa1eef374ed0a2f0a99294ec2178d9be2af193d5e',
    }
    for path, expected in expected_frozen.items():
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError(f'Component differs from V1 run: {path}; verify contents before continuing.')
    source = Path(__file__).resolve()
    hashes[str(source)] = hashlib.sha256(source.read_bytes()).hexdigest()
    case,reference,start = select_case()
    base.check_files(hashes)
    print('Discriminating case:',json.dumps(case,ensure_ascii=False),flush=True)
    print('Reference values:',json.dumps({m:{'t+1':reference['forecast']['t+1']['material_demand'][m],
        't+2':reference['forecast']['t+2']['material_demand'][m],
        'peak':reference['two_hour_peak_demand'][m]} for m in case['materials']},ensure_ascii=False),flush=True)
    if preflight_only:
        return
    if not os.getenv('LLM_API_KEY') or os.getenv('LLM_MODEL')!='glm-5.2':
        raise RuntimeError('Terminal LLM_API_KEY and LLM_MODEL=glm-5.2 required.')
    directory = base.ROOT/'tests/system_eval/results'/('forecast_report_integration_v11_'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    directory.mkdir(parents=True,exist_ok=False)
    base.save_json(directory/'input_cases_snapshot.json',[case])
    base.save_json(directory/'manifest.json',dict(file_sha256=hashes,
        llm_model=os.getenv('LLM_MODEL'),selected_materials=case['materials'],
        selection='reference-shape coverage only; no future actual demand or errors used',
        heldout_start_sample_position=start,prediction_accuracy_evaluated=False,
        production_components_modified=False,old_v1_cases_rerun=False))
    # Change only the evaluation helper's requested material set. The real
    # Agent, tool, node, graph and Report prompt remain unchanged.
    previous_materials = base.MATERIALS
    base.MATERIALS = tuple(case['materials'])
    try:
        record = await base.real_chain(case,base.load_yaml(base.REPORT_PROMPT)['system_prompt'],directory)
    except Exception as exc:
        record = dict(case,evaluation_type='real_forecast_report_graph',automatic_passed=False,
                      error=f'{type(exc).__name__}: {exc}',manual_review_status='not_evaluable')
    finally:
        base.MATERIALS = previous_materials
    base.save_json(directory/'case_results.json',[record])
    base.check_files(hashes)
    summary = dict(evaluation_id='forecast_report_integration_v11_nonzero',
        total_real_cases=1,automatic_passed_cases=int(record['automatic_passed']),
        selected_materials=case['materials'],selected_timestamp=case['timestamp'],
        pending_full_text_reviews=int(record.get('manual_review_status')=='pending'),
        verified_full_text_factual_accuracy=None,
        note='Coverage supplement after all-zero V1 report values. One real chain, no HITL rerun. '
             'Reference is the same model implementation, not a predictive-accuracy ground truth.')
    base.save_json(directory/'summary.json',summary)
    print(json.dumps(record,ensure_ascii=False,indent=2,default=str),flush=True)
    print('\n=== SUMMARY ===\n'+json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
    print('Results:',directory,flush=True)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--preflight-only',action='store_true')
    args=parser.parse_args()
    asyncio.run(main(args.preflight_only))
