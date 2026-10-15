"""CLI for a new isolated Olist SQLite / weekly dataset artifact."""
import argparse
import json
from datetime import datetime
from pathlib import Path
from core.tools.olist_weekly_sql import build_weekly_dataset, sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preparation-dir', required=True, type=Path)
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    backend = Path(__file__).resolve().parents[2]
    out = args.output_dir or backend/'tests'/'system_eval'/'results'/('olist_weekly_sql_v1_'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    paths = [backend/'core'/'tools'/'olist_weekly_sql.py', Path(__file__).resolve()]
    before = {str(p.relative_to(backend)):sha(p) for p in paths}
    result = build_weekly_dataset(args.preparation_dir, out)
    after = {str(p.relative_to(backend)):sha(p) for p in paths}
    if before != after:
        raise RuntimeError('Code changed during execution')
    (out/'code_manifest.json').write_text(json.dumps({'file_sha256':before}, indent=2), encoding='utf-8')
    print(json.dumps({k:result[k] for k in ['status','snapshot_rows','independent_sql_reference_match','selected_categories','weeks_per_category','split_rows','source_unchanged']}, ensure_ascii=False, indent=2))
    print('Results:', out.resolve())


if __name__ == '__main__':
    main()
