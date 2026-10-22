"""Replicate aim_gimbal's exact rotation (no Blender) to pick yaw/pitch/roll.

aim_gimbal does:
  orientation = Euler((90+pitch, 0, yaw-90) deg, 'XYZ').to_quaternion()
  final       = orientation @ Quaternion(Z, roll)
Blender camera looks along local -Z, image-up is local +Y. This script reports
the resulting world view direction and up vector so the mount aim can be chosen
to point down the nacelle at the blade without a convention mismatch.
"""
import math

import numpy as np


def _axis_quat(axis, angle):
    a = np.array(axis, float)
    a = a / np.linalg.norm(a)
    s = math.sin(angle / 2)
    return np.array([math.cos(angle / 2), a[0] * s, a[1] * s, a[2] * s])


def _qmul(p, q):
    w0, x0, y0, z0 = p
    w1, x1, y1, z1 = q
    return np.array([
        w0 * w1 - x0 * x1 - y0 * y1 - z0 * z1,
        w0 * x1 + x0 * w1 + y0 * z1 - z0 * y1,
        w0 * y1 - x0 * z1 + y0 * w1 + z0 * x1,
        w0 * z1 + x0 * y1 - y0 * x1 + z0 * w1,
    ])


def _rotate(q, v):
    qv = np.array([0.0, *v])
    qc = np.array([q[0], -q[1], -q[2], -q[3]])
    return _qmul(_qmul(q, qv), qc)[1:]


def euler_xyz_quat(rx, ry, rz):
    # Blender Euler 'XYZ' to_quaternion: qx @ qy @ qz
    return _qmul(_qmul(_axis_quat((1, 0, 0), rx), _axis_quat((0, 1, 0), ry)),
                 _axis_quat((0, 0, 1), rz))


def aim(yaw, pitch, roll):
    orientation = euler_xyz_quat(math.radians(90 + pitch), 0.0, math.radians(yaw - 90))
    final = _qmul(orientation, _axis_quat((0, 0, 1), math.radians(roll)))
    view = _rotate(final, (0, 0, -1))   # camera looks down local -Z
    up = _rotate(final, (0, 1, 0))      # image up is local +Y
    return view, up


if __name__ == "__main__":
    # Want: view direction mostly -Z (down), leaning slightly toward -X (hub),
    # so the blade shoulder at -X is centred. Check candidates.
    for yaw in (90, 180, 270):
        for pitch in (-85, -80):
            v, u = aim(yaw, pitch, 0.0)
            print(f"yaw={yaw} pitch={pitch} roll=0 : view={[round(x,3) for x in v]} up={[round(x,3) for x in u]}")
