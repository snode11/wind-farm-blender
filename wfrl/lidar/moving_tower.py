"""Geometry against the solver's deformed tower surface, in turbine-local world axes."""
import numpy as np
from .physics import first_hit


def horizontal_clearance(tip, points, triangles):
    """Distance to the actual triangulated tower's horizontal section at tip z.

    This is the saved tip-reference clearance, not whole-blade minimum distance.
    The surface polygon and its discretization are retained without fitting a circle.
    """
    tip = np.asarray(tip, float)
    tri = np.asarray(points)[triangles]
    tri = tri[(tri[:, :, 2].min(1) <= tip[2]) & (tri[:, :, 2].max(1) >= tip[2])]
    segments = []
    for face in tri:
        hits = []
        for a, b in zip(face, np.roll(face, -1, axis=0)):
            dz = b[2] - a[2]
            if abs(dz) < 1e-12:
                if abs(tip[2] - a[2]) < 1e-10: hits.extend([a, b])
            else:
                ratio = (tip[2] - a[2]) / dz
                if -1e-10 <= ratio <= 1 + 1e-10: hits.append(a + np.clip(ratio, 0, 1) * (b - a))
        if len(hits) >= 2:
            unique = np.unique(np.round(hits, 10), axis=0)
            if len(unique) >= 2: segments.append((unique[0], unique[-1]))
    if not segments:
        raise ValueError('Tip height does not intersect moving tower')
    seg = np.asarray(segments)
    a, edge = seg[:, 0], seg[:, 1] - seg[:, 0]
    length = np.sum(edge * edge, axis=1)
    u = np.clip(np.sum((tip - a) * edge, axis=1) / length, 0, 1)
    nearest = a + u[:, None] * edge
    distances = np.linalg.norm(nearest - tip, axis=1)
    index = int(np.argmin(distances))
    # Odd-even crossing classifies points inside the horizontal polygon.
    x, y = tip[:2]; a2, b2 = seg[:, 0, :2], seg[:, 1, :2]
    cross = (a2[:, 1] > y) != (b2[:, 1] > y)
    selected_a, selected_b = a2[cross], b2[cross]
    xcross = selected_a[:, 0] + (y - selected_a[:, 1]) * (selected_b[:, 0] - selected_a[:, 0]) / (selected_b[:, 1] - selected_a[:, 1])
    inside = int(np.count_nonzero(xcross > x)) % 2 == 1
    return float(distances[index]) * (-1 if inside else 1), nearest[index].tolist()


def background_hit(origin, direction, points, triangles):
    hits = []
    tower = first_hit(origin, direction, points, triangles)
    if tower: hits.append((tower[0], tower[1], 'tower'))
    if direction[2] < 0 and origin[2] >= 0:
        t = -origin[2] / direction[2]
        if t > 1e-8: hits.append((float(t), (origin + t * direction).tolist(), 'ground'))
    return min(hits, key=lambda hit: hit[0]) if hits else None


def separated_from_tower(points, triangles, tower_points, tower_triangles):
    """Conservative per-blade-triangle separation from a tower slab envelope.

    At each blade triangle's height interval, enclose overlapping tower triangles.
    Passing certifies separation of these discrete surfaces. Failure is unresolved.
    """
    tri = points[triangles]; tower = tower_points[tower_triangles]
    lo, hi = tri[:, :, 2].min(1), tri[:, :, 2].max(1)
    tlo, thi = tower[:, :, 2].min(1), tower[:, :, 2].max(1)
    active = (lo <= thi.max()) & (hi >= tlo.min())
    tri, lo, hi = tri[active], lo[active], hi[active]
    xmin = tower[:, :, 0].min(1)
    # Fast sufficient bound handles most blades without a pairwise matrix.
    pending = tri[:, :, 0].max(1) >= xmin.min() - 1e-8
    for face, low, high in zip(tri[pending], lo[pending], hi[pending]):
        overlap = (tlo <= high) & (thi >= low)
        if not overlap.any() or face[:, 0].max() < xmin[overlap].min() - 1e-8:
            continue
        envelope = tower[overlap].reshape(-1,3)[:, :2]
        lower, upper = envelope.min(0), envelope.max(0)
        if np.any(face[:, :2].max(0) < lower-1e-8) or np.any(face[:, :2].min(0) > upper+1e-8):
            continue
        center = (lower+upper)/2
        radius = np.linalg.norm(envelope-center,axis=1).max()
        xy = face[:, :2]-center
        edge = np.roll(xy,-1,axis=0)-xy
        lengths = np.sum(edge*edge,axis=1)
        alpha = np.divide(-np.sum(xy*edge,axis=1),lengths,out=np.zeros(3),where=lengths>0)
        nearest = xy+np.clip(alpha,0,1)[:,None]*edge
        distance = np.linalg.norm(nearest,axis=1).min()
        cross = edge[:,0]*(-xy[:,1])-edge[:,1]*(-xy[:,0])
        inside = np.all(cross>=0) or np.all(cross<=0)
        if inside or distance <= radius+1e-8:
            return False
    return True
