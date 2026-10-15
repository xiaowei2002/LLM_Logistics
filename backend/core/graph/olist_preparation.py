"""Olist-specific deterministic validation and zero-deletion preparation.
No database/LLM calls. Copies verified source bytes to a new directory, then
builds item facts from those copies, preserving the item grain and raw values.
"""
from __future__ import annotations
import csv
import hashlib
import io
import json
import math
import shutil
import tempfile
from pathlib import Path
import pandas as pd

FILES = {'orders':'olist_orders_dataset.csv',
         'order_items':'olist_order_items_dataset.csv',
         'products':'olist_products_dataset.csv'}
REQUIRED = {'orders':['order_id','order_status','order_purchase_timestamp'],
            'order_items':['order_id','order_item_id','product_id','price','freight_value'],
            'products':['product_id','product_category_name']}
KEYS = {'orders':['order_id'], 'order_items':['order_id','order_item_id'],
        'products':['product_id']}
UNKNOWN_CATEGORY = '__UNKNOWN_CATEGORY__'


class OlistPreparationError(ValueError):
    pass


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> pd.DataFrame:
    raw = path.read_bytes()
    reader = csv.reader(io.StringIO(raw.decode('utf-8-sig'), newline=''))
    header = next(reader, None)
    if not header or len(header) != len(set(header)):
        raise OlistPreparationError(f'{path.name}: missing/duplicate CSV headers')
    for record, row in enumerate(reader, 2):
        if len(row) != len(header):
            raise OlistPreparationError(f'{path.name}: ragged CSV record {record}')
    return pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=False).fillna('')


def inspect_olist_tables(paths: dict[str, Path]) -> dict:
    frames = {name:_read(Path(paths[name])) for name in FILES}
    errors, warnings, tables = [], [], {}
    for name, data in frames.items():
        missing = sorted(set(REQUIRED[name])-set(data.columns))
        if missing:
            errors.append(f'{name}: missing columns {missing}')
            continue
        key = KEYS[name]
        blank = int(data[key].apply(lambda s:s.str.strip().eq('')).any(axis=1).sum())
        repeated = int(data.duplicated(key).sum())
        duplicates = int(data.duplicated().sum())
        tables[name] = dict(rows=len(data), columns=len(data.columns), blank_key_rows=blank,
            duplicate_key_excess_rows=repeated, exact_duplicate_excess_rows=duplicates)
        if data.empty or blank or repeated:
            errors.append(f'{name}: empty table, blank key or repeated key; no automatic deletion')
    if errors:
        return dict(passed=False, errors=errors, warnings=warnings, tables=tables)
    orders, items, products = (frames[n] for n in ['orders','order_items','products'])
    purchase = pd.to_datetime(orders['order_purchase_timestamp'],
                             format='%Y-%m-%d %H:%M:%S', errors='coerce')
    if purchase.isna().any():
        errors.append('orders: missing or invalid purchase timestamp')
    for column in ['order_approved_at','order_delivered_carrier_date',
                   'order_delivered_customer_date','order_estimated_delivery_date']:
        if column not in orders:
            continue
        parsed = pd.to_datetime(orders[column],format='%Y-%m-%d %H:%M:%S',errors='coerce')
        missing = int(orders[column].str.strip().eq('').sum())
        invalid = int((parsed.isna() & orders[column].str.strip().ne('')).sum())
        before = int(parsed.lt(purchase).sum())
        if missing or invalid or before:
            warnings.append(dict(type='optional_order_timestamp', column=column,
                missing=missing, invalid_nonblank=invalid, before_purchase=before,
                action='retain_original; not used to filter forecast records'))
    for column in ['price','freight_value']:
        value = pd.to_numeric(items[column], errors='coerce')
        if value.isna().any() or not value.map(math.isfinite).all() or value.lt(0).any():
            errors.append(f'order_items: invalid/nonfinite/negative {column}; no row deletion')
    if not items['order_item_id'].str.fullmatch(r'[1-9][0-9]*').all():
        errors.append('order_items: item position must be a positive integer string')
    orphan_orders = int((~items.order_id.isin(orders.order_id)).sum())
    orphan_products = int((~items.product_id.isin(products.product_id)).sum())
    if orphan_orders or orphan_products:
        errors.append('order_items: unmatched order/product IDs')
    unknown = products.product_category_name.str.strip().eq('')
    if products.product_category_name.eq(UNKNOWN_CATEGORY).any():
        errors.append('products: source category collides with reserved unknown marker')
    if unknown.any():
        warnings.append(dict(type='unknown_category', products=int(unknown.sum()),
            item_records=int(items.product_id.isin(products.loc[unknown,'product_id']).sum()),
            action='retain raw category; add separate derived marker'))
    no_items = int((~orders.order_id.isin(items.order_id)).sum())
    if no_items:
        warnings.append(dict(type='orders_without_items', orders=no_items,
            action='retain orders; do not invent item demand'))
    return dict(passed=not errors, errors=errors, warnings=warnings, tables=tables,
                orphan_item_orders=orphan_orders, orphan_item_products=orphan_products,
                purchase_min=str(purchase.min()), purchase_max=str(purchase.max()))


def _resolve(source: Path, canonical: str) -> Path:
    direct = source/canonical
    if direct.is_file():
        return direct
    candidates = list(source.glob(Path(canonical).stem+'*.csv'))
    if len(candidates) != 1:
        raise OlistPreparationError(f'Expected exactly one source for {canonical}; found {len(candidates)}')
    return candidates[0]


def prepare_olist_snapshot(source_dir: str | Path, output_dir: str | Path) -> dict:
    source, output = Path(source_dir).resolve(), Path(output_dir).resolve()
    if not source.is_dir():
        raise OlistPreparationError('Source directory does not exist')
    if output == source or output.is_relative_to(source):
        raise OlistPreparationError('Output must be outside the source directory')
    if output.exists():
        raise FileExistsError(f'Refusing to overwrite existing output: {output}')
    paths = {name:_resolve(source,file) for name,file in FILES.items()}
    original_hashes = {name:sha(path) for name,path in paths.items()}
    gate = inspect_olist_tables(paths)
    if not gate['passed']:
        raise OlistPreparationError(json.dumps(gate,ensure_ascii=False))
    output.parent.mkdir(parents=True,exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.olist_prepare_',dir=output.parent))
    try:
        clean = stage/'cleaned'; clean.mkdir()
        cleaned_paths = {}
        for name,path in paths.items():
            target=clean/FILES[name]
            shutil.copyfile(path,target)
            if sha(target) != original_hashes[name]:
                raise OlistPreparationError('Source changed before/during snapshot copy')
            cleaned_paths[name]=target
        # Re-read the saved copies: downstream preparation is bound to these bytes.
        saved_gate = inspect_olist_tables(cleaned_paths)
        if saved_gate != gate:
            raise OlistPreparationError('Saved clean snapshots do not reproduce source gate')
        orders,items,products = (_read(cleaned_paths[name]) for name in ['orders','order_items','products'])
        facts = items.merge(orders[REQUIRED['orders']],on='order_id',how='left',
            validate='many_to_one',sort=False).merge(products[REQUIRED['products']],
            on='product_id',how='left',validate='many_to_one',sort=False)
        if len(facts) != len(items) or not facts[KEYS['order_items']].equals(items[KEYS['order_items']]):
            raise OlistPreparationError('Join altered item grain or item order')
        purchase = pd.to_datetime(facts.order_purchase_timestamp,format='%Y-%m-%d %H:%M:%S')
        missing = facts.product_category_name.str.strip().eq('')
        facts['category_missing'] = missing.astype(int)
        facts['category_key'] = facts.product_category_name.where(~missing,UNKNOWN_CATEGORY)
        facts['week_start'] = (purchase.dt.normalize()-pd.to_timedelta(purchase.dt.dayofweek,unit='D')).dt.strftime('%Y-%m-%d')
        facts['item_count'] = 1
        columns = ['order_id','order_item_id','product_id','order_status',
            'order_purchase_timestamp','product_category_name','category_key',
            'category_missing','week_start','item_count','price','freight_value']
        facts=facts[columns]
        target=stage/'order_item_facts.csv'
        facts.to_csv(target,index=False,encoding='utf-8-sig',lineterminator='\n')
        reread=_read(target)
        expected=facts.astype(str)
        if not reread.equals(expected):
            raise OlistPreparationError('Saved facts do not exactly match prepared rows')
        if any(sha(path)!=original_hashes[name] for name,path in paths.items()):
            raise OlistPreparationError('An original source file changed during preparation')
        result=dict(preparation_version='olist_preparation_v1',status='success',
            execution_type='deterministic_olist_validation_and_zero_deletion',
            real_cleaning_agent_used=False, real_sql_used=False, real_forecast_used=False,
            report_generated=False, source_files={n:str(p) for n,p in paths.items()},
            source_sha256=original_hashes, source_unchanged=True, deleted_rows=0,
            cleaning_gate=gate, cleaned_gate=saved_gate,
            cleaned_files={n:str(output/'cleaned'/FILES[n]) for n in FILES},
            cleaned_sha256={n:sha(p) for n,p in cleaned_paths.items()},
            facts_file=str(output/'order_item_facts.csv'), facts_sha256=sha(target),
            facts_rows=len(facts), facts_columns=columns, item_count_sum=int(facts.item_count.sum()),
            unknown_category_item_records=int(missing.sum()), join_preserved_item_grain=True,
            filtering_by_final_order_status=False,
            demand_definition='Observed order-item record count by purchase week/category; not inventory or latent demand',
            note='Zero-deletion source copies are byte-identical. Derived item_count=1 counts records; order_item_id is a position, not quantity. No model or database run.')
        (stage/'preparation_summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        # Rename only to a new destination; retain files for downstream evidence.
        if output.exists():
            raise FileExistsError(output)
        stage.rename(output)
        return result
    finally:
        if stage.exists():
            shutil.rmtree(stage)
