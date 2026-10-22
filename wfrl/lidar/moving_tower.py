"""Geometry against the solver's deformed tower surface, in turbine-local world axes."""
import numpy as np
from .physics import first_hit


def tip_surface_clearance(points, tower_points, tower_triangles, sections=19):
    """Minimum horizontal gap from the terminal contour to same-height tower.

    Intersect each segment-minus-triangle convex prism with z=0, then find
    its nearest point in xy. Interpolated witnesses remain on both surfaces;
    no circle approximation or whole-blade collision claim is involved.
    """
    contour = np.asarray(points).reshape(sections, -1, 3)[-1]
    tower = np.asarray(tower_points)[tower_triangles]
    tower = tower[(tower[:,:,2].min(1) <= contour[:,2].max()) &
                  (tower[:,:,2].max(1) >= contour[:,2].min())]
    center=contour.mean(axis=0)
    _,wall0=horizontal_clearance(center,tower_points,tower_triangles)
    candidate=contour[np.argmin(np.linalg.norm(contour[:,:2]-np.asarray(wall0)[:2],axis=1))]
    distance,wall=horizontal_clearance(candidate,tower_points,tower_triangles)
    best=(abs(distance),candidate,np.asarray(wall))
    pairs = np.array([(i,j) for i in range(6) for j in range(i+1,6)])
    from scipy.spatial import ConvexHull, QhullError
    ends=np.roll(contour,-1,axis=0)
    low=np.minimum(contour,ends); high=np.maximum(contour,ends)
    tl=tower.min(axis=1);th=tower.max(axis=1)
    gap=np.maximum(0,np.maximum(low[:,None,:2]-th[None,:,:2],tl[None,:,:2]-high[:,None,:2]))
    bound=np.linalg.norm(gap,axis=2)
    valid=(bound<=best[0]+1e-12)&(low[:,None,2]<=th[None,:,2])&(high[:,None,2]>=tl[None,:,2])
    candidates=np.argwhere(valid)
    candidates=candidates[np.argsort(bound[valid])]
    for segment,face in candidates:
        if bound[segment,face]>best[0]+1e-12: continue
        a,b=contour[segment],ends[segment];tri=tower[face]
        if np.linalg.norm(a-b) < 1e-12: continue
        tips=np.repeat([a,b],3,axis=0); walls=np.tile(tri,(2,1))
        diff=tips-walls
        if diff[:,2].min()>0 or diff[:,2].max()<0: continue
        i,j=pairs.T; dz=diff[j,2]-diff[i,2]
        valid=(np.abs(dz)>1e-14)&(diff[i,2]*diff[j,2]<=0)
        i,j,dz=i[valid],j[valid],dz[valid]
        ratio=-diff[i,2]/dz
        tp=tips[i]+ratio[:,None]*(tips[j]-tips[i])
        wp=walls[i]+ratio[:,None]*(walls[j]-walls[i])
        flat=np.flatnonzero(np.abs(diff[:,2])<1e-12)
        if len(flat):tp=np.concatenate([tp,tips[flat]]);wp=np.concatenate([wp,walls[flat]])
        if not len(tp): continue
        xy=(tp-wp)[:,:2]
        try: ids=ConvexHull(xy).vertices if len(xy)>2 else np.arange(len(xy))
        except QhullError:
            axis=int(np.argmax(np.ptp(xy,axis=0)))
            ids=np.array([np.argmin(xy[:,axis]),np.argmax(xy[:,axis])])
        # If the surfaces intersect, the z=0 difference polygon contains zero.
        for j in range(1,len(ids)-1):
            pick=ids[[0,j,j+1]];matrix=np.vstack((xy[pick].T,np.ones(3)))
            if abs(np.linalg.det(matrix))<1e-15:continue
            weights=np.linalg.solve(matrix,[0,0,1])
            if np.all(weights>=-1e-10):
                tip=weights@tp[pick];wall=weights@wp[pick]
                return 0.,tip.tolist(),wall.tolist()
        for lo,hi in zip(ids,np.roll(ids,-1)):
            edge=xy[hi]-xy[lo]; length=edge@edge
            u=float(np.clip(-xy[lo]@edge/length,0,1)) if length>1e-20 else 0.
            tip=tp[lo]+u*(tp[hi]-tp[lo]); wall=wp[lo]+u*(wp[hi]-wp[lo])
            distance=float(np.linalg.norm(tip[:2]-wall[:2]))
            if best is None or distance<best[0]:best=(distance,tip,wall)
    if best is None: raise ValueError('Terminal contour does not overlap tower height')
    # Preserve penetration sign using the independent polygon classifier.
    signed,_=horizontal_clearance(best[1],tower_points,tower_triangles)
    return (best[0] if signed>=0 else -best[0]),best[1].tolist(),best[2].tolist()


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
