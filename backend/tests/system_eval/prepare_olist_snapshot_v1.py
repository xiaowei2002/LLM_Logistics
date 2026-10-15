"""Run Olist preparation only; no database, Agent or model calls."""
from __future__ import annotations
import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from core.graph.olist_preparation import prepare_olist_snapshot


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source-dir',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[2]
    output=args.output_dir or root/'tests/system_eval/results'/('olist_preparation_v1_'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    tracked=[Path(__file__),root/'core/graph/olist_preparation.py']
    hashes={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in tracked}
    result=prepare_olist_snapshot(args.source_dir,output)
    if any(hashlib.sha256(p.read_bytes()).hexdigest()!=hashes[str(p.relative_to(root))] for p in tracked):
        raise RuntimeError('Preparation code changed during execution')
    (output/'code_manifest.json').write_text(json.dumps({'file_sha256':hashes},indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:result[k] for k in ['status','deleted_rows','source_unchanged',
        'facts_rows','item_count_sum','unknown_category_item_records','join_preserved_item_grain']},ensure_ascii=False,indent=2))
    print('Warnings:',json.dumps(result['cleaning_gate']['warnings'],ensure_ascii=False))
    print('Results:',output.resolve())


if __name__=='__main__':
    main()
