"""Tracked full-length render; run after the separate short-clip acceptance."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / 'results/camera-video/t1-4k-20fps-20260919'
STATUS = Path(__file__).with_name('full-render-status.json')
COMMAND = [sys.executable, str(ROOT / 'scripts/camera_video.py'), 'build', '--output', str(OUTPUT),
           '--resolution', '4k', '--fps', '20', '--samples', '32', '--exposure-samples', '8',
           '--exposure-s', '.001', '--device', 'metal']


def save(record):
    temporary = STATUS.with_suffix('.tmp')
    temporary.write_text(json.dumps(record, indent=2) + '\n')
    temporary.replace(STATUS)


def main():
    def terminated(_number, _frame):
        raise KeyboardInterrupt('Render process group was terminated')
    signal.signal(signal.SIGTERM, terminated)
    start = time.monotonic()
    record = {'status': 'running', 'pid': os.getpid(), 'process_group_id': os.getpgrp(),
              'started_utc': datetime.now(timezone.utc).isoformat(), 'command': COMMAND,
              'output': str(OUTPUT), 'expected_frames': 1200, 'fps': 20,
              'simulation_start_s': 117, 'simulation_end_s': 177,
              'source_status': 'REVIEW_ONLY', 'completed_output_not_yet_available': True}
    save(record)
    try:
        result = subprocess.run(COMMAND, cwd=ROOT, check=False)
        record.update(exit_code=result.returncode, status='complete' if result.returncode == 0 else 'failed',
                      completed_output_not_yet_available=result.returncode != 0)
    except BaseException as error:
        record.update(status='interrupted', error=repr(error))
        raise
    finally:
        record.update(elapsed_seconds=time.monotonic() - start,
                      last_update_utc=datetime.now(timezone.utc).isoformat())
        save(record)
    return record.get('exit_code', 1)


if __name__ == '__main__':
    raise SystemExit(main())
