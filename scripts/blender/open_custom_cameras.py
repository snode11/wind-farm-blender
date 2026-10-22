"""Open the current three-camera housing demo (stable launcher path)."""
from pathlib import Path
import runpy
runpy.run_path(str(Path(__file__).with_name('open_stacked_cameras.py')), run_name='__main__')
