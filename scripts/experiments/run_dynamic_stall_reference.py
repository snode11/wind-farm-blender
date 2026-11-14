"""Run the pinned official UA case in a new temporary directory (A1)."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import signal
import subprocess
from urllib.request import urlopen

from scripts.experiments.check_openfast_reference import compare

REVISION = '548bf2143633a7066f4582167fdf4180951fd7f6'
PREFIX = 'glue-codes/openfast/'
CASE = '5MW_Land_DLL_WTurb/5MW_Land_DLL_WTurb.fst'


def bounded_run(command, directory, log, timeout):
    with log.open('wb') as stream:
        process = subprocess.Popen(command, cwd=directory, stdout=stream, stderr=subprocess.STDOUT,
                                   env={**os.environ, 'OMP_NUM_THREADS': '1'}, start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
        except BaseException:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10.)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise
    if code:
        raise RuntimeError(f'Process exited {code}: {log}')


def run(output, openfast, compiler='gfortran', timeout=600.):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    tree = json.load(urlopen(f'https://api.github.com/repos/OpenFAST/r-test/git/trees/{REVISION}?recursive=1', timeout=60))
    available = {item['path'][len(PREFIX):] for item in tree['tree']
                 if item['type'] == 'blob' and item['path'].startswith(PREFIX)}
    hashes = {}

    def fetch(name):
        if name in hashes:
            return
        if name not in available or name.startswith('../'):
            raise ValueError(f'Unexpected official resource: {name}')
        raw = urlopen(f'https://raw.githubusercontent.com/OpenFAST/r-test/{REVISION}/{PREFIX}{name}', timeout=60).read()
        path = output/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        hashes[name] = hashlib.sha256(raw).hexdigest()
        if path.suffix not in ('.dat', '.fst'):
            return
        for reference in re.findall(r'"([^"\r\n]+)"', raw.decode('utf-8')):
            child = posixpath.normpath(str(PurePosixPath(name).parent/reference))
            if child in available and PurePosixPath(child).suffix in ('.dat', '.txt', '.bts', '.wnd'):
                fetch(child)

    fetch(CASE)
    reference_name = str(PurePosixPath(CASE).with_suffix('.outb'))
    fetch(reference_name)
    reference = output/'reference.outb'
    (output/reference_name).rename(reference)
    hashes.pop(reference_name)
    controller = '5MW_Baseline/ServoData/DISCON/DISCON.F90'
    fetch(controller)
    bounded_run([compiler, '-shared', '-fPIC', '-ffree-line-length-none', '-fdefault-real-8',
                 '-DIMPLICIT_DLLEXPORT', '-o', str(output/'5MW_Baseline/ServoData/DISCON.dll'),
                 str(output/controller)], output, output/'compile.log', timeout)
    bounded_run([str(Path(openfast).resolve()), str(output/CASE)], output, output/'solver.log', timeout)
    if 'OpenFAST terminated normally.' not in (output/'solver.log').read_text(encoding='utf-8', errors='replace'):
        raise RuntimeError('OpenFAST normal termination was not confirmed')
    for name, digest in hashes.items():
        if hashlib.sha256((output/name).read_bytes()).hexdigest() != digest:
            raise RuntimeError(f'Official input changed: {name}')
    result = compare(reference, output/reference_name)
    result.update(revision=REVISION, inputs_sha256=hashes,
                  solver_sha256=hashlib.sha256(Path(openfast).read_bytes()).hexdigest(),
                  completion='OpenFAST process exited; normal termination; official input hashes unchanged')
    (output/'A1.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(result, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--openfast', type=Path, required=True)
    parser.add_argument('--compiler', default='gfortran')
    parser.add_argument('--timeout', type=float, default=600.)
    args = parser.parse_args()
    run(args.output, args.openfast, args.compiler, args.timeout)


if __name__ == '__main__':
    main()
