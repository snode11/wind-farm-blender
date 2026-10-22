"""Development codec loader. The extension builder replaces this with the codec.

Load the standard-library source directly, without importing the backend package.
"""
import importlib.util
from pathlib import Path

_source = Path(__file__).resolve().parents[2] / 'wfrl' / 'blender_bridge' / 'messages.py'
_spec = importlib.util.spec_from_file_location('_wfrl_wire_protocol', _source)
_codec = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_codec)
for _name in dir(_codec):
    if not _name.startswith('_'):
        globals()[_name] = getattr(_codec, _name)
