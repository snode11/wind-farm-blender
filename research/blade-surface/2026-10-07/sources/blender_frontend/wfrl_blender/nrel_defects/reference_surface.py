"""Frozen healthy NREL display loft and metre-based defect parameterization.

The packaged upper/lower airfoil branches register suction/pressure in native
blade coordinates (x axial, y chordwise, z radial). Anchors use span from the
blade root; vertex coordinates retain the frontend's hub-radius offset. This
binds defects to the actual display mesh, not to a structural measurement mesh.
"""
from bisect import bisect_right
from copy import deepcopy
import hashlib
import json
import math
import random
from pathlib import Path

from .defect_shapes import PATCH_KINDS, patch_width_factor

PARAMETERIZATION_VERSION = 'nrel-display-healthy-triangles-v1'
BINDING_TOLERANCE_M = 1e-5
DIMENSION_TOLERANCE_M = .001


def _add(a, b): return tuple(x+y for x, y in zip(a, b))
def _sub(a, b): return tuple(x-y for x, y in zip(a, b))
def _mul(a, s): return tuple(x*s for x in a)
def _dot(a, b): return sum(x*y for x, y in zip(a, b))
def _cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
def _norm(a): return math.sqrt(_dot(a, a))
def _unit(a):
    n = _norm(a)
    if n < 1e-12: raise ValueError('DEGENERATE_SURFACE: tangent or normal')
    return _mul(a, 1/n)


def _finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name} must be a finite number')
    return float(value)


def _closest_triangle(p, a, b, c):
    """Closest point and barycentrics, including edges (Ericson region tests)."""
    ab, ac, ap = _sub(b, a), _sub(c, a), _sub(p, a)
    d1, d2 = _dot(ab, ap), _dot(ac, ap)
    if d1 <= 0 and d2 <= 0: return a, (1., 0., 0.)
    bp = _sub(p, b); d3, d4 = _dot(ab, bp), _dot(ac, bp)
    if d3 >= 0 and d4 <= d3: return b, (0., 1., 0.)
    vc = d1*d4-d3*d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        v = d1/(d1-d3); return _add(a, _mul(ab, v)), (1-v, v, 0.)
    cp = _sub(p, c); d5, d6 = _dot(ab, cp), _dot(ac, cp)
    if d6 >= 0 and d5 <= d6: return c, (0., 0., 1.)
    vb = d5*d2-d1*d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        w = d2/(d2-d6); return _add(a, _mul(ac, w)), (1-w, 0., w)
    va = d3*d6-d5*d4
    if va <= 0 and d4-d3 >= 0 and d5-d6 >= 0:
        w = (d4-d3)/((d4-d3)+(d5-d6))
        return _add(b, _mul(_sub(c, b), w)), (0., 1-w, w)
    den = 1/(va+vb+vc); v, w = vb*den, vc*den
    return _add(a, _add(_mul(ab, v), _mul(ac, w))), (1-v-w, v, w)


class ReferenceSurface:
    """Immutable-by-convention healthy mesh; returned metadata is copied.

    Quad tessellation is frozen to the (a,c) diagonal.  Consumers needing exact
    analysis agreement should use ``triangles`` rather than retessellating quads.
    """
    reference_geometry_id = 'nrel5mw-display-reference'
    parameterization_version = PARAMETERIZATION_VERSION

    def __init__(self, vertices, faces, ring_points=48, metadata=None):
        self.geometry = deepcopy(metadata or {})
        asset = Path(__file__).parents[1] / 'assets/nrel5mw_geometry.json'
        resource_bytes = asset.read_bytes()
        source = json.loads(resource_bytes)
        self.hub_radius_m = _finite(self.geometry.get('hub_radius_m', source['scalars']['HubRad']), 'hub_radius_m')
        if type(ring_points) is not int or ring_points < 12 or ring_points % 2:
            raise ValueError('ring_points must be an even integer >= 12')
        self.n = ring_points
        self.vertices = tuple(tuple(_finite(x, 'vertex') for x in v) for v in vertices)
        if not self.vertices or any(len(v) != 3 for v in self.vertices):
            raise ValueError('Reference vertices must contain three finite coordinates')
        self.faces = tuple(tuple(f) for f in faces)
        if not self.faces or any(len(f) < 3 or any(type(i) is not int or not 0 <= i < len(self.vertices) for i in f)
                                 for f in self.faces):
            raise ValueError('Invalid reference faces')
        # Loft rows are complete rings; the terminal apex and optional loose
        # mechanical-tip probe are individual vertices, never extra rings.
        self.ring_count = len(self.vertices) // self.n
        if self.ring_count < 2 or len(self.vertices) % self.n not in (1, 2):
            raise ValueError('NREL display loft requires complete rings and a terminal apex')
        self._spans = tuple(self.vertices[k*self.n][2]-self.hub_radius_m for k in range(self.ring_count))
        if any(b <= a for a, b in zip(self._spans, self._spans[1:])):
            raise ValueError('NREL reference spans must increase strictly')
        if any(abs(self.vertices[k*self.n+j][2]-self.hub_radius_m-s) > 1e-5
               for k, s in enumerate(self._spans) for j in range(self.n)):
            raise ValueError('NREL reference rings must have a constant radial coordinate')
        self.stations = tuple(dict(span_m=s) for s in self._spans)
        self.reference_span_m = _finite(source['scalars']['TipRad']-self.hub_radius_m, 'reference_span_m')
        turbine_ids = self.geometry.get('turbine_ids', ('T1', 'T2', 'T3'))
        if not isinstance(turbine_ids, (list, tuple)):
            raise ValueError('turbine_ids must be an array')
        self.turbine_ids = tuple(turbine_ids)
        if not self.turbine_ids or any(not isinstance(tid, str) or not tid for tid in self.turbine_ids) or len(set(self.turbine_ids)) != len(self.turbine_ids):
            raise ValueError('turbine_ids must contain unique nonempty strings')
        self._us = tuple(.5*(1+math.cos(math.tau*j/self.n)) for j in range(self.n))
        self.triangles, self.triangle_face_indices = self._triangulate()
        # Exclude the cylindrical root, edge seams and presentation tip taper.
        shoulder = float(source['blade_stations'][-2][0])
        self.domain = {'s_m': (10., min(shoulder-.3, self._spans[-1])), 'u': (.05, .95)}
        if self.domain['s_m'][1] <= self.domain['s_m'][0]:
            raise ValueError('NREL display reference has no supported airfoil region')
        self.resource_sha256 = {'assets/nrel5mw_geometry.json': hashlib.sha256(resource_bytes).hexdigest()}
        payload = dict(geometry=self.geometry, vertices=self.vertices, faces=self.faces, ring_points=self.n,
                       resources=self.resource_sha256, parameterization=self.parameterization_version)
        self.reference_geometry_sha256 = hashlib.sha256(json.dumps(payload,
            sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

    def _triangulate(self):
        triangles, parents = [], []
        for parent, face in enumerate(self.faces):
            ordered = face
            if len(face) == 4 and all(i < self.ring_count*self.n for i in face):
                rows = sorted(set(i//self.n for i in face))
                cols = set(i % self.n for i in face)
                if len(rows) == 2 and rows[1] == rows[0]+1 and len(cols) == 2:
                    j = next((j for j in cols if (j+1) % self.n in cols), None)
                    if j is not None:
                        k = (j+1) % self.n
                        # a=lower j, b=upper j, c=upper j+1, d=lower j+1.
                        ordered = (rows[0]*self.n+j, rows[1]*self.n+j,
                                   rows[1]*self.n+k, rows[0]*self.n+k)
                        authored = _cross(_sub(self.vertices[face[1]], self.vertices[face[0]]),
                                          _sub(self.vertices[face[2]], self.vertices[face[0]]))
                        canonical = _cross(_sub(self.vertices[ordered[1]], self.vertices[ordered[0]]),
                                           _sub(self.vertices[ordered[2]], self.vertices[ordered[0]]))
                        if _dot(authored, canonical) < 0:
                            ordered = (ordered[0], ordered[3], ordered[2], ordered[1])
            for j in range(1, len(ordered)-1):
                triangles.append((ordered[0], ordered[j], ordered[j+1])); parents.append(parent)
        return tuple(triangles), tuple(parents)

    def metadata(self):
        return dict(reference_geometry_id=self.reference_geometry_id,
            reference_geometry_sha256=self.reference_geometry_sha256,
            parameterization_version=self.parameterization_version,
            reference_span_m=self.reference_span_m, valid_domain=deepcopy(self.domain),
            surface_registration={'suction': 'packaged NREL upper airfoil branch', 'pressure': 'packaged NREL lower airfoil branch'},
            region_policy='airfoil display surface only; cylindrical root, tapered tip and edge-crossing paths unsupported',
            hub_radius_m=self.hub_radius_m, turbine_ids=list(self.turbine_ids),
            provenance=deepcopy(self.geometry),
            resource_sha256=deepcopy(self.resource_sha256), triangle_diagonal='quad a-c',
            binding_tolerance_m=BINDING_TOLERANCE_M, dimension_tolerance_m=DIMENSION_TOLERANCE_M)

    def _anchor(self, anchor):
        if not isinstance(anchor, dict): raise ValueError('anchor must be an object')
        side = anchor.get('surface_side')
        if side not in ('pressure', 'suction'): raise ValueError('surface_side must be pressure or suction')
        s, u = _finite(anchor.get('s_m'), 's_m'), _finite(anchor.get('u'), 'u')
        theta = _finite(anchor.get('theta_deg', 0.), 'theta_deg')
        if not 0 <= theta < 360: raise ValueError('theta_deg must be in [0,360)')
        if not self.domain['s_m'][0] <= s <= self.domain['s_m'][1] or not .05 <= u <= .95:
            raise ValueError('UNSUPPORTED_SURFACE_REGION: valid s and u domain exceeded')
        if anchor.get('region_hint', 'surface') != 'surface':
            raise ValueError('UNSUPPORTED_SURFACE_REGION: edge seams are not supported')
        return s, u, side

    def frame(self, anchor):
        s, u, side = self._anchor(anchor)
        k = max(0, min(len(self._spans)-2, bisect_right(self._spans, s)-1))
        if side == 'suction':
            j = next(j for j in range(self.n//2) if self._us[j] >= u >= self._us[j+1])
        else:
            j = next(j for j in range(self.n//2, self.n-1) if self._us[j] <= u <= self._us[j+1])
        ds, du = self._spans[k+1]-self._spans[k], self._us[j+1]-self._us[j]
        t, v = (s-self._spans[k])/ds, (u-self._us[j])/du
        a, b = self.vertices[k*self.n+j], self.vertices[(k+1)*self.n+j]
        c, d = self.vertices[(k+1)*self.n+j+1], self.vertices[k*self.n+j+1]
        if t >= v:
            p = _add(a, _add(_mul(_sub(b, a), t), _mul(_sub(c, b), v)))
            dp_ds, dp_du = _mul(_sub(b, a), 1/ds), _mul(_sub(c, b), 1/du)
        else:
            p = _add(a, _add(_mul(_sub(c, d), t), _mul(_sub(d, a), v)))
            dp_ds, dp_du = _mul(_sub(c, d), 1/ds), _mul(_sub(d, a), 1/du)
        normal = _unit(_mul(_cross(dp_ds, dp_du), -1 if side == 'suction' else 1))
        e_s = _unit(dp_ds)
        return dict(point=p, normal=normal, e_s=e_s, e_t=_unit(_cross(normal, e_s)),
                    e_u=_unit(dp_du), dp_ds=dp_ds, dp_du=dp_du)

    def point(self, anchor): return self.frame(anchor)['point']

    def locate(self, point, surface_side=None, tolerance_m=BINDING_TOLERANCE_M):
        """Accept a healthy-mesh click only; never silently snap a groove wall."""
        p = tuple(_finite(x, 'point') for x in point)
        if len(p) != 3: raise ValueError('point must have three coordinates')
        tolerance_m = _finite(tolerance_m, 'tolerance_m')
        if tolerance_m <= 0 or tolerance_m > BINDING_TOLERANCE_M:
            raise ValueError('Binding tolerance cannot exceed the frozen 1e-5 m')
        if surface_side not in (None, 'pressure', 'suction'): raise ValueError('Unknown surface side')
        matches = []
        s = p[2]-self.hub_radius_m
        if not self.domain['s_m'][0] <= s <= self.domain['s_m'][1]:
            raise ValueError('UNSUPPORTED_SURFACE_REGION')
        # At a fixed Z every triangle intersects a straight segment. Include the
        # diagonal break to avoid interpolation across a nonplanar quad.
        k = min(len(self._spans)-2, bisect_right(self._spans, s)-1)
        t = (s-self._spans[k])/(self._spans[k+1]-self._spans[k])
        for side in ((surface_side,) if surface_side else ('suction', 'pressure')):
            js = range(self.n//2) if side == 'suction' else range(self.n//2, self.n-1)
            for j in js:
                low, high = sorted((self._us[j], self._us[j+1]))
                low, high = max(.05, low), min(.95, high)
                if low > high: continue
                diag = self._us[j]+t*(self._us[j+1]-self._us[j])
                knots = sorted(set([low, high]+([diag] if low < diag < high else [])))
                for u0, u1 in zip(knots, knots[1:]):
                    anchor = dict(s_m=s, u=u0, surface_side=side, theta_deg=0.)
                    a = self.point(anchor); b = self.point(dict(anchor, u=u1))
                    ab = _sub(b, a); v = max(0., min(1., _dot(_sub(p, a), ab)/_dot(ab, ab)))
                    q = _add(a, _mul(ab, v))
                    if _norm(_sub(p, q)) <= tolerance_m:
                        found = dict(anchor, u=u0+v*(u1-u0))
                        if not any(f['surface_side'] == side and abs(f['u']-found['u']) < 1e-8 for f in matches):
                            matches.append(found)
        if len(matches) != 1: raise ValueError('AMBIGUOUS_OR_OFF_REFERENCE_SURFACE: reselect healthy surface')
        return matches[0]

    def project(self, point, side):
        """Explicit nearest mapping; unsupported nearest regions are rejected."""
        p = tuple(_finite(x, 'point') for x in point)
        if len(p) != 3 or side not in ('suction', 'pressure'): raise ValueError('Invalid projection input')
        best = None
        js = range(self.n//2) if side == 'suction' else range(self.n//2, self.n-1)
        # Visit nearest span bands first; a Z-distance bound then prunes safely.
        span = p[2]-self.hub_radius_m
        bands = sorted(range(len(self._spans)-1), key=lambda k: abs(.5*(self._spans[k]+self._spans[k+1])-span))
        for k in bands:
            zdist = max(self._spans[k]-span, span-self._spans[k+1], 0.)
            if best is not None and zdist*zdist > best[0]: continue
            for j in js:
                ids = (k*self.n+j, (k+1)*self.n+j, (k+1)*self.n+j+1, k*self.n+j+1)
                uv = ((self._spans[k], self._us[j]), (self._spans[k+1], self._us[j]),
                      (self._spans[k+1], self._us[j+1]), (self._spans[k], self._us[j+1]))
                for tri in ((0, 1, 2), (0, 2, 3)):
                    q, weights = _closest_triangle(p, *(self.vertices[ids[i]] for i in tri))
                    distance = _dot(_sub(p, q), _sub(p, q))
                    if best is None or distance < best[0]:
                        s, u = (sum(w*uv[i][axis] for w, i in zip(weights, tri)) for axis in (0, 1))
                        best = (distance, dict(s_m=s, u=u, surface_side=side, theta_deg=0.))
        self._anchor(best[1])
        return best[1]

    def _step(self, anchor, theta, distance):
        f = self.frame(anchor); angle = math.radians(theta)
        direction = _add(_mul(f['e_s'], math.cos(angle)), _mul(f['e_t'], math.sin(angle)))
        a, b = f['dp_ds'], f['dp_du']
        aa, ab, bb = _dot(a, a), _dot(a, b), _dot(b, b)
        den = aa*bb-ab*ab
        if den < 1e-14: raise ValueError('DEGENERATE_SURFACE')
        da, db = _dot(direction, a), _dot(direction, b)
        ds, du = (da*bb-db*ab)/den, (db*aa-da*ab)/den
        scale = distance
        candidate = dict(anchor)
        for _ in range(6):
            candidate.update(s_m=anchor['s_m']+ds*scale, u=anchor['u']+du*scale)
            length = _norm(_sub(self.point(candidate), f['point']))
            if abs(length-distance) < 1e-10: break
            if length <= 0: raise ValueError('DEGENERATE_SHAPE')
            scale *= distance/length
        return candidate

    def sample_shape(self, defect):
        """Deterministic, surface-bound centerline and tapered boundary samples.

        Length is the sampled surface centerline polyline; widths are transverse
        surface polylines through the center (four subsegments per half-width).
        No world-space offsets are persisted.  The returned strip is the support
        definition for both the appearance mask and geometric opening.
        """
        anchor = deepcopy(defect['anchor']); self._anchor(anchor)
        shape = defect['shape']; length = _finite(shape['length_m'], 'length_m')
        width = _finite(shape['max_width_m'], 'max_width_m')
        if length <= 0 or width <= 0: raise ValueError('Shape dimensions must be positive')
        seed = shape['seed']
        if type(seed) is not int: raise ValueError('seed must be an integer')
        phase = random.Random(seed).uniform(-math.pi, math.pi)
        patch = defect['morphology'] in PATCH_KINDS
        half_count = max(16, math.ceil(length/.025/2))
        if half_count > 1000: raise ValueError('Unsupported shape length')
        spacing = length/(2*half_count)
        def half(sign):
            out = [deepcopy(anchor)]
            for i in range(half_count):
                q = sign*(i+.5)/half_count
                turn = 0. if patch else 4*math.sin(math.pi*q)*math.sin(2*math.pi*q+phase)
                out.append(self._step(out[-1], anchor['theta_deg']+(180 if sign < 0 else 0)+turn, spacing))
            return out
        anchors = list(reversed(half(-1)))[:-1]+half(1)
        centerline = [self.point(a) for a in anchors]
        left, right, la, ra, normals, widths, paths, profile = [], [], [], [], [], [], [], []
        for i, a in enumerate(anchors):
            f = self.frame(a); normals.append(f['normal'])
            tangent = _sub(centerline[min(i+1, len(anchors)-1)], centerline[max(0, i-1)])
            tangent = _unit(_sub(tangent, _mul(f['normal'], _dot(tangent, f['normal']))))
            transverse = _unit(_cross(f['normal'], tangent))
            theta = math.degrees(math.atan2(_dot(transverse, f['e_t']), _dot(transverse, f['e_s'])))
            factor = (patch_width_factor(defect['morphology'], 2*i/(len(anchors)-1)-1, shape)
                      if patch else .025+.975*math.sin(math.pi*i/(len(anchors)-1))**.7)
            profile.append(factor); half_width = width*factor/2
            sides = []
            for sign in (-1, 1):
                current = deepcopy(a); path = [centerline[i]]
                for _ in range(4):
                    current = self._step(current, theta+(180 if sign < 0 else 0), half_width/4)
                    path.append(self.point(current))
                sides.append((current, path))
            la.append(sides[0][0]); ra.append(sides[1][0]); left.append(sides[0][1][-1]); right.append(sides[1][1][-1])
            path = list(reversed(sides[0][1]))+sides[1][1][1:]; paths.append(path)
            widths.append(sum(_norm(_sub(b, a)) for a, b in zip(path, path[1:])))
        measured = sum(_norm(_sub(b, a)) for a, b in zip(centerline, centerline[1:]))
        if abs(measured-length) > DIMENSION_TOLERANCE_M or abs(max(widths)-width) > DIMENSION_TOLERANCE_M:
            raise ValueError('GENERATED_DIMENSION_OUT_OF_TOLERANCE')
        return dict(centerline=centerline, left=left, right=right, anchors=anchors,
            left_anchors=la, right_anchors=ra, normals=normals, widths_m=widths,
            transverse_paths=paths, width_profile=profile, measured_length_m=measured,
            measured_max_width_m=max(widths), nominal_length_m=length, nominal_max_width_m=width,
            support_definition={'kind': 'reference_surface_strip', 'threshold': .5,
                'boundary': 'left/right sampled polylines with flat end caps',
                'length_method': 'surface-bound sampled centerline polyline',
                'width_method': 'transverse surface polyline through centerline'},
            parameterization_version=self.parameterization_version,
            reference_geometry_sha256=self.reference_geometry_sha256)
