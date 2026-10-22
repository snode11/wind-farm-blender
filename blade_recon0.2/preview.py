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

from atlas import Atlas
from camera import Camera, rot_axis
from model import Rotor, TurbineConfig, load_json
from raster import rasterize, texture_lookup, vertex_frames

GREEN, ORANGE = np.array([60, 190, 70]), np.array([30, 140, 255])     # BGR


def viewer(rotor, W=960, H=720, dist=210.0, az=-35.0, el=12.0, target=None, fov=50.0):
    """绕目标点(默认轮毂)的观察相机：az 方位(从上风正面起算，负=偏向左侧)，el 仰角。"""
    tgt = rotor.hub if target is None else np.asarray(target, float)
    d = rot_axis([0, 0, 1], math.radians(az)) @ np.array([1.0, 0, 0])
    d = d * math.cos(math.radians(el)) + np.array([0, 0, math.sin(math.radians(el))])
    C = tgt + dist * d
    fwd = (tgt - C) / np.linalg.norm(tgt - C)
    right = np.cross(fwd, [0, 0, 1.0]); right /= np.linalg.norm(right)
    down = np.cross(fwd, right)
    R = np.stack([right, down, fwd]); T = np.eye(4); T[:3, :3] = R; T[:3, 3] = -R @ C
    f = W / (2 * math.tan(math.radians(fov / 2)))
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


def draw_textured(cam, rotor, faces, state, seen, light, atlas, tex, alpha):
    """纹理版：看到过的纹素用重建纹理(×朗伯明暗)，没看到过的用 绿/橙 证据色。"""
    img = np.full((cam.H, cam.W, 3), 245, np.float32)
    V, _ = rotor.forward(state)
    nrm = np.stack([vertex_frames(V[b])[0] for b in range(3)])
    R = rasterize(cam, V, faces, normals=nrm)
    if len(R.ys) == 0:
        return img.astype(np.uint8)
    g, rc = texture_lookup(cam, V, faces, atlas, R, tex, aa=2)
    a, _ = texture_lookup(cam, V, faces, atlas, R, alpha, aa=1)
    corner = np.stack([nrm[b].reshape(-1, 3)[faces] for b in range(3)])
    n = R.interp(corner)
    n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-12
    lam = 0.45 + 0.55 * np.abs(n @ light)
    N = rotor.cfg.n_ring
    sec = np.minimum(faces[R.face, 0] // N, len(seen[0]) - 1)
    ev = np.where(seen[R.blade, sec][:, None], GREEN, ORANGE).astype(np.float32)
    tg = np.repeat(g[:, None], 3, 1) * 1.1
    a = np.clip(a, 0, 1)[:, None]
    col = (a * tg + (1 - a) * ev) * lam[:, None]
    img[R.ys, R.xs] = col
    return np.clip(img, 0, 255).astype(np.uint8)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--recon', required=True)
    ap.add_argument('--truth', default=None)
    ap.add_argument('--out', required=True)
    ap.add_argument('--az', type=float, default=-35.0)
    ap.add_argument('--el', type=float, default=12.0)
    ap.add_argument('--dist', type=float, default=210.0)
    ap.add_argument('--texture', default=None, help='texture.py 输出目录')
    ap.add_argument('--follow-blade', type=int, default=0,
                    help='1~3：观察相机对准该叶片 r=55 m 处并跟着转(看纹理细节用，配合 --dist 20~40)')
    ap.add_argument('--frames', type=int, nargs=2, default=None, help='只输出 [起, 止) 帧')
    args = ap.parse_args()
    R = load_json(args.recon)
    rotor = Rotor(TurbineConfig.from_dict(R['turbine']))
    faces = rotor.tpl.faces()
    truth = {f['frame']: np.array(f['state']) for f in load_json(args.truth)['frames']} if args.truth else None
    cam = viewer(rotor, az=args.az, el=args.el, dist=args.dist)
    light = np.array([0.4, -0.4, 0.8]); light /= np.linalg.norm(light)
    atlas = tex = alpha = None
    if args.texture:
        atlas = Atlas(rotor.tpl, **_atlas_args(args.texture))
        tex, alpha = [], []
        for b in range(3):
            im = cv2.imread('%s/blade%d_tex.png' % (args.texture, b + 1), cv2.IMREAD_UNCHANGED)
            tex.append(im[..., 0].astype(np.float32)); alpha.append(im[..., 3].astype(np.float32) / 255)
    w = cv2.VideoWriter(args.out, cv2.VideoWriter_fourcc(*'mp4v'), R.get('fps', 10), (cam.W, cam.H))
    frames = R['frames'][slice(*args.frames)] if args.frames else R['frames']
    for f in frames:
        st = np.array(f['state'])
        if args.follow_blade:
            _, A = rotor.forward(st)
            i = int(np.argmin(abs(rotor.tpl.r - 55)))
            cam = viewer(rotor, az=args.az, el=args.el, dist=args.dist, target=A[args.follow_blade - 1, i])
        tr = None
        if truth is not None:
            # 叶片编号按方位角对齐到重建(三片叶片外形相同，编号可循环)
            t0 = truth[f['frame']]
            m = int(np.argmin([abs((st[0] - t0[0] - 120 * k + 180) % 360 - 180) for k in range(3)]))
            tr = t0.copy(); tr[0] = t0[0] + 120 * m
            for g in range(4):
                tr[1 + 3 * g:4 + 3 * g] = np.roll(t0[1 + 3 * g:4 + 3 * g], -m)
        seen = np.array(f['observed_sections'], bool)
        if tex is not None:
            img = draw_textured(cam, rotor, faces, st, seen, light, atlas, tex, alpha)
        else:
            img = draw(cam, rotor, faces, st, seen, light, tr)
        cv2.putText(img, 't=%.1fs  rpm=%.1f  flap[m]=%s' % (f['t'], f['rpm'], np.round(st[4:7], 2)),
                    (12, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (30, 30, 30), 1, cv2.LINE_AA)
        cv2.putText(img, 'green=observed  orange=inferred' + ('  dark line=truth' if tr is not None else ''),
                    (12, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (30, 30, 30), 1, cv2.LINE_AA)
        w.write(img)
    w.release()
    print('wrote', args.out)


def _atlas_args(tex_dir):
    m = load_json('%s/texture.json' % tex_dir)['atlas']
    return {'dr': m['dr'], 'n_cols': m['W']}


if __name__ == '__main__':
    main()
