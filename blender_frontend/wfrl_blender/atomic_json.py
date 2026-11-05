"""Atomic UTF-8 JSON and text output using a temporary file beside the destination."""
import json
import os
from pathlib import Path
import tempfile


def write_json(path, value, *, mkdir=False):
    """Flush and sync strict JSON before replacement; create parents only on request."""
    def write(handle):
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write('\n')

    _write_atomic(path, write, mkdir=mkdir)


def write_text(path, text, *, mkdir=False):
    """Flush and sync UTF-8 text unchanged, without adding or translating newlines."""
    _write_atomic(path, lambda handle: handle.write(text), mkdir=mkdir)


def _write_atomic(path, write, *, mkdir):
    path = Path(path)
    if mkdir:
        path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                                         prefix='.json-', suffix='.tmp',
                                         dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            write(handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
