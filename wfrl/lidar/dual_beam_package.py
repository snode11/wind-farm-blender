"""Copy verified review data into a self-contained, relocatable directory."""
import json
from pathlib import Path
import shutil

from .dual_beam_replay import resolve_package


def portable_copy(package, output):
    package, output = Path(package).resolve(), Path(output).resolve()
    source, overlay = resolve_package(package)
    if overlay is None:
        raise ValueError('Expected a dual-beam result package')
    output.mkdir(parents=True, exist_ok=False)
    geometry = output / 'source'
    geometry.mkdir()
    for name in overlay['manifest']['source_hashes']:
        shutil.copy2(source / name, geometry / name)
    for name in overlay['manifest']['files']:
        shutil.copy2(package / name, output / name)
    manifest = dict(overlay['manifest'], source_package='source', portable=True)
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    if (package / 'report.md').is_file():
        shutil.copy2(package / 'report.md', output / 'report.md')
    resolve_package(output)
    return output
