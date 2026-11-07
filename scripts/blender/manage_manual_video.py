"""Query, wait, cancel or retry an existing manual-video job with standalone Python."""
from pathlib import Path
import runpy

if __name__ == '__main__':
    root = Path(__file__).resolve().parents[2]
    runpy.run_path(str(root / 'blender_frontend/wfrl_blender/manual_video_job.py'), run_name='__main__')
