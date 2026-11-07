"""Process and filesystem fixtures shared by host tests."""
import os
from pathlib import Path
import subprocess
import sys
from unittest import SkipTest


def venv_python(environment):
    return environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def symlink_or_skip(path, target, *, target_is_directory=False):
    try:
        path.symlink_to(target, target_is_directory=target_is_directory)
    except (OSError, NotImplementedError) as exc:
        raise SkipTest(f'Symbolic link fixture unavailable: {exc}') from exc


def write_python_tool(path, body, *, mode=0o755):
    if os.name == 'nt':
        path = path.resolve().with_suffix('.py')
        path.write_text(body, encoding='utf-8')
    else:
        path.write_text(f'#!{sys.executable}\n{body}\n', encoding='utf-8')
        path.chmod(mode)
    return path


def python_tool_popen(scripts, *, python=sys.executable):
    """Start known Python fixtures directly so their process handles belong to Python."""
    popen = subprocess.Popen

    def launch(argv, *args, **kwargs):
        if isinstance(argv, (list, tuple)) and argv and Path(os.fsdecode(argv[0])) in scripts:
            argv = [python, *argv]
        return popen(argv, *args, **kwargs)

    return launch
