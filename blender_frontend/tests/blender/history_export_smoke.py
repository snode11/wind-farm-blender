"""Native smoke for bounded history export through the visible operator."""
import json
import sys
import tempfile
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import wfrl_blender
from wfrl_blender import charts


def main():
    wfrl_blender.register()
    try:
        bpy.ops.wfrl.load_demo()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'history.json'
            operator = bpy.ops.wfrl.export_history
            result = operator(filepath=str(path))
            assert result == {'FINISHED'}
            charts.export_job.thread.join(5)
            assert path.exists()
            assert json.loads(path.read_text())
        print('WFRL_HISTORY_EXPORT_SMOKE=PASS')
    finally:
        wfrl_blender.unregister()


if __name__ == '__main__':
    main()
