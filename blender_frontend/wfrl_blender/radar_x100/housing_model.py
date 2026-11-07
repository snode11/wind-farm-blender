"""Display model of the installed radar unit: finned housing, bracket, glands, cables, mounting plate.

Sensor axes: +X boresight, +Y left, +Z up; the unit envelope is centred on the sensor origin.
The housing-plus-bracket envelope is the user-supplied installed size; mounting plate, slots and
cable routing are estimated from the installation photo. The mesh is display only: it is not
sampled, does not occlude radar rays and is not a manufacturer CAD model.
"""
import math
import numpy as np
from .core import installation

# name, RGBA, metallic, roughness
MATERIALS = (
    ('WFRL.X100.Aluminium', (.78, .80, .82, 1.), .5, .35),
    ('WFRL.X100.Radome', (.20, .21, .22, 1.), 0., .55),
    ('WFRL.X100.Bracket', (.33, .35, .37, 1.), .2, .6),
    ('WFRL.X100.Label', (.92, .92, .90, 1.), 0., .7),
    ('WFRL.X100.Gland', (.70, .72, .74, 1.), .4, .4),
    ('WFRL.X100.Plate', (.74, .76, .76, 1.), .5, .45),
    ('WFRL.X100.Cable', (.03, .03, .03, 1.), 0., .5),
    ('WFRL.X100.Hole', (.02, .02, .02, 1.), 0., .9),
)
ALUMINIUM, RADOME, BRACKET, LABEL, GLAND, PLATE, CABLE, HOLE = range(len(MATERIALS))
UNIT_MATERIALS = (ALUMINIUM, RADOME, BRACKET, LABEL, GLAND)
SOURCE = ('Installed unit 173 W x 202.5 H x 175.6 D mm (housing with bracket, user supplied); '
          'plate, slots and cables estimated from the installation photo; display only, no CAD')


class _Mesh:
    def __init__(self):
        self.vertices, self.faces, self.materials = [], [], []

    def box(self, lo, hi, material):
        (x0, y0, z0), (x1, y1, z1) = lo, hi
        base = len(self.vertices)
        self.vertices += [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
        for face in ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)):
            self.faces.append(tuple(base + i for i in face))
            self.materials.append(material)

    def tube(self, points, radius, material, sides=12, caps=True):
        """Swept circle along a polyline; parallel-transported frames avoid twisting."""
        points = np.asarray(points, float)
        tangents = np.gradient(points, axis=0)
        tangents /= np.linalg.norm(tangents, axis=1)[:, None]
        helper = np.array((0., 0., 1.)) if abs(tangents[0][2]) < .9 else np.array((1., 0., 0.))
        normal = np.cross(tangents[0], helper)
        normal /= np.linalg.norm(normal)
        base = len(self.vertices)
        angles = np.linspace(0, 2 * math.pi, sides, endpoint=False)
        for point, tangent in zip(points, tangents):
            normal = normal - tangent * (normal @ tangent)
            normal /= np.linalg.norm(normal)
            binormal = np.cross(tangent, normal)
            ring = point + radius * (np.cos(angles)[:, None] * normal + np.sin(angles)[:, None] * binormal)
            self.vertices += [tuple(v) for v in ring]
        for row in range(len(points) - 1):
            for k in range(sides):
                a, b = base + row * sides + k, base + row * sides + (k + 1) % sides
                self.faces.append((a, b, b + sides, a + sides))
                self.materials.append(material)
        if caps:
            self.faces.append(tuple(base + k for k in reversed(range(sides))))
            last = base + (len(points) - 1) * sides
            self.faces.append(tuple(last + k for k in range(sides)))
            self.materials += [material, material]


def _arc_path(points, radius=.03, steps=5):
    """Round polyline corners so cables bend instead of kinking."""
    points = [np.asarray(p, float) for p in points]
    out = [points[0]]
    for prev, corner, nxt in zip(points, points[1:], points[2:]):
        a, b = prev - corner, nxt - corner
        r = min(radius, np.linalg.norm(a) / 2, np.linalg.norm(b) / 2)
        start, end = corner + a / np.linalg.norm(a) * r, corner + b / np.linalg.norm(b) * r
        for t in np.linspace(0, 1, steps):
            out.append((1 - t) ** 2 * start + 2 * (1 - t) * t * corner + t * t * end)
    out.append(points[-1])
    return np.array(out)


def build():
    """Vertices, quads/ngons and material indices in sensor coordinates (metres)."""
    hx, hy, hz = installation.housing_half_extents()
    mesh = _Mesh()
    top = .045          # housing lid height; the bracket yoke fills the space above
    # Radome on the boresight face, finned aluminium body behind it.
    mesh.box((hx - .016, -.076, -hz), (hx, .076, top - .010), RADOME)
    mesh.box((.005, -.080, -hz), (hx - .016, .080, top), ALUMINIUM)
    for y in np.linspace(-.074, .074, 13):
        mesh.box((-.012, y - .00175, -hz + .006), (.005, y + .00175, top - .005), ALUMINIUM)
    mesh.box((-.0126, -.030, .000), (-.0118, .030, .026), LABEL)
    # Bracket yoke: two cheeks bolted to the plate and a top bar; the finned back stays visible.
    for sign in (-1, 1):
        mesh.box((-hx, sign * hy - (sign > 0) * .005, -.020),
                 (.030, sign * hy + (sign < 0) * .005, hz), BRACKET)
    mesh.box((-hx, -hy, hz - .009), (.030, hy, hz), BRACKET)
    # Two cable glands on the lid; cables rise behind the bracket bar and leave upwards along the plate.
    for y in (-.028, .028):
        z = np.linspace(top, top + .022, 2)
        mesh.tube([(.040, y, z[0]), (.040, y, z[1])], .010, GLAND, sides=6)
        mesh.tube([(.040, y, top + .006), (.040, y, top + .013)], .013, GLAND, sides=6)
        path = _arc_path([(.040, y, top + .020), (.040, y, hz + .030), (-hx + .030, y * 1.4, hz + .030),
                          (-hx + .030, y * 2.2, hz + .30)], radius=.035)
        mesh.tube(path, .0065, CABLE)
    # Galvanised mounting plate behind the unit with a window, corner slots and bolt holes.
    x0, x1 = -hx - .006, -hx
    width, low, high, window = .22, -.175, .205, (.120, -.135, .090)
    wy, wz0, wz1 = window
    mesh.box((x0, -width, low), (x1, -wy, high), PLATE)
    mesh.box((x0, wy, low), (x1, width, high), PLATE)
    mesh.box((x0, -wy, wz1), (x1, wy, high), PLATE)
    mesh.box((x0, -wy, low), (x1, wy, wz0), PLATE)
    for y in (-.19, .19):
        for z in (low + .035, high - .035):
            mesh.box((x0 - .0004, y - .007, z - .017), (x1 + .0004, y + .007, z + .017), HOLE)
    for y, z in ((-.16, .14), (.16, .14), (-.16, -.15), (.16, -.15), (0., .16), (0., -.157)):
        mesh.box((x0 - .0004, y - .004, z - .004), (x1 + .0004, y + .004, z + .004), HOLE)
    return np.array(mesh.vertices), mesh.faces, np.array(mesh.materials)


def unit_bounds(vertices, faces, materials):
    """Bounds of housing and bracket faces, excluding plate, holes and cables."""
    used = sorted({i for face, m in zip(faces, materials) if m in UNIT_MATERIALS for i in face})
    points = np.asarray(vertices)[used]
    return points.min(axis=0), points.max(axis=0)
