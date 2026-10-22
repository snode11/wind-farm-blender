"""Run the official extension build on filesystems without hardlink support.

exFAT/FAT32 volumes reject os.link with WinError 1, which aborts the publish
step in build_extension.py. Substituting a copy keeps every vendoring and
integrity check in the real builder intact; only the publish mechanism changes.
"""

from __future__ import annotations

import os
import runpy
import shutil
import sys
from pathlib import Path

_real_link = os.link


def _link_or_copy(src, dst, *, follow_symlinks=True):
    try:
        return _real_link(src, dst, follow_symlinks=follow_symlinks)
    except OSError:
        # exFAT has no hardlinks; a copy satisfies the publisher's need for a
        # second independent path to the same bytes.
        shutil.copy2(src, dst)


os.link = _link_or_copy

_SCRIPT = Path(__file__).resolve().parent / 'build_extension.py'
sys.argv = [str(_SCRIPT), *sys.argv[1:]]
runpy.run_path(str(_SCRIPT), run_name='__main__')
