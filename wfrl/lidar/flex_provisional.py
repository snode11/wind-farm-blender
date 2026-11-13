"""Explicit REVIEW_ONLY reader for a visual prototype, never a READY package.

Uses freshly processed physics and B2 statistics. It does not weaken the
published ReplayPackage validator or claim numerical refinement validation.
"""
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

from .replay import ReplayReader, precompute


def checked_bytes(path, digest):
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != digest:
        raise ValueError('Preview integrity mismatch: '+str(path))
    return content


def build(run, destination):
    run, destination = Path(run).resolve(), Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    cfg = json.loads((run/'run_config.json').read_text(encoding='utf-8'))
    status = json.loads((run/'exit_status.json').read_text(encoding='utf-8'))
    if status['exit_code'] != 0 or 'FAST.Farm terminated normally.' not in (run/'solver.log').read_text(encoding='utf-8'):
        raise ValueError('Physical run did not finish')
    for item in cfg['inputs']:
        checked_bytes(run/item['path'], item['sha256'])
    data = json.loads((run/'processed.json').read_text(encoding='utf-8'))
    if data['evaluation_only'] or data['collision_excluded'] is not True:
        raise ValueError('Full physical postprocessing required for this preview')
    replay = dict(threshold_m=7., hysteresis_m=.1, max_hold_s=5., passage_margin=1.25)
    cumulative, statistics = precompute(data['measurements'], data['motion'], replay)
    contents = dict(motion=data['motion'], measurements=data['measurements'], cumulative=cumulative, statistics=statistics)
    manifest = dict(schema='wfrl.flex-review.v1', status='REVIEW_ONLY', source='FAST.Farm',
                    run_id=run.name, turbine_id='T1', source_run=str(run),
                    segment=dict(start_s=cfg['startup_discard_s'], end_s=cfg['duration_s']), replay=replay,
                    calibration=data['calibration'], wind_profile=cfg['wind_profile'],
                    validation='source completion and discrete collision checks only; no refinement comparison',
                    raw_sources={name:hashlib.sha256((run/name).read_bytes()).hexdigest()
                                 for name in ('run_config.json','processed.json','exit_status.json','solver.log','surface_hashes.json')})
    payload=json.dumps(contents,allow_nan=False).encode()
    manifest['data_sha256']=hashlib.sha256(payload).hexdigest()
    manifest_payload=json.dumps(manifest,indent=2,allow_nan=False)
    reader=_reader(manifest,contents)
    destination.mkdir(parents=True)
    (destination/'data.json').write_bytes(payload)
    (destination/'manifest.json').write_text(manifest_payload, encoding='utf-8')
    return reader


def load(path):
    path=Path(path)
    manifest=json.loads((path/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('schema')!='wfrl.flex-review.v1' or manifest.get('status')!='REVIEW_ONLY':
        raise ValueError('Expected an explicitly provisional flexibility preview')
    data=json.loads(checked_bytes(path/'data.json',manifest['data_sha256']))
    return _reader(manifest,data)


def _reader(manifest,data):
    times=[r['time_s'] for r in data['motion']]
    if (not times or any(not math.isfinite(t) for t in times)
            or any(b<=a for a,b in zip(times,times[1:]))
            or times[0]!=manifest['segment']['start_s'] or times[-1]!=manifest['segment']['end_s']):
        raise ValueError('Preview motion must cover its full interval')
    package=SimpleNamespace(manifest=manifest,**data)
    return ReplayReader(package)


def source_reader(path):
    reader=load(path); m=reader.package.manifest; run=Path(m['source_run'])
    for name,digest in m['raw_sources'].items():
        checked_bytes(run/name,digest)
    cfg=json.loads((run/'run_config.json').read_text(encoding='utf-8'))
    hashes=json.loads((run/'surface_hashes.json').read_text(encoding='utf-8'))
    return SimpleNamespace(package=reader.package,run=run,fps=cfg['fps'],section_count=37 if cfg['span_refined'] else 19,
                           times=[r['time_s'] for r in reader.package.motion],hashes={r['path']:r['sha256'] for r in hashes})
