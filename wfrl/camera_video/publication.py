"""Complete-file publication with an exclusive destination name."""
from __future__ import annotations

import ctypes
import errno
import os
from pathlib import Path
import shutil
import sys
import tempfile


def commit_exclusive(source, destination):
    """Move a same-filesystem staging file without replacing any directory entry."""
    source, destination = Path(source), Path(destination)
    if os.name == 'nt':
        os.rename(source, destination)  # Windows rename fails if the name exists.
        return
    libc = ctypes.CDLL(None, use_errno=True)
    function = None
    if sys.platform == 'darwin' and hasattr(libc, 'renamex_np'):
        function = libc.renamex_np
        function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        args = (os.fsencode(source), os.fsencode(destination), 0x00000004)  # RENAME_EXCL
    elif sys.platform.startswith('linux') and hasattr(libc, 'renameat2'):
        function = libc.renameat2
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        args = (-100, os.fsencode(source), -100, os.fsencode(destination), 1)  # RENAME_NOREPLACE
    if function is not None:
        function.restype = ctypes.c_int
        if function(*args) == 0:
            return
        code = ctypes.get_errno()
        if code not in {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}:
            raise OSError(code, os.strerror(code), str(destination))
    os.link(source, destination, follow_symlinks=False)
    source.unlink()


def preflight(destination):
    """Check complete-file commit and collision protection on the actual target FS."""
    destination = Path(destination)
    if os.path.lexists(destination):
        raise FileExistsError(destination)
    with tempfile.TemporaryDirectory(prefix='.wfrl-publish-check-', dir=destination.parent) as directory:
        root = Path(directory)
        source, target, collision = (root / name for name in ('source', 'target', 'collision'))
        source.write_bytes(b'wfrl publication probe')
        commit_exclusive(source, target)
        collision.write_bytes(b'preserve this entry')
        try:
            commit_exclusive(target, collision)
        except FileExistsError:
            pass
        else:
            raise OSError('Target filesystem cannot protect an occupied output name')
        if collision.read_bytes() != b'preserve this entry':
            raise OSError('Target filesystem replaced an occupied output name')


def publish(source, destination, *, before_commit=None, on_stage=None):
    """Copy to a private target-FS staging file, flush, then commit exclusively.

    Callbacks can record the staging identity at creation and verify its complete
    contents before committing, including recovery after an interrupted status write.
    """
    source, destination = Path(source), Path(destination)
    if os.path.lexists(destination):
        raise FileExistsError(destination)
    staging, identity = None, None
    try:
        with tempfile.NamedTemporaryFile(prefix='.wfrl-video-', dir=destination.parent, delete=False) as handle:
            staging = Path(handle.name)
            identity = os.fstat(handle.fileno())
            if on_stage is not None:
                on_stage(staging, identity)
            with source.open('rb') as incoming:
                shutil.copyfileobj(incoming, handle)
            handle.flush()
            os.fsync(handle.fileno())
            identity = os.fstat(handle.fileno())
        if before_commit is not None:
            before_commit(staging, identity)
        commit_exclusive(staging, destination)
    finally:
        if staging is not None:
            try:
                current = staging.lstat()
                if identity is not None and (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                    staging.unlink()
            except FileNotFoundError:
                pass
