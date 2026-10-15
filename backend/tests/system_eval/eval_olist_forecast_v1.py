"""Two-stage weekly forecast baseline evaluation: select, then frozen test.
Selection never opens weekly_test.csv or the SQLite database.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
import pandas as pd
from core.tools.olist_weekly_forecast import MODELS, forecast_olist_weekly
from core.tools.olist_weekly_sql import START, TRAIN_END, VAL_END, END, sha

BOUNDARIES = dict(start_inclusive=START, train_end_exclusive=TRAIN_END,
                  validation_end_exclusive=VAL_END, test_end_exclusive=END)
PROTOCOL = dict(version='olist_forecast_v1', models=list(MODELS), horizon_weeks=2,
    selection='per-category minimum validation MAE across both horizons',
    tie_break='candidate order in models; exact numeric ties only',
    validation_origins=11, test_origins=13,
    rolling='weekly expanding history; prior actual observations become visible after their week completes',
    moving_average='same last-k-actual mean for both forecast horizons; no recursive updates',
    time_boundaries=BOUNDARIES,
    scope='Five training-selected high-volume categories; not all-category or cross-dataset validation')
INPUT_NAMES = ['weekly_summary.json','selected_categories.csv','weekly_train.csv','weekly_validation.csv']


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def code_hashes():
    backend = Path(__file__).resolve().parents[2]
    return {str(p.relative_to(backend)).replace('\\','/'):sha(p) for p in [
        backend/'core'/'tools'/'olist_weekly_forecast.py',
        backend/'core'/'tools'/'olist_weekly_sql.py', Path(__file__).resolve()]}


def fingerprint(weekly_dir, names):
    return {name:sha(Path(weekly_dir)/name) for name in names}


def load_metadata(weekly_dir):
    root = Path(weekly_dir)
    summary = json.loads((root/'weekly_summary.json').read_text(encoding='utf-8-sig'))
    if summary.get('evaluation_id') != 'olist_weekly_sql_v1' or summary.get('status') != 'success' or summary.get('independent_sql_reference_match') is not True or summary.get('category_selection_uses_training_only') is not True:
        raise ValueError('Expected successful audited weekly preparation')
    if summary.get('time_boundaries') != BOUNDARIES:
        raise ValueError('Unexpected time boundaries')
    if sha(root/'selected_categories.csv') != summary['output_sha256']['selected_categories.csv']:
        raise ValueError('Category file hash mismatch')
    cats = pd.read_csv(root/'selected_categories.csv', dtype=str, keep_default_na=False)
    if list(cats.columns) != ['category_key','train_item_count'] or len(cats) != 5 or cats.category_key.duplicated().any() or cats.category_key.isin(['','__UNKNOWN_CATEGORY__']).any():
        raise ValueError('Invalid selected categories')
    rows = [{'category_key':r.category_key,'train_item_count':int(r.train_item_count)} for r in cats.itertuples()]
    if rows != summary['selected_categories']:
        raise ValueError('Category metadata mismatch')
    return summary, rows


def load_split(root, name, summary, categories):
    path = Path(root)/f'weekly_{name}.csv'
    if sha(path) != summary['output_sha256'][path.name]:
        raise ValueError(f'{name} split hash mismatch')
    data = pd.read_csv(path, dtype=str, keep_default_na=False)
    if list(data.columns) != ['category_key','week_start','observed_item_count','split']:
        raise ValueError('Invalid weekly schema')
    low, high = {'train':(START,TRAIN_END),'validation':(TRAIN_END,VAL_END),'test':(VAL_END,END)}[name]
    weeks = list(pd.date_range(low, high, freq='7D', inclusive='left').strftime('%Y-%m-%d'))
    expected = {(cat['category_key'],w) for cat in categories for w in weeks}
    if data.duplicated(['category_key','week_start']).any() or set(zip(data.category_key,data.week_start)) != expected or len(data) != len(expected) or not data.split.eq(name).all():
        raise ValueError('Missing, duplicate, mixed-category or out-of-range weekly rows')
    if not data.observed_item_count.str.fullmatch(r'\d+').all():
        raise ValueError('Invalid observed weekly count')
    data['observed_item_count'] = data.observed_item_count.astype('int64')
    if name == 'train':
        totals = data.groupby('category_key').observed_item_count.sum().to_dict()
        if any(int(totals[r['category_key']]) != r['train_item_count'] for r in categories):
            raise ValueError('Training totals differ from category selection evidence')
    return data.sort_values(['category_key','week_start']).reset_index(drop=True)


def score(rows):
    if not rows:
        raise ValueError('No forecast predictions')
    errors = [abs(float(r['prediction'])-float(r['actual'])) for r in rows]
    denominator = sum(abs(float(r['actual'])) for r in rows)
    return dict(predictions=len(rows), MAE=sum(errors)/len(errors),
                RMSE=math.sqrt(sum(v*v for v in errors)/len(errors)),
                WMAPE_percent=100*sum(errors)/denominator if denominator else None,
                WMAPE_denominator=denominator,
                note='WMAPE denominator zero returns null; overlapping horizon-target pairs counted separately')


def rolling_rows(data, low, high, models):
    records = []
    for category in sorted(data.category_key.unique()):
        history = data[data.category_key.eq(category)].sort_values('week_start').to_dict('records')
        for i, row in enumerate(history):
            if not low <= row['week_start'] < high or i+1 >= len(history) or history[i+1]['week_start'] >= high:
                continue
            # Pass only earlier rows to the forecasting API, never target labels.
            for model in models:
                result = forecast_olist_weekly(history[:i], category, row['week_start'], model, 2)
                for target in result['forecasts']:
                    actual = history[i+target['horizon']-1]
                    if actual['week_start'] != target['week_start']:
                        raise ValueError('Target alignment failed')
                    records.append(dict(category_key=category, model=model, origin_week=row['week_start'],
                        history_last_week=result['history_last_week'], horizon=target['horizon'],
                        target_week=target['week_start'], prediction=target['predicted_item_count'],
                        actual=actual['observed_item_count']))
    return records


def metrics_table(rows):
    result = []
    for model in MODELS:
        selected = [r for r in rows if r['model'] == model]
        if selected:
            result.append(dict(model=model, **score(selected)))
    return result


def staged_output(output_dir, input_dirs):
    out = Path(output_dir).resolve()
    if out.exists() or any(out == Path(d).resolve() or Path(d).resolve() in out.parents for d in input_dirs):
        raise ValueError('Use a new output directory outside input directories')
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='olist_forecast_', dir=out.parent))
    return out, stage


def select_models(weekly_dir, output_dir):
    before, codes = fingerprint(weekly_dir, INPUT_NAMES), code_hashes()
    summary, categories = load_metadata(weekly_dir)
    train = load_split(weekly_dir,'train',summary,categories)
    validation = load_split(weekly_dir,'validation',summary,categories)
    rows = rolling_rows(pd.concat([train,validation], ignore_index=True), TRAIN_END, VAL_END, MODELS)
    if len(rows) != 5*11*2*len(MODELS):
        raise ValueError('Unexpected validation prediction coverage')
    chosen, category_scores = {}, []
    for cat in [r['category_key'] for r in categories]:
        evaluated = []
        for model in MODELS:
            stats = score([r for r in rows if r['category_key'] == cat and r['model'] == model])
            evaluated.append(dict(category_key=cat, model=model, **stats))
        best = min(range(len(MODELS)), key=lambda i:evaluated[i]['MAE'])
        chosen[cat] = MODELS[best]
        category_scores.extend(evaluated)
    if fingerprint(weekly_dir, INPUT_NAMES) != before or code_hashes() != codes:
        raise ValueError('Input/code changed during selection')
    out, stage = staged_output(output_dir,[weekly_dir])
    try:
        pd.DataFrame(rows).to_csv(stage/'validation_predictions.csv', index=False, encoding='utf-8-sig', lineterminator='\n')
        dump(stage/'validation_metrics.json', dict(by_model=metrics_table(rows),by_category=category_scores))
        selection = dict(evaluation_id='olist_forecast_v1_selection', protocol=PROTOCOL,
            chosen_models=chosen, input_sha256=before, code_sha256=codes,
            selection_uses_test_labels=False, test_file_opened_by_selection=False,
            selection_artifact_sha256={n:sha(stage/n) for n in ['validation_predictions.csv','validation_metrics.json']})
        dump(stage/'selection.json',selection)
        dump(stage/'selection_summary.json',dict(status='success', chosen_models=chosen,
            validation_origins_per_category=11, validation_predictions_per_model=110,
            selection_uses_test_labels=False, test_file_opened_by_selection=False,
            selection_sha256=sha(stage/'selection.json'), real_llm_used=False))
        stage.rename(out)
        return json.loads((out/'selection_summary.json').read_text())
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def evaluate_test(weekly_dir, selection_dir, output_dir):
    selection_dir = Path(selection_dir)
    selection_hash = sha(selection_dir/'selection.json')
    frozen = json.loads((selection_dir/'selection.json').read_text())
    if frozen.get('evaluation_id') != 'olist_forecast_v1_selection' or frozen.get('protocol') != PROTOCOL or frozen.get('code_sha256') != code_hashes():
        raise ValueError('Frozen protocol or code mismatch; do not retune on test')
    if fingerprint(weekly_dir, INPUT_NAMES) != frozen['input_sha256']:
        raise ValueError('Selection inputs changed')
    for name, checksum in frozen['selection_artifact_sha256'].items():
        if name not in ('validation_predictions.csv','validation_metrics.json') or sha(selection_dir/name) != checksum:
            raise ValueError('Selection artifact changed')
    summary, categories = load_metadata(weekly_dir)
    chosen = frozen['chosen_models']
    if set(chosen) != {r['category_key'] for r in categories} or any(m not in MODELS for m in chosen.values()):
        raise ValueError('Invalid frozen model choices')
    # Independently recompute choice from saved validation predictions (no test label used).
    saved = pd.read_csv(selection_dir/'validation_predictions.csv').to_dict('records')
    for cat, model in chosen.items():
        expected = min(MODELS, key=lambda m:score([r for r in saved if r['category_key']==cat and r['model']==m])['MAE'])
        if model != expected:
            raise ValueError('Frozen model choice differs from validation scores')
    train = load_split(weekly_dir,'train',summary,categories)
    validation = load_split(weekly_dir,'validation',summary,categories)
    # Test labels are first opened only after frozen choices and provenance checks.
    test = load_split(weekly_dir,'test',summary,categories)
    test_hash = sha(Path(weekly_dir)/'weekly_test.csv')
    rows = rolling_rows(pd.concat([train,validation,test], ignore_index=True), VAL_END, END, MODELS)
    if len(rows) != 5*13*2*len(MODELS):
        raise ValueError('Unexpected test prediction coverage')
    selected = [r for r in rows if r['model'] == chosen[r['category_key']]]
    if len(selected) != 130 or any(r['history_last_week'] >= r['origin_week'] or r['target_week'] < r['origin_week'] for r in rows):
        raise ValueError('Temporal alignment failed')
    if sha(selection_dir/'selection.json') != selection_hash or fingerprint(weekly_dir, INPUT_NAMES) != frozen['input_sha256'] or sha(Path(weekly_dir)/'weekly_test.csv') != test_hash or code_hashes() != frozen['code_sha256']:
        raise ValueError('Input/code changed during test evaluation')
    out, stage = staged_output(output_dir,[weekly_dir,selection_dir])
    try:
        pd.DataFrame(rows).to_csv(stage/'test_predictions_all_baselines.csv', index=False, encoding='utf-8-sig', lineterminator='\n')
        pd.DataFrame(selected).to_csv(stage/'test_predictions_frozen_selection.csv', index=False, encoding='utf-8-sig', lineterminator='\n')
        metrics = dict(all_baselines=metrics_table(rows), frozen_selection=score(selected),
            frozen_selection_by_horizon=[dict(horizon=h,**score([r for r in selected if r['horizon']==h])) for h in [1,2]],
            frozen_selection_by_category=[dict(category_key=c,**score([r for r in selected if r['category_key']==c])) for c in chosen])
        dump(stage/'test_metrics.json',metrics)
        result = dict(evaluation_id='olist_forecast_v1_test', status='success',
            protocol=PROTOCOL, chosen_models=chosen, test_origins_per_category=13,
            test_predictions_per_model=130, frozen_selection_predictions=130,
            selection_sha256=selection_hash, input_sha256={**frozen['input_sha256'],'weekly_test.csv':test_hash},
            code_sha256=frozen['code_sha256'], metrics=metrics,
            all_forecast_history_ends_before_origin=True, target_alignment_checked=True,
            real_forecast_baselines_used=True, real_llm_used=False,
            real_sql_agent_used=False, real_report_used=False, real_langgraph_used=False,
            source_unchanged=True,
            output_sha256={p.name:sha(p) for p in stage.iterdir() if p.is_file()},
            limitations=['Observed item counts, not latent demand, stock or probability intervals',
                        'Overlapping two-week forecasts; forecast-target pairs are not independent samples',
                        'Rolling test permits previous completed test weeks as history; not one fixed 14-week forecast',
                        'Baseline comparison supports pipeline checks; no claim of algorithmic novelty or universal accuracy',
                        'Independent reference metrics and tool correctness need review; does not measure Agent routing or report factuality'])
        dump(stage/'forecast_summary.json',result)
        stage.rename(out)
        return result
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',required=True,choices=['select','test'])
    parser.add_argument('--weekly-dir',required=True,type=Path)
    parser.add_argument('--selection-dir',type=Path)
    parser.add_argument('--output-dir',type=Path)
    args = parser.parse_args()
    backend = Path(__file__).resolve().parents[2]
    out = args.output_dir or backend/'tests'/'system_eval'/'results'/('olist_forecast_v1_'+args.stage+'_'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    if args.stage == 'select':
        result = select_models(args.weekly_dir,out)
    else:
        if args.selection_dir is None:
            parser.error('--selection-dir is required for test')
        result = evaluate_test(args.weekly_dir,args.selection_dir,out)
    print(json.dumps({k:result[k] for k in result if k in ['status','chosen_models','selection_uses_test_labels','test_file_opened_by_selection','validation_predictions_per_model','test_predictions_per_model','metrics']}, ensure_ascii=False, indent=2))
    print('Results:',out.resolve())


if __name__ == '__main__':
    main()
