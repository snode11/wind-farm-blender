"""Explicit independent blade reference contract for BeamDyn replay v3."""
import hashlib
import json
from pathlib import Path
import numpy as np


def read_reference(path, manifest):
    if manifest.get('schema') != 'wfrl.farm-flex-review.v3':
        if 'blade-reference.json' in manifest['files']:
            raise ValueError('Prebend reference requires replay v3')
        return None
    name = 'blade-reference.json'
    raw = (Path(path) / name).read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest['files'].get(name):
        raise ValueError('Blade reference integrity mismatch')
    data = json.loads(raw)
    curve = np.asarray(data['curve'], float)
    if (data['schema'] != 'wfrl.blade-reference.v1' or data['structural_module'] != 'BeamDyn'
            or manifest.get('structural_module') != 'BeamDyn'
            or data['model'] != manifest.get('model')
            or data['definition'] != 'x=-(z/61.5)^2; y=0; metres; z from blade root'
            or curve.shape != (49, 4) or not np.isfinite(curve).all()
            or np.any(np.diff(curve[:, 2]) <= 0)
            or not np.allclose(curve[[0,-1], 2], [0,61.5], atol=1e-8)
            or not np.allclose(curve[:,0], -(curve[:,2]/61.5)**2, atol=1e-8)
            or not np.allclose(curve[:,1], 0, atol=1e-8)
            or not np.allclose(data['tip_local_m'], [-1,0,63], atol=1e-8)
            or data.get('precone_deg') != -2.5
            or data['reference_state'] != 'independent zero-load stationary solve'):
        raise ValueError('Unsupported or mismatched BeamDyn reference geometry')
    return data


def bend_vertices(vertices, reference):
    """Same analytic reference as the solver, before any loaded transforms."""
    result = np.asarray(vertices, dtype=float).copy()
    span = np.clip(result[:, 2] - 1.5, 0, 61.5)
    result[:, 0] -= (span / 61.5)**2
    return result
