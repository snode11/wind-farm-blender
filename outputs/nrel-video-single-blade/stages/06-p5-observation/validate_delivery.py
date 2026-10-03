"""Read-only P5 delivery verification; never fits models or recomputes scores."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import re
import sys
from urllib.parse import unquote

_REPOSITORY = next(path for path in Path(__file__).resolve().parents
                   if (path / "pyproject.toml").is_file() and (path / "wfrl/nrel_reconstruction").is_dir())
if str(_REPOSITORY) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY))
from wfrl.nrel_reconstruction.artifact_paths import frozen_path, relocated_path, repository_root

BASE = Path(__file__).resolve().parent
ROOT = repository_root(__file__)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    return json.loads(path.read_text())


def verify(mapping, base):
    changed = []
    resolved_sources = {}
    preserved_snapshots = []
    for name, value in mapping.items():
        expected = value if isinstance(value, str) else value['sha256']
        requested = base / name
        try:
            path = frozen_path(requested, expected, root=ROOT)
        except RuntimeError:
            changed.append(str(relocated_path(requested, root=ROOT)))
            continue
        resolved_sources[name] = str(path)
        if path != relocated_path(requested, root=ROOT):
            preserved_snapshots.append(name)
        if isinstance(value, dict) and 'bytes' in value and path.stat().st_size != value['bytes']:
            changed.append(str(path))
    return {'count': len(mapping), 'changed': changed,
            'resolved_sources': resolved_sources, 'preserved_snapshot_keys': preserved_snapshots}


def historical_file_set():
    parent = ROOT / 'outputs/nrel-video-single-blade'
    roots = [relocated_path(parent / p, root=ROOT) for p in ('20261002-first/formal', '20261002-second',
              '20261003-diagnostics', '20261003-p3-center-scale', '20261003-p4-greville')]
    files = {p for root in roots for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    files.update(p for p in (ROOT / 'docs/proposal').glob('NREL5MW*') if p.is_file())
    files.update(p for p in (parent / 'review').glob('*-review.zip') if p.is_file() and not p.name.startswith('20261003-p5-'))
    return {str(p.relative_to(ROOT)) for p in files}


def main():
    original = load(BASE / 'historical_before.json')['files']
    current = historical_file_set()
    history = verify(original, ROOT)
    relocated_original = {str(relocated_path(name, root=ROOT).relative_to(ROOT)) for name in original}
    history.update({'added': sorted(current - relocated_original), 'removed': sorted(relocated_original - current)})
    freeze = load(BASE / 'fit/model_freeze.json')
    tests = load(BASE / 'test_validation.json')
    implementation = load(BASE / 'implementation/manifest.json')['files']
    eval_record = load(BASE / 'evaluation-only/evaluation_record.json')
    checks = {'history': history, 'fit_outputs': verify(freeze['sha256'], BASE / 'fit'),
        'fit_entry_inputs': verify(load(BASE / 'fit/fit_entry_inputs.json'), Path('/')),
        'fit_code': verify(load(BASE / 'fit/implementation_at_launch.json'), Path('/')),
        'tested_source_and_tests': verify(tests['sha256'], ROOT),
        'snapshot': verify(implementation, BASE / 'implementation'),
        'evaluated_files': verify(eval_record['protected_signatures_after'], Path('/'))}
    missing_links = []
    for md in BASE.rglob('*.md'):
        if 'implementation' in md.parts:
            continue
        for value in re.findall(r'\]\(([^)]+)\)', md.read_text()):
            if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:', value) or value.startswith('#'):
                continue
            target = unquote(value.strip('<>').split('#')[0])
            resolved = (md.parent / target).resolve()
            if resolved == BASE / 'validation.json':
                continue
            if target and not resolved.exists():
                missing_links.append({'document': str(md.relative_to(BASE)), 'target': value})
    fit_summary = load(BASE / 'fit/fit_summary.json')
    controls = relocated_path(ROOT / 'outputs/nrel-video-single-blade/20261002-second/reconstruction', root=ROOT)
    identity = {name: (BASE / 'fit/original_raw' / name).read_bytes() == (controls / name).read_bytes()
                for name in ('initial_template.ply', 'prior_only.ply', 'T1_B1.ply')}
    budgets = {b: {'steps': row['steps'], 'image_forward_backward_evaluations': row['image_forward_backward_evaluations']}
               for b, row in fit_summary['branches'].items()}
    pass_checks = (all(not c['changed'] for c in checks.values()) and not history['added'] and not history['removed']
        and not missing_links and all(identity.values()) and tests['exit_code'] == 0 and tests['passed'] == 92
        and eval_record['status'] == 'COMPLETE_SIGNATURES_UNCHANGED' and eval_record['signatures_unchanged'] is True
        and all(row == {'steps': 100, 'image_forward_backward_evaluations': 600} for row in budgets.values())
        and all(eval_record['original_raw_historical_scores_exactly_equal'].values()))
    result = {'utc': datetime.now(timezone.utc).isoformat(), 'status': 'PASS' if pass_checks else 'FAILED',
        'checks': checks, 'baseline_ply_byte_equal': identity, 'budgets': budgets,
        'tests_passed': tests['passed'], 'evaluation_status': eval_record['status'],
        'missing_local_links': missing_links, 'final_candidate_adopted': False,
        'formal_result_retained': 'stages/02-second-run/reconstruction',
        'historical_verification_note': 'Frozen signatures are checked against the relocated files or explicitly listed preserved snapshots. Snapshot matches do not establish byte identity of current live source or edited documentation.',
        'experiment_complete': True, 'geometry_acceptance': 'NOT_YET_ESTABLISHED',
        'engineering_acceptance': 'PENDING_USE_CASE_TARGETS', 'further_experiments_scheduled': False}
    (BASE / 'validation.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k: result[k] for k in ('status', 'tests_passed', 'missing_local_links', 'baseline_ply_byte_equal')}))
    if not pass_checks:
        raise RuntimeError('Delivery verification failed; inspect validation.json')


if __name__ == '__main__':
    main()
