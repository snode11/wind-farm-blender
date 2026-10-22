"""Validated support motion for flexible-tower farm archives."""
from pathlib import Path
import hashlib
import numpy as np


def read_tower(path, manifest, times):
    name = 'tower-motion.npz'
    if manifest.get('schema') in ('wfrl.farm-flex-review.v2', 'wfrl.farm-flex-review.v3') and manifest.get('tower_model') != 'elastodyn-flexible':
        raise ValueError('V2 requires flexible tower metadata')
    if manifest.get('tower_model', 'rigid') == 'rigid':
        if name in manifest['files']: raise ValueError('Unexpected moving tower in rigid package')
        return None
    if manifest.get('schema') not in ('wfrl.farm-flex-review.v2', 'wfrl.farm-flex-review.v3') or manifest.get('tower_model') != 'elastodyn-flexible':
        raise ValueError('Unsupported tower model')
    raw = (Path(path) / name).read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest['files'].get(name):
        raise ValueError('Tower motion integrity mismatch')
    with np.load(Path(path) / name, allow_pickle=False) as archive:
        data = {key: archive[key].copy() for key in archive.files}
    n = len(times); turbines = len(manifest['turbine_ids']); z = data['heights']
    if (z.ndim != 1 or len(z) < 2 or np.any(np.diff(z) <= 0)
            or not np.allclose(z[[0,-1]],[0,87.6],atol=1e-6,rtol=0)
            or not np.array_equal(data['times'], times)
            or data['transforms'].shape != (n, turbines, len(z), 3, 4)
            or data['nacelle'].shape != (n, turbines, 3, 4)
            or any(not np.isfinite(v).all() for v in data.values())):
        raise ValueError('Invalid tower motion shape/time/values')
    for key in ('transforms', 'nacelle'):
        r = data[key][..., :3]
        if not np.allclose(np.swapaxes(r, -1, -2) @ r, np.eye(3), atol=2e-5, rtol=0) or np.any(np.linalg.det(r) < .9999):
            raise ValueError('Invalid tower rotation')
    return data


def interpolate_transform(a, b, alpha):
    """Interpolate translation and project rotation onto SO(3)."""
    out = a * (1-alpha) + b * alpha
    u, _, vt = np.linalg.svd(out[..., :3])
    out[..., :3] = u @ vt
    return out
