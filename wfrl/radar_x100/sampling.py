"""Fixed material coordinates sampled from a reference surface's area measure."""
from dataclasses import dataclass
import hashlib
import numpy as np
from .schema import MAX_POINTS, digest

AREA_ALGORITHM = 'shifted-halton.v1'


def _radical_inverse(indices, base):
    integers = np.array(indices, dtype=np.int64, copy=True)
    values = np.zeros(len(integers), dtype=float)
    weight = 1 / base
    while np.any(integers):
        values += (integers % base) * weight
        integers //= base
        weight /= base
    return values


@dataclass(frozen=True)
class MaterialPlan:
    source_id: str
    geometry_id: str
    reference_time_s: float
    triangle_ids: np.ndarray
    barycentric: np.ndarray
    seed: int
    budget: int
    method: str
    identity: str

    def __post_init__(self):
        ids = np.array(self.triangle_ids, dtype=np.int32, copy=True)
        weights = np.array(self.barycentric, dtype=np.float64, copy=True)
        if (ids.ndim != 1 or not 1 <= len(ids) <= MAX_POINTS or np.any(ids < 0)
                or weights.shape != (len(ids), 3) or not np.isfinite(weights).all()
                or np.any(weights < 0) or not np.allclose(weights.sum(axis=1), 1)):
            raise ValueError('Invalid fixed material sampling plan')
        ids.flags.writeable = weights.flags.writeable = False
        object.__setattr__(self, 'triangle_ids', ids)
        object.__setattr__(self, 'barycentric', weights)

    def points(self, sample):
        if (sample.source_id != self.source_id or sample.geometry_id != self.geometry_id
                or self.triangle_ids.max() >= len(sample.triangles)):
            raise ValueError('Material sampling source or topology changed')
        return np.einsum('nij,ni->nj', sample.vertices[sample.triangles[self.triangle_ids]], self.barycentric)


def make_plan(reference, budget=512, seed=100, method='AREA'):
    if type(budget) is not int or not 1 <= budget <= MAX_POINTS or type(seed) is not int or seed < 0:
        raise ValueError('Invalid material sampling budget or seed')
    rng = np.random.default_rng(seed)
    if method == 'AREA':
        faces = reference.vertices[reference.triangles]
        area = np.linalg.norm(np.cross(faces[:, 1] - faces[:, 0], faces[:, 2] - faces[:, 0]), axis=1) * .5
        if not np.isfinite(area).all() or area.sum() <= 0:
            raise ValueError('Reference surface has no finite positive area')
        # Shifted low-discrepancy coordinates retain identical prefixes across budgets.
        indices = np.arange(1, budget + 1)
        uniform = (np.column_stack([_radical_inverse(indices, base) for base in (2, 3, 5)]) + rng.random(3)) % 1
        ids = np.searchsorted(np.cumsum(area) / area.sum(), uniform[:, 0], side='right')
        root = np.sqrt(uniform[:, 1])
        weights = np.column_stack((1 - root, root * (1 - uniform[:, 2]), root * uniform[:, 2]))
    elif method == 'CENTROID':
        ids = np.sort(rng.choice(len(reference.triangles), min(budget, len(reference.triangles)), replace=False))
        weights = np.full((len(ids), 3), 1 / 3)
    else:
        raise ValueError('Unknown material sampling method')
    identity = digest(dict(source=reference.source_id, geometry=reference.geometry_id,
        reference_time_s=reference.time_s, method=method, algorithm=AREA_ALGORITHM if method == 'AREA' else 'triangle-centroid.v1', seed=seed, budget=budget,
        coordinates_sha256=hashlib.sha256(ids.astype('<i4').tobytes() + weights.astype('<f8').tobytes()).hexdigest()))
    return MaterialPlan(reference.source_id, reference.geometry_id, reference.time_s,
                        ids, weights, seed, budget, method, identity)
