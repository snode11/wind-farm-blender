"""Sweep T1 Down camera poses for the four framing constraints.

Finds eye/target/fov where: root in frame, tip in frame, blade reads as a
triangle (root chord >> tip chord), and the tip stays on the root side of the
image centre line (|tip_v| < |root_v| and same handedness).
"""
import itertools
import math

import probe_down_framing as P


def score(r):
    if not (r["root_in"] and r["tip_in"] and r["triangle"]):
        return None
    tip_v, root_v = r["tip_v"], r["root_v"]
    # tip must be closer to centre (v=0) than root, and not cross to the far side
    if abs(tip_v) >= abs(root_v):
        return None
    if root_v * tip_v < -1e-6:  # opposite sides -> tip crossed centre
        return None
    # prefer root near the frame edge and tip comfortably short of centre
    return abs(root_v) - abs(tip_v)


def main():
    best = None
    eyes_x = [-6, -5, -4, -3, -2]
    eyes_y = [1, 2, 3, 4, 5]
    eyes_z = [-1, 0, 1, 2, 3]
    tgt_z = [-20, -28, -36, -45]
    fovs = [55, 65, 72, 80, 90]
    for ex, ey, ez, tz, fov in itertools.product(eyes_x, eyes_y, eyes_z, tgt_z, fovs):
        r = P.evaluate(eye=(ex, ey, ez), target=(-5.0, 0.0, tz), roll=180.0, hfov=fov)
        s = score(r)
        if s is None:
            continue
        if best is None or s > best[0]:
            best = (s, dict(eye=(ex, ey, ez), target_z=tz, fov=fov), r)
    if best is None:
        print("no pose satisfied all four constraints in this grid")
        return
    s, params, r = best
    print("best params:", params)
    print("root_uv", [round(v, 3) for v in r["root_uv"]],
          "tip_uv", [round(v, 3) for v in r["tip_uv"]])
    print("root_in", r["root_in"], "tip_in", r["tip_in"], "triangle", r["triangle"],
          "all_in_frac", round(r["all_in_frac"], 2))
    print("root_width_img", round(r["root_width_img"], 3),
          "tip_width_img", round(r["tip_width_img"], 3))
    print("tip_v", round(r["tip_v"], 3), "root_v", round(r["root_v"], 3),
          "-> tip closer to centre by", round(s, 3))


if __name__ == "__main__":
    main()
