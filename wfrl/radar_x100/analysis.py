"""Observation-only same-source geometry diagnostics; no target association."""
from dataclasses import dataclass
import numpy as np


def readonly(value, dtype=np.float64):
    array = np.array(value, dtype=dtype, copy=True)
    array.flags.writeable = False
    return array


@dataclass(frozen=True)
class Context:
    identity: tuple
    world_from_analysis: np.ndarray
    world_from_sensor: np.ndarray
    vertices: np.ndarray
    triangles: np.ndarray

    def __post_init__(self):
        for name in ('world_from_analysis', 'world_from_sensor', 'vertices', 'triangles'):
            object.__setattr__(self, name, readonly(getattr(self, name),
                np.int32 if name == 'triangles' else np.float64))
        for matrix in (self.world_from_analysis, self.world_from_sensor):
            if matrix.shape != (4,4) or not np.isfinite(matrix).all():
                raise ValueError('Invalid analysis transform')
        validate_surface(self.vertices, self.triangles)


def validate_surface(vertices, triangles):
    if (vertices.ndim != 2 or vertices.shape[1:] != (3,) or not np.isfinite(vertices).all()
            or triangles.ndim != 2 or triangles.shape[1:] != (3,) or not len(triangles)
            or np.any(triangles < 0) or np.any(triangles >= len(vertices))):
        raise ValueError('Invalid tower side surface')
    faces = vertices[triangles]
    if np.any(np.linalg.norm(np.cross(faces[:,1]-faces[:,0], faces[:,2]-faces[:,0]), axis=1) <= 1e-12):
        raise ValueError('Degenerate tower side surface')


def transform(points, matrix):
    return np.asarray(points, dtype=np.float64) @ matrix[:3,:3].T + matrix[:3,3]


def local(points, world_from_local):
    return (points-world_from_local[:3,3]) @ world_from_local[:3,:3]


def closest_reference(points, vertices, triangles):
    """Independent small-sample NumPy oracle: plane interior plus three closed edges."""
    vertices, triangles = np.asarray(vertices, float), np.asarray(triangles, int)
    validate_surface(vertices, triangles)
    faces = vertices[triangles]
    a,b,c = faces[:,0],faces[:,1],faces[:,2]
    u,v = b-a,c-a
    n = np.cross(u,v)
    nn = np.einsum('ij,ij->i',n,n)
    distances, nearest, ids = [], [], []
    for point in np.asarray(points, float):
        if not np.isfinite(point).all():
            distances.append(np.nan); nearest.append([np.nan]*3); ids.append(-1); continue
        q = point-n*(np.einsum('ij,ij->i',point-a,n)/nn)[:,None]
        w=q-a
        beta=np.einsum('ij,ij->i',np.cross(w,v),n)/nn
        gamma=np.einsum('ij,ij->i',np.cross(u,w),n)/nn
        candidates=[np.where(((beta>=0)&(gamma>=0)&(beta+gamma<=1))[:,None],q,np.inf)]
        for x,y in ((a,b),(b,c),(c,a)):
            edge=y-x
            t=np.clip(np.einsum('ij,ij->i',point-x,edge)/np.einsum('ij,ij->i',edge,edge),0,1)
            candidates.append(x+t[:,None]*edge)
        candidates=np.stack(candidates)
        squared=np.sum((candidates-point)**2,axis=2)
        i,j=np.unravel_index(np.argmin(squared),squared.shape)
        distances.append(np.sqrt(squared[i,j])); nearest.append(candidates[i,j]); ids.append(j)
    return np.array(distances),np.array(nearest).reshape(-1,3),np.array(ids)


def distance_colors(distances, maximum=10.):
    if not np.isfinite(maximum) or maximum <= 0:
        raise ValueError('Invalid diagnostic color limit')
    values=np.asarray(distances)
    valid=np.isfinite(values)
    colors=np.tile([.5,.5,.5,1.],(len(values),1))
    t=np.clip(values[valid]/maximum,0,1)
    colors[valid,:3]=np.column_stack((.12+.78*t,.2+.65*t,.38+.52*t))
    return colors.astype(np.float32),int(np.sum(values[valid]>maximum))
