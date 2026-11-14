"""Copy verified review data into a self-contained, relocatable directory."""
import json
from pathlib import Path
import shutil
import tempfile

from .dual_beam_replay import resolve_package


def portable_copy(package, output):
    package, output = Path(package).resolve(), Path(output).resolve()
    source, overlay = resolve_package(package)
    if overlay is None:
        raise ValueError('Expected a dual-beam result package')
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.dual-beam-', dir=output.parent) as temporary:
        staging = Path(temporary) / 'package'
        geometry = staging / 'source'
        geometry.mkdir(parents=True)
        for name in overlay['manifest']['source_hashes']:
            shutil.copy2(source / name, geometry / name)
        for name in overlay['manifest']['files']:
            shutil.copy2(package / name, staging / name)
        manifest = dict(overlay['manifest'], source_package='source', portable=True)
        (staging / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        if (package / 'report.md').is_file():
            shutil.copy2(package / 'report.md', staging / 'report.md')
        resolve_package(staging)
        if output.exists():
            raise FileExistsError(output)
        staging.rename(output)
    return output
