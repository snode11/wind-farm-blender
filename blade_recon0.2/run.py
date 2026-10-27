#!/usr/bin/env python3
"""Dispatch v0.2 algorithms to the project external Python environment.

python3 blade_recon0.2/run.py --check-env
python3 blade_recon0.2/run.py recon --data ... --out ...
Set BLADE_RECON_PYTHON or pass --python before the command for another runtime.
This launcher does not create environments or install packages.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys


def main():
    source = Path(__file__).resolve().parent
    default = source.parent / 'blade_recon' / '.venv-trial' / 'bin' / 'python'
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', default=os.environ.get('BLADE_RECON_PYTHON', str(default)))
    parser.add_argument('--check-env', action='store_true')
    parser.add_argument('command', nargs='?', choices=('recon', 'texture', 'preview', 'eval_texture', 'synth', 'evaluate', 'coverage', 'check_alignment'))
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    # Keep the venv launcher path: resolving its symlink loses pyvenv.cfg lookup.
    python = Path(args.python).expanduser().absolute()
    if not python.is_file():
        parser.error('External Python is missing: %s; configure --python or BLADE_RECON_PYTHON' % python)
    probe = 'import sys,json,numpy,scipy,cv2; print(json.dumps(dict(python=sys.executable,numpy=numpy.__version__,scipy=scipy.__version__,opencv=cv2.__version__)))'
    result = subprocess.run([str(python), '-c', probe], text=True, capture_output=True)
    if result.returncode:
        print(result.stderr, file=sys.stderr)
        parser.error('Runtime requires NumPy, SciPy and OpenCV; see requirements.txt')
    if args.check_env:
        print(result.stdout.strip())
    if not args.command:
        if args.check_env:
            return 0
        parser.error('Choose a command, or --check-env')
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    return subprocess.call([str(python), str(source / (args.command + '.py')), *args.arguments], env=env)


if __name__ == '__main__':
    raise SystemExit(main())
