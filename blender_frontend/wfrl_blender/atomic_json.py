"""Atomic UTF-8 JSON and text output using a temporary file beside the destination."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import time
import uuid


def write_json(path, value, *, mkdir=False):
    """Flush and sync strict JSON before replacement; create parents only on request."""
    with _open_atomic(path, mkdir=mkdir) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write('\n')


@contextmanager
def write_json_array(path, metadata, key):
    """Write a JSON object with one incrementally appended array and publish on success."""
    if not isinstance(metadata, dict) or not isinstance(key, str):
        raise TypeError('A streamed array requires object metadata and a string field name')
    if key in metadata:
        raise ValueError(f'Array field already present: {key}')
    header = json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False)
    with _open_atomic(path, mkdir=False) as handle:
        handle.write(header[:-1].rstrip())
        if metadata:
            handle.write(',')
        handle.write(f'\n  {json.dumps(key, ensure_ascii=False)}: [')
        has_items = False

        def append(item):
            nonlocal has_items
            row = json.dumps(item, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
            handle.write((',\n' if has_items else '\n') + '    ' + row)
            has_items = True

        yield append
        handle.write('\n  ]\n}\n' if has_items else ']\n}\n')


def write_text(path, text, *, mkdir=False):
    """Flush and sync UTF-8 text unchanged, without adding or translating newlines."""
    with _open_atomic(path, mkdir=mkdir) as handle:
        handle.write(text)


@contextmanager
def _open_atomic(path, *, mkdir):
    path = Path(path)
    if mkdir:
        path.parent.mkdir(parents=True, exist_ok=True)
    mode = None
    try:
        target_stat = path.lstat()
    except FileNotFoundError:
        pass
    else:
        if stat.S_ISREG(target_stat.st_mode) and hasattr(os, 'fchmod'):
            mode = stat.S_IMODE(target_stat.st_mode)
    temporary = None
    descriptor = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0)
        while temporary is None:
            candidate = path.parent / f'.json-{uuid.uuid4().hex}.tmp'
            try:
                descriptor = os.open(candidate, flags, 0o666)
            except FileExistsError:
                continue
            temporary = candidate
        with os.fdopen(descriptor, mode='w', encoding='utf-8', newline='\n') as handle:
            descriptor = None
            if mode is not None:
                os.fchmod(handle.fileno(), mode)
            yield handle
            handle.flush()
            if mode is not None and mode & (stat.S_ISUID | stat.S_ISGID):
                # Writes can clear set-ID bits; restore them before syncing.
                os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
        retry_delays = (0.05, 0.1, 0.2, 0.4) if os.name == 'nt' else ()
        for attempt in range(len(retry_delays) + 1):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == len(retry_delays):
                    raise
                time.sleep(retry_delays[attempt])
    finally:
        try:
            if descriptor is not None:
                os.close(descriptor)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
