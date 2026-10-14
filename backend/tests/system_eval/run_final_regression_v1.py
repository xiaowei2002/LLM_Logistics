"""Run existing offline unit/regression modules; no real integration suites.
From backend: python -m tests.system_eval.run_final_regression_v1
Creates a new evidence directory; preserves historical evaluation results.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

MODULES = [
    'test_sql_readonly_guard', 'test_sql_readonly_guard_v32',
    'test_sql_mcp_guard', 'test_sql_mcp_error_text_v23',
    'test_sql_mcp_protocol_audit_v22', 'test_sql_evidence',
    'test_sql_evidence_audit_v21', 'test_sql_agent_evidence',
    'test_sql_agent_guard_integration', 'test_sql_node',
    'test_sql_workflow_risk_v2', 'test_cleaning_precheck',
    'test_cleaning_approval', 'test_cleaning_executor',
    'test_cleaning_precheck_workflow', 'test_cleaning_safe_resume',
    'test_cleaning_node', 'test_forecast_tool_evidence', 'test_forecast_node',
    'test_report_node', 'test_report_node_v3', 'test_multi_agent_workflow',
]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--only', nargs='+', choices=MODULES,
                        help='Optional subset for local diagnosis; default runs all modules.')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    modules = args.only or MODULES
    missing = [m for m in modules if not (root/'tests'/f'{m}.py').is_file()]
    if missing:
        raise FileNotFoundError('Missing existing test modules: '+', '.join(missing))
    folder = root/'tests/system_eval/results'/('final_regression_v1_'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    folder.mkdir(parents=True, exist_ok=False)
    paths = []
    for part in ['agents', 'graph', 'prompts', 'tools', 'utils']:
        paths += [p for p in (root/'core'/part).rglob('*') if p.is_file()
                  and p.suffix in ['.py','.yaml','.yml'] and '__pycache__' not in p.parts]
    paths += [root/'tests'/f'{m}.py' for m in modules]
    paths += [Path(__file__)]
    hashes = {str(p.relative_to(root)):sha(p) for p in paths}
    versions = {}
    for name in ['torch','langchain','langgraph','pandas','numpy','scikit-learn','sqlglot']:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    write_json(folder/'manifest.json', dict(python=platform.python_version(),
        executable=sys.executable, packages=versions, file_sha256=hashes,
        selected_modules=modules, complete_requested_suite=args.only is None,
        real_integration_tests_selected=False, historical_evaluations_rerun=False))
    env = os.environ.copy()
    env.update(RUN_INTEGRATION_TESTS='0', PYTHON_DOTENV_DISABLED='1',
               PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    records = []
    for module in modules:
        print('Running tests.'+module, flush=True)
        started = datetime.now()
        command = [sys.executable,'-m','unittest','tests.'+module,'-v']
        try:
            result = subprocess.run(command, cwd=root, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, encoding='utf-8', errors='replace', timeout=60)
            output, code = result.stdout, result.returncode
        except subprocess.TimeoutExpired as exc:
            output = exc.stdout or ''
            if isinstance(output, bytes):
                output = output.decode('utf-8','replace')
            output += '\nRunner timeout after 60 seconds. No pass recorded.\n'
            code = 124
        (folder/(module+'.log')).write_text(output, encoding='utf-8')
        count = re.search(r'Ran (\d+) tests?', output)
        changed = [rel for rel,h in hashes.items() if sha(root/rel) != h]
        record = dict(module=module, return_code=code, passed=code==0 and not changed,
            reported_test_count=int(count.group(1)) if count else None,
            elapsed_seconds=(datetime.now()-started).total_seconds(),
            changed_files=changed, log_file=module+'.log')
        records.append(record)
        write_json(folder/'module_results.json', records)
        print(('PASS' if record['passed'] else 'FAIL')+' | tests='+str(record['reported_test_count']), flush=True)
        if changed:
            break
    summary = dict(evaluation_id='final_regression_v1',
        complete_requested_suite=args.only is None and len(records)==len(MODULES),
        completed_modules=len(records), passed_modules=sum(r['passed'] for r in records),
        all_selected_modules_passed=len(records)==len(modules) and all(r['passed'] for r in records),
        reported_total_test_cases=sum(r['reported_test_count'] or 0 for r in records),
        note='Existing offline regression tests. Does not measure real LLM/DB/model accuracy. '
        'A passing module count is not an end-to-end task accuracy. See logs for individual failures/skips.')
    write_json(folder/'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print('Results:',folder)
    if not summary['all_selected_modules_passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
