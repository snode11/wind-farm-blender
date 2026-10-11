"""Restore hash-pinned DTU reference inputs from an operator-supplied archive."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tempfile
from zipfile import ZipFile

PROJECT = Path(__file__).resolve().parents[1]


def restore(archive: Path, destination: Path, *, overwrite: bool = False) -> None:
    provenance = json.loads((PROJECT / 'sources/provenance.json').read_text())
    expected = {name: digest for name, digest in provenance['files'].items()
                if name in {'DTU_10MW_RWT.htc', 'DTU_10MW_RWT_ae.dat'}}
    payloads = {}
    with ZipFile(archive) as package:
        for name, digest in expected.items():
            matches = [item for item in package.infolist()
                       if Path(item.filename).name == name and not item.is_dir()]
            if len(matches) != 1:
                raise ValueError(f'Expected one {name}, found {len(matches)}')
            data = package.read(matches[0])
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError(f'Source digest mismatch: {name}')
            if (destination / name).exists() and not overwrite:
                raise FileExistsError(f'{destination / name}; use --overwrite to replace')
            payloads[name] = data
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.dtu-reference-', dir=destination) as staging:
        for name, data in payloads.items():
            temporary = Path(staging) / name
            temporary.write_bytes(data)
            temporary.replace(destination / name)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--destination', type=Path, default=PROJECT / 'sources')
    parser.add_argument('--overwrite', action='store_true')
    args = parser.parse_args()
    restore(args.archive, args.destination, overwrite=args.overwrite)
    print('Restored both hash-pinned DTU reference files')
