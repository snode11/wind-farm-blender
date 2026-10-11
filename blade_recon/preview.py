"""
preview.py —— 不依赖 Blender 的三维重建预览视频(第三人称视角)。

绿色 = 图像观测约束的截面，橙色 = 模型推断；--truth 给出时叠加真值叶片轮廓(白线)。
    python preview.py --recon out/synth20/recon.json --truth data/synth20/truth.json \
                      --out out/synth20/preview.mp4
"""
import argparse
import math

import cv2
import numpy as np

from camera import Camera, rot_axis
from model import Rotor, TurbineConfig, load_json

GREEN, ORANGE = np.array([60, 190, 70]), np.array([30, 140, 255])     # BGR


def viewer(rotor, W=960, H=720, dist=210.0, az=-35.0, el=12.0):
    """绕轮毂的观察相机：az 方位(从上风正面起算，负=偏向左侧)，el 仰角。"""
    tgt = rotor.hub
    d = rot_axis([0, 0, 1], math.radians(az)) @ np.array([1.0, 0, 0])
    d = d * math.cos(math.radians(el)) + np.array([0, 0, math.sin(math.radians(el))])
    C = tgt + dist * d
    fwd = (tgt - C) / np.linalg.norm(tgt - C)
    right = np.cross(fwd, [0, 0, 1.0]); right /= np.linalg.norm(right)
    down = np.cross(fwd, right)
    R = np.stack([right, down, fwd]); T = np.eye(4); T[:3, :3] = R; T[:3, 3] = -R @ C
    f = W / (2 * math.tan(math.radians(25)))
    K = np.array([[f, 0, W / 2], [0, f, H / 2], [0, 0, 1]])
    return Camera('view', W, H, K, T)


def draw(cam, rotor, faces, state, seen, light, truth=None):
    img = np.full((cam.H, cam.W, 3), 245, np.uint8)
    # 塔筒、轮毂作参照
    top, base = cam.project(np.array([[0, 0, rotor.cfg.hub_height_m - 2], [0, 0, 0.0]]))[0]
    if np.isfinite(top).all() and np.isfinite(base).all():
        cv2.line(img, tuple(np.int32(top)), tuple(np.int32(base)), (170, 170, 170), 9)
    V, _ = rotor.forward(state)
    N = rotor.cfg.n_ring
    T, Z, C = [], [], []
    for b in range(3):
        v = V[b].reshape(-1, 3)
        uv, z = cam.project(v)
        p0, p1, p2 = v[faces[:, 0]], v[faces[:, 1]], v[faces[:, 2]]
        nrm = np.cross(p1 - p0, p2 - p0); nrm /= np.linalg.norm(nrm, axis=1, keepdims=True) + 1e-12
        lam = 0.45 + 0.55 * np.abs(nrm @ light)
        sec = faces[:, 0] // N
        base_col = np.where(seen[b][np.minimum(sec, len(seen[b]) - 1)][:, None], GREEN, ORANGE)
        ok = np.isfinite(uv[faces]).all((1, 2))
        T.append(uv[faces][ok]); Z.append(z[faces].mean(1)[ok]); C.append((base_col * lam[:, None])[ok])
    T = np.concatenate(T); Z = np.concatenate(Z); C = np.concatenate(C)
    for i in np.argsort(-Z):
        cv2.fillPoly(img, [np.round(T[i] * 4).astype(np.int32)], C[i].tolist(), shift=2)
    hub = cam.project(rotor.hub[None])[0][0]
    if np.isfinite(hub).all():
        cv2.circle(img, tuple(np.int32(hub)), 6, (90, 90, 90), -1)
    if truth is not None:
        Vt, At = rotor.forward(truth)
        for b in range(3):
            for j in (0, N // 2):                                   # 后缘线、前缘线
                pts, _ = cam.project(Vt[b][:, j])
                ok = np.isfinite(pts).all(1)
                if ok.sum() > 1:
                    cv2.polylines(img, [np.round(pts[ok]).astype(np.int32)], False, (40, 40, 40), 1,
                                  cv2.LINE_AA)
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--recon', required=True)
    ap.add_argument('--truth', default=None)
    ap.add_argument('--out', required=True)
    ap.add_argument('--az', type=float, default=-35.0)
    ap.add_argument('--el', type=float, default=12.0)
    args = ap.parse_args()
    R = load_json(args.recon)
    rotor = Rotor(TurbineConfig.from_dict(R['turbine']))
    faces = rotor.tpl.faces()
    truth = {f['frame']: np.array(f['state']) for f in load_json(args.truth)['frames']} if args.truth else None
    cam = viewer(rotor, az=args.az, el=args.el)
    light = np.array([0.4, -0.4, 0.8]); light /= np.linalg.norm(light)
    w = cv2.VideoWriter(args.out, cv2.VideoWriter_fourcc(*'mp4v'), R.get('fps', 10), (cam.W, cam.H))
    for f in R['frames']:
        st = np.array(f['state'])
        tr = None
        if truth is not None:
            # 叶片编号按方位角对齐到重建(三片叶片外形相同，编号可循环)
            t0 = truth[f['frame']]
            m = int(np.argmin([abs((st[0] - t0[0] - 120 * k + 180) % 360 - 180) for k in range(3)]))
            tr = t0.copy(); tr[0] = t0[0] + 120 * m
            for g in range(4):
                tr[1 + 3 * g:4 + 3 * g] = np.roll(t0[1 + 3 * g:4 + 3 * g], -m)
        img = draw(cam, rotor, faces, st, np.array(f['observed_sections'], bool), light, tr)
        cv2.putText(img, 't=%.1fs  rpm=%.1f  flap[m]=%s' % (f['t'], f['rpm'], np.round(st[4:7], 2)),
                    (12, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (30, 30, 30), 1, cv2.LINE_AA)
        cv2.putText(img, 'green=observed  orange=inferred' + ('  dark line=truth' if tr is not None else ''),
                    (12, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (30, 30, 30), 1, cv2.LINE_AA)
        w.write(img)
    w.release()
    print('wrote', args.out)


if __name__ == '__main__':
    main()
