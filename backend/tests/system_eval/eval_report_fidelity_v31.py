"""Replay only the unresolved RV202 using V3 node and V3.1 prompt.
Run from backend: python -m tests.system_eval.eval_report_fidelity_v31
"""
from __future__ import annotations
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

os.environ['PYTHON_DOTENV_DISABLED'] = '1'

from core.utils.yaml_loader import load_yaml
from tests.system_eval.eval_report_fidelity_v3 import (
    CapturedReportAgent, boundary_review_flags, build_report_node,
    evaluate_case, save_json,
)

ROOT = Path(__file__).resolve().parents[2]
PROMPT_PATH = ROOT / 'core/prompts/report_agent_v31.yaml'
CASE_PATH = ROOT / 'tests/system_eval/cases/report_fidelity_cases.json'


def main():
    config = json.loads(CASE_PATH.read_text(encoding='utf-8-sig'))
    cases = [c for c in config['cases'] if c['case_id'] == 'RV202']
    if len(cases) != 1:
        raise ValueError('Exactly one RV202 is required.')
    if not os.getenv('LLM_API_KEY') or not os.getenv('LLM_MODEL'):
        raise RuntimeError('Terminal LLM_API_KEY and LLM_MODEL are required.')
    case = cases[0]
    prompt = load_yaml(PROMPT_PATH)['system_prompt']
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError('Empty prompt.')
    paths = [PROMPT_PATH, CASE_PATH, Path(__file__),
             ROOT / 'core/graph/nodes/report_node_v3.py',
             ROOT / 'core/agents/report_agent.py',
             ROOT / 'tests/system_eval/eval_report_fidelity_v3.py',
             ROOT / 'tests/system_eval/eval_report_fidelity.py']
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in paths}
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    result_dir = ROOT / 'tests/system_eval/results' / f'report_fidelity_v31_{stamp}'
    result_dir.mkdir(parents=True, exist_ok=False)
    save_json(result_dir / 'input_cases_snapshot.json', {'cases': cases})
    save_json(result_dir / 'manifest.json', dict(file_sha256=hashes,
        llm_model=os.getenv('LLM_MODEL'), prompt=PROMPT_PATH.name,
        node='report_node_v3', scope='RV202 targeted repair regression'))
    agent = CapturedReportAgent(prompt)
    row, report = evaluate_case(case, build_report_node(agent))
    (result_dir / 'RV202_report.txt').write_text(report, encoding='utf-8')
    (result_dir / 'RV202_instruction.txt').write_text(
        '\n\n'.join(agent.instructions), encoding='utf-8')
    # Exact old failure is probed in addition to original V2 token rules.
    compact = ''.join(report.split())
    known_patterns = ['另外2行', '额外2行', '相差2行', '差了2行', '37.5%']
    known_hits = [p for p in known_patterns if p in compact]
    row['known_failure_hits'] = known_hits
    row['original_rules_passed'] = row['automatic_passed']
    row['automatic_passed'] = row['automatic_passed'] and not known_hits
    row['llm_generate_calls'] = len(agent.instructions)
    row['boundary_review_flags'] = boundary_review_flags(report, case['state'])
    row['manual_review_status'] = 'pending' if report.strip() else 'not_evaluable'
    save_json(result_dir / 'case_results.json', [row])
    consistent = all(hashlib.sha256((ROOT / n).read_bytes()).hexdigest() == h
                     for n, h in hashes.items())
    summary = dict(evaluation_id='report_fidelity_v31_rv202', total_cases=1,
        automatically_passed_cases=int(row['automatic_passed']),
        pending_manual_reviews=int(bool(report.strip())),
        inputs_unchanged_during_run=consistent,
        verified_full_text_factual_accuracy=None,
        note='Targeted regression of a known error; exact phrase probes cannot '
             'establish complete fidelity or generalization. Human full-text review required.')
    save_json(result_dir / 'report_fidelity_summary.json', summary)
    if not consistent:
        raise RuntimeError('Input files changed during the run; results are invalid.')
    print(json.dumps(row, ensure_ascii=False, indent=2))
    print('\n=== REPORT ===\n' + report)
    print('\nResults:', result_dir)


if __name__ == '__main__':
    main()
