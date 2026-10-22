"""Project the downward blade into T1 Down image coordinates (no Blender).

Models the inspection pose (blade at bottom dead centre) in the YawRoot frame
and projects root/mid/tip through a candidate gimbal pose, so the framing
constraints can be checked analytically before rendering:

  - root visible   : root inside the normalised image rect
  - tip visible    : tip inside the rect
  - triangle       : image chord width shrinks root -> tip (foreshortening)
  - tip before centre: tip image-v stays on the root side of the frame centre

Image convention: u right, v up, both in [-1, 1]; centre line is v = 0.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]


def _geometry():
    spec = importlib.util.spec_from_file_location(
        "tg", ROOT / "blender_frontend/wfrl_blender/turbine_geometry.py")
    tg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tg)
    return tg


def blade_points_yawroot(tg, model, n=40):
    """Span samples of the downward blade, in the YawRoot frame (metres)."""
    s = tg.geometry_data(model)["scalars"]
    hub_x, _, hub_z = tg.hub_position()
    hub = np.array([hub_x, 0.0, hub_z - s["TowerHt"]])
    cone = math.radians(abs(s["PreCone(1)"]))
    tilt = math.radians(abs(s["ShftTilt"]))
    radii = np.linspace(s["HubRad"], s["TipRad"], n)
    chords = _chord_profile(tg, model, radii)
    pts, widths = [], []
    for r, c in zip(radii, chords):
        # Blade points down; precone tilts it slightly downwind, shaft tilt too.
        down = r * math.cos(cone)
        out = r * math.sin(cone)
        p = hub + np.array([-out * math.sin(tilt) - 0.0, 0.0, -down])
        p[0] += -out * math.cos(tilt)
        pts.append(p)
        widths.append(c)
    return np.array(pts), np.array(widths)


def _chord_profile(tg, model, radii):
    data = tg.geometry_data(model)
    hub = data["scalars"]["HubRad"]
    stations = [(row[0] + hub, row[5]) for row in data["blade_stations"]]
    sr = [s[0] for s in stations]
    sc = [s[1] for s in stations]
    return np.interp(radii, sr, sc)


def look_at(eye, target, roll_deg):
    fwd = target - eye
    fwd = fwd / np.linalg.norm(fwd)
    world_up = np.array([0.0, 0.0, 1.0])
    right = np.cross(fwd, world_up)
    if np.linalg.norm(right) < 1e-6:
        world_up = np.array([0.0, 1.0, 0.0])
        right = np.cross(fwd, world_up)
    right = right / np.linalg.norm(right)
    up = np.cross(right, fwd)
    roll = math.radians(roll_deg)
    r2 = right * math.cos(roll) + up * math.sin(roll)
    u2 = -right * math.sin(roll) + up * math.cos(roll)
    return np.array([r2, u2, fwd])  # rows: image x, image y, view dir


def project(points, eye, basis, hfov_deg, aspect):
    half_h = math.tan(math.radians(hfov_deg) / 2)
    half_v = half_h / aspect
    out = []
    for p in points:
        rel = p - eye
        x = basis[0] @ rel
        y = basis[1] @ rel
        z = basis[2] @ rel
        if z <= 1e-6:
            out.append((math.nan, math.nan, z))
            continue
        out.append((x / z / half_h, y / z / half_v, z))
    return np.array(out)


def evaluate(model="rootkink", eye=None, target=None, roll=180.0,
             hfov=70.0, aspect=16 / 9):
    tg = _geometry()
    pts, widths = blade_points_yawroot(tg, model)
    eye = np.array(eye)
    target = pts[len(pts) // 2] if target is None else np.array(target)
    basis = look_at(eye, target, roll)
    img = project(pts, eye, basis, hfov, aspect)
    inside = np.all(np.abs(img[:, :2]) <= 1.0, axis=1)
    root_uv, tip_uv = img[0], img[-1]
    # Image chord width proxy: angular size = chord / distance / half_h.
    half_h = math.tan(math.radians(hfov) / 2)
    width_img = widths / img[:, 2] / half_h
    return {
        "root_uv": root_uv[:2], "tip_uv": tip_uv[:2],
        "root_in": bool(inside[0]), "tip_in": bool(inside[-1]),
        "all_in_frac": float(inside.mean()),
        "root_width_img": float(width_img[0]), "tip_width_img": float(width_img[-1]),
        "triangle": bool(width_img[0] > width_img[-1] * 2),
        "tip_v": float(tip_uv[1]), "root_v": float(root_uv[1]),
    }


def _fmt(r):
    out = {}
    for k, v in r.items():
        if isinstance(v, float):
            out[k] = round(v, 3)
        elif isinstance(v, np.ndarray):
            out[k] = [round(float(x), 3) for x in v]
        else:
            out[k] = v
    return out


if __name__ == "__main__":
    import json
    tg = _geometry()
    s = tg.geometry_data("rootkink")["scalars"]
    hub_x, _, hub_z = tg.hub_position()
    print("hub rel YawRoot: x", round(hub_x, 2), "z", round(hub_z - s["TowerHt"], 2))
    for eye in [(-5.0, 9.0, 1.5), (-5.0, 12.0, 2.0), (-2.0, 10.0, 3.0)]:
        r = evaluate(eye=eye, target=(-5.0, 0.0, -28.0), roll=180.0, hfov=72.0)
        print("eye", eye, "->", json.dumps(_fmt(r), ensure_ascii=False))
