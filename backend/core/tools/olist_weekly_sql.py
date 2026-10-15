"""Versioned Olist SQLite snapshot and parameterized weekly count queries.
This is deterministic SQL infrastructure, not an LLM SQLAgent evaluation.
"""
from __future__ import annotations
from contextlib import closing
import hashlib
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path
import pandas as pd

START = '2017-01-02'
TRAIN_END = '2018-02-26'
VAL_END = '2018-05-21'
END = '2018-08-27'
TOP_K = 5
UNKNOWN = '__UNKNOWN_CATEGORY__'
TOP_SQL = '''SELECT category_key, SUM(item_count) AS train_item_count
FROM order_item_facts
WHERE week_start >= ? AND week_start < ? AND category_key <> ?
GROUP BY category_key
ORDER BY train_item_count DESC, category_key ASC
LIMIT ?'''
WEEK_SQL = '''SELECT category_key, week_start, SUM(item_count) AS observed_item_count
FROM order_item_facts
WHERE week_start >= ? AND week_start < ?
GROUP BY category_key, week_start
ORDER BY category_key ASC, week_start ASC'''
REQUIRED = ['order_id', 'order_item_id', 'product_id', 'order_status',
            'order_purchase_timestamp', 'product_category_name', 'category_key',
            'category_missing', 'week_start', 'item_count', 'price', 'freight_value']


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_facts(path):
    data = pd.read_csv(path, dtype=str, keep_default_na=False)
    if list(data.columns) != REQUIRED or data.empty:
        raise ValueError('Invalid or empty facts schema')
    if data[['order_id', 'order_item_id']].eq('').any().any() or data.duplicated(['order_id', 'order_item_id']).any():
        raise ValueError('Invalid item keys')
    if not data.item_count.eq('1').all():
        raise ValueError('item_count must be exactly 1 per observed item record')
    t = pd.to_datetime(data.order_purchase_timestamp, format='%Y-%m-%d %H:%M:%S', errors='raise')
    expected = (t - pd.to_timedelta(t.dt.dayofweek, unit='D')).dt.strftime('%Y-%m-%d')
    if not data.week_start.eq(expected).all():
        raise ValueError('week_start is inconsistent with purchase timestamp')
    missing = data.product_category_name.eq('')
    if not data.category_key.eq(data.product_category_name.where(~missing, UNKNOWN)).all() or not data.category_missing.eq(missing.astype(int).astype(str)).all():
        raise ValueError('Category markers inconsistent')
    return data


def open_readonly(db):
    """OS read-only URI, query_only, and default-deny SQLite authorizer."""
    con = sqlite3.connect(Path(db).resolve().as_uri() + '?mode=ro', uri=True)
    con.execute('PRAGMA query_only=ON')
    def authorize(action, arg1, arg2, database, trigger):
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ and database == 'main' and arg1 == 'order_item_facts' and arg2 in REQUIRED:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION and str(arg2).lower() in {'sum', 'count'}:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    con.set_authorizer(authorize)
    return con


def query(con, sql, params):
    # Only reviewed templates can run here; category/date values are bound.
    if sql not in (TOP_SQL, WEEK_SQL):
        raise ValueError('Only fixed Olist query templates are supported')
    cursor = con.execute(sql, params)
    return [dict(zip([c[0] for c in cursor.description], row)) for row in cursor.fetchall()]


def build_weekly_dataset(preparation_dir, output_dir):
    prep, out = Path(preparation_dir).resolve(), Path(output_dir).resolve()
    if out.exists() or out == prep or prep in out.parents:
        raise ValueError('Use a new output directory outside the preparation directory')
    summary_path, facts_path = prep/'preparation_summary.json', prep/'order_item_facts.csv'
    summary_hash = sha(summary_path)
    source = json.loads(summary_path.read_text(encoding='utf-8-sig'))
    if source.get('preparation_version') != 'olist_preparation_v1' or source.get('status') != 'success' or source.get('join_preserved_item_grain') is not True:
        raise ValueError('Expected successful Olist preparation V1 evidence')
    facts_hash = sha(facts_path)
    if facts_hash != source.get('facts_sha256'):
        raise ValueError('Facts hash differs from preparation manifest')
    data = read_facts(facts_path)
    if len(data) != source.get('facts_rows') or len(data) != source.get('item_count_sum'):
        raise ValueError('Manifest counts differ from facts')
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='olist_weekly_', dir=out.parent))
    try:
        db = stage/'olist_snapshot.sqlite3'
        with closing(sqlite3.connect(db)) as con:
            loaded = data.copy()
            loaded['item_count'] = loaded.item_count.astype('int64')
            loaded.to_sql('order_item_facts', con, index=False, if_exists='fail')
            con.execute('CREATE UNIQUE INDEX item_key ON order_item_facts(order_id, order_item_id)')
            con.commit()
        con = open_readonly(db)
        try:
            selected = query(con, TOP_SQL, (START, TRAIN_END, UNKNOWN, TOP_K))
            weekly = query(con, WEEK_SQL, (START, END))
        finally:
            con.close()
        if len(selected) != TOP_K:
            raise ValueError('Fewer than five eligible training categories')
        # Independent pandas reference for SQL selection and every aggregate.
        window = data[data.week_start.ge(START) & data.week_start.lt(END)]
        train = window[window.week_start.lt(TRAIN_END) & window.category_key.ne(UNKNOWN)]
        counts = train.groupby('category_key').size().reset_index(name='train_item_count')
        expected_top = counts.sort_values(['train_item_count','category_key'], ascending=[False,True]).head(TOP_K).to_dict('records')
        expected_week = window.groupby(['category_key','week_start']).size().reset_index(name='observed_item_count').sort_values(['category_key','week_start']).to_dict('records')
        if selected != expected_top or weekly != expected_week:
            raise ValueError('SQL / independent reference mismatch')
        weeks = pd.date_range(START, END, freq='7D', inclusive='left').strftime('%Y-%m-%d')
        grid = pd.MultiIndex.from_product([[r['category_key'] for r in selected], weeks], names=['category_key','week_start']).to_frame(index=False)
        full = grid.merge(pd.DataFrame(weekly), on=['category_key','week_start'], how='left', validate='one_to_one')
        full['observed_item_count'] = full.observed_item_count.fillna(0).astype('int64')
        full['split'] = full.week_start.map(lambda w: 'train' if w < TRAIN_END else 'validation' if w < VAL_END else 'test')
        for split in ['train','validation','test']:
            full[full.split.eq(split)].to_csv(stage/f'weekly_{split}.csv', index=False, encoding='utf-8-sig', lineterminator='\n')
        pd.DataFrame(selected).to_csv(stage/'selected_categories.csv', index=False, encoding='utf-8-sig', lineterminator='\n')
        evidence = {'selection':{'sql':TOP_SQL,'parameters':[START,TRAIN_END,UNKNOWN,TOP_K],'rows':selected},
                    'weekly':{'sql':WEEK_SQL,'parameters':[START,END],'rows':weekly}}
        (stage/'sql_evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
        if sha(facts_path) != facts_hash or sha(summary_path) != summary_hash:
            raise ValueError('Input changed during execution')
        result = dict(evaluation_id='olist_weekly_sql_v1', status='success',
            execution_type='deterministic_fixed_SQL_and_weekly_preparation', real_sql_used=True,
            sql_engine='SQLite', real_llm_used=False, real_sql_agent_used=False,
            real_forecast_used=False, real_report_used=False,
            preparation_summary_sha256=summary_hash, facts_sha256=facts_hash,
            source_unchanged=True, snapshot_rows=len(data),
            independent_sql_reference_match=True, selected_categories=selected,
            category_selection_uses_training_only=True,
            time_boundaries={'start_inclusive':START,'train_end_exclusive':TRAIN_END,
                             'validation_end_exclusive':VAL_END,'test_end_exclusive':END},
            weeks_per_category={'train':60,'validation':12,'test':14},
            series_rows=len(full), split_rows={k:int(v) for k,v in full.groupby('split').size().items()},
            observed_items_in_window=len(window), unknown_items_in_window=int(window.category_key.eq(UNKNOWN).sum()),
            output_sha256={p.name:sha(p) for p in sorted(stage.iterdir()) if p.is_file()},
            demand_definition='Observed order-item record count, not sales revenue or latent demand',
            zero_semantics='Zero means no observed records for that category/week in this snapshot',
            limitations=['Fixed source window, not proof of complete real-world capture',
                        'Category mapping comes from supplied product metadata; as-of history unavailable',
                        'Full SQLite snapshot and test file contain held-out data; forecasting must receive only rows earlier than its cutoff',
                        'No forecast accuracy or Agent/workflow success measured in this step'])
        (stage/'weekly_summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        stage.rename(out)
        return result
    finally:
        if stage.exists():
            shutil.rmtree(stage)
