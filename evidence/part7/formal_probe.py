"""Bounded actual formal-training JSONL -> Bridge event verification (not GUI)."""
import json
from pathlib import Path
import sys
import time
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from wfrl.blender_bridge.backend_session import BackendSession
out = Path(__file__).resolve().parent
session = BackendSession(mpi_launcher='/opt/homebrew/bin/mpiexec')
report = {'boundary': __doc__, 'events': [], 'passed': False}
options = dict(scene=str(ROOT/'scenes/turb3_ctrl3.yaml'), iters=1, n_steps=4,
               warmup_steps=0, episode_steps=0, seed=0, tag='part7-acceptance')
report['options'] = options
try:
    session.start('formal_training', options)
    deadline = time.monotonic()+300
    while time.monotonic()<deadline:
        if session._progress_dir is not None:
            p=Path(session._progress_dir.name)/'progress.jsonl'
            if p.exists(): (out/'formal-source.jsonl').write_bytes(p.read_bytes())
        report['events'].extend(session.poll())
        if session.status in {'STOPPED','FAILED'} and not session.alive(): break
        time.sleep(.05)
    stats=[p for k,p in report['events'] if k=='training_stats' and p['record_kind']=='iteration_stats']
    assert session.status=='STOPPED' and not session.alive(), session.status
    assert stats and stats[-1]['stats']['value_loss']['validity']=='valid', stats
    report['passed']=True
except Exception as exc:
    report['error']=f'{type(exc).__name__}: {exc}'
finally:
    if session.alive(): session.stop(180)
    if session._stop_worker: session._stop_worker.join(185)
    report.update(final_status=session.status,backend_alive=session.alive(),run_id=session.run_id)
    (out/'formal-stats.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
print({k:v for k,v in report.items() if k not in {'events','options'}})
raise SystemExit(0 if report['passed'] else 1)
