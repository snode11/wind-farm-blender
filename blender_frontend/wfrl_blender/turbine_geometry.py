"""NREL 5MW design meshes, using only the packaged asset and Python stdlib.

The loft follows wfrl/viz/geometry.py: reference-point aligned airfoil sections,
structural twist, and interpolation of the 19 AeroDyn stations. Local blade
coordinates are x axial/downstream, y chordwise, z radial. No backend imports.
"""
from __future__ import annotations

from bisect import bisect_right
from functools import lru_cache
import json
import math
from pathlib import Path


@lru_cache(maxsize=1)
def geometry_data():
    return json.loads((Path(__file__).parent / "assets/nrel5mw_geometry.json").read_text())


def hub_position():
    """Hub relative to tower ground origin, before nacelle yaw, in metres."""
    scalars = geometry_data()["scalars"]
    tilt = math.radians(-scalars["ShftTilt"])
    return (scalars["OverHang"] * math.cos(tilt), 0.0,
            scalars["TowerHt"] - scalars["OverHang"] * math.sin(tilt))


def _resample_ring(points, count):
    """Anchor LE/TE and sample each surface at matching cosine-spaced chords.

    The packaged contours run upper TE -> LE -> lower TE. The single seam
    vertex is the TE midpoint; finite trailing-edge thickness is retained at
    the adjacent samples without duplicate vertices on sharp-edge profiles.
    """
    if count < 12 or count % 2:
        raise ValueError("Airfoil sampling requires an even count >= 12")
    points = [tuple(p) for p in points]
    leading = min(range(len(points)), key=lambda i: points[i][0])
    upper, lower = list(reversed(points[:leading + 1])), points[leading:]
    def at_x(surface, x):
        xs = [p[0] for p in surface]
        j = max(0, min(bisect_right(xs, x) - 1, len(xs) - 2))
        a, b = surface[j], surface[j + 1]
        t = (x - a[0]) / (b[0] - a[0])
        return (x, a[1] + t * (b[1] - a[1]))
    xmin, xmax = points[leading][0], max(p[0] for p in points)
    half = count // 2
    ring = []
    for i in range(count):
        x = xmin + (xmax - xmin) * .5 * (1 + math.cos(math.tau * i / count))
        ring.append(at_x(upper if i <= half else lower, x))
    ring[0] = (xmax, .5 * (points[0][1] + points[-1][1]))
    ring[half] = points[leading]
    return ring


def _ring_faces(rings, count):
    faces = []
    for ring in range(rings - 1):
        a, b = ring * count, (ring + 1) * count
        for j in range(count):
            k = (j + 1) % count
            faces.append((a + j, a + k, b + k, b + j))
    faces.extend((tuple(reversed(range(count))), tuple(range((rings - 1) * count, rings * count))))
    return faces


@lru_cache(maxsize=4)
def blade_mesh(subdiv=4, ring_points=96):
    """Return vertices/faces: 73 interpolated rings with curved airfoil contours."""
    if subdiv < 1 or ring_points < 12:
        raise ValueError("Blade loft needs positive subdivision and at least 12 outline points")
    data = geometry_data()
    sections = []
    for span, curve, sweep, _, twist, chord, afid in data["blade_stations"]:
        airfoil = data["airfoils"][int(afid) - 1]
        ref = airfoil["reference"]
        beta = math.radians(twist)
        ring = []
        for x, y in _resample_ring(airfoil["coordinates"], ring_points):
            u, v = (x - ref[0]) * chord, (y - ref[1]) * chord
            tangent = u * math.cos(beta) + v * math.sin(beta)
            axial = -u * math.sin(beta) + v * math.cos(beta)
            ring.append((curve + axial, sweep + tangent, data["scalars"]["HubRad"] + span))
        sections.append(ring)
    vertices = []
    for i in range(len(sections) - 1):
        for j in range(subdiv + (i == len(sections) - 2)):
            t = j / subdiv
            vertices.extend(tuple(a + (b - a) * t for a, b in zip(p, q))
                            for p, q in zip(sections[i], sections[i + 1]))
    # Swapping airfoil (chord, thickness) into (axial, tangent) reverses winding.
    faces = _ring_faces(len(vertices) // ring_points, ring_points)
    return vertices, [tuple(reversed(face)) for face in faces]


def tower_mesh(ring_points=64):
    stations = geometry_data()["tower_stations"]
    vertices = [(diameter * 0.5 * math.cos(2 * math.pi * i / ring_points),
                 diameter * 0.5 * math.sin(2 * math.pi * i / ring_points), elevation)
                for elevation, diameter in stations for i in range(ring_points)]
    return vertices, _ring_faces(len(stations), ring_points)
