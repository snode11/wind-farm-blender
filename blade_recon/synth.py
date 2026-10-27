"""
synth.py —— 生成自测用的三路近红外风格视频 + 相机参数 + 真值状态。

本机没有 Blender 渲染出的三摄视频时用它跑通管线；有 Blender 视频后直接用 recon.py，
本文件只用于对照。真值只写进 truth.json，重建器不读。

    python synth.py --out data/synth --seconds 6 --fps 10 --scale 0.25
"""
import argparse
import math
import os

import cv2
import numpy as np

from camera import build_rig
from model import Rotor, TurbineConfig, N_STATE, save_json


def true_state(t, rpm=12.1, pitch=(0.0, 0.0, 0.0), flap_mean=3.5, imb=(0.0, 0.0, 0.0), spin=1):
    """一组物理合理的状态：额定转速，挥舞 = 平均推力弯曲 + 风切变 1P，摆振 = 重力 1P。"""
    s = np.zeros(N_STATE)
    psi = (spin * rpm * 6.0 * t) % 360.0
    s[0] = psi
    for b in range(3):
        pb = math.radians(psi + 120 * b)
        s[1 + b] = pitch[b]
        s[4 + b] = flap_mean + imb[b] + 0.35 * math.cos(pb)       # 上方风大 → 朝上时弯得多
        s[7 + b] = 0.30 * spin * math.sin(pb)                     # 重力摆振随转向变号                            # 叶片水平时重力摆振最大
        s[10 + b] = 0.15 * math.cos(pb)
    return s


def render(cam, rotor, state, faces, light, rng=None, noise=2.0):
    """近红外灰度：天空亮、叶片按朗伯明暗，画家算法(按深度从远到近填三角形)。返回 (img, mask)。"""
    H, W = cam.H, cam.W
    sky = np.linspace(205, 175, H)[:, None].repeat(W, 1)
    img = sky.astype(np.float32)
    mask = np.zeros((H, W), np.uint8)
    V, _ = rotor.forward(state)
    tris, depth, shade = [], [], []
    for b in range(3):
        v = V[b].reshape(-1, 3)
        uv, z = cam.project(v)
        tri = faces
        p0, p1, p2 = v[tri[:, 0]], v[tri[:, 1]], v[tri[:, 2]]
        nrm = np.cross(p1 - p0, p2 - p0)
        nrm /= np.linalg.norm(nrm, axis=1, keepdims=True) + 1e-12
        lam = 0.35 + 0.65 * np.abs(nrm @ light)
        zt = z[tri].mean(1)
        uvt = uv[tri]
        ok = np.isfinite(uvt).all((1, 2)) & (z[tri] > 0.1).all(1)
        # 粗裁剪：三角形完全在画面外则跳过
        ok &= ~((uvt[..., 0] < 0).all(1) | (uvt[..., 0] >= W).all(1)
                | (uvt[..., 1] < 0).all(1) | (uvt[..., 1] >= H).all(1))
        tris.append(uvt[ok]); depth.append(zt[ok]); shade.append(lam[ok])
    if tris:
        T = np.concatenate(tris); Z = np.concatenate(depth); L = np.concatenate(shade)
        order = np.argsort(-Z)
        for i in order:
            pts = np.round(T[i] * 4).astype(np.int32).reshape(1, 3, 2)
            g = float(35 + 100 * L[i])
            cv2.fillPoly(img, pts, g, lineType=cv2.LINE_8, shift=2)
            cv2.fillPoly(mask, pts, 255, lineType=cv2.LINE_8, shift=2)
    if rng is not None and noise > 0:
        img += rng.normal(0, noise, img.shape).astype(np.float32)
    return np.clip(img, 0, 255).astype(np.uint8), mask


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='data/synth')
    ap.add_argument('--seconds', type=float, default=6.0)
    ap.add_argument('--fps', type=float, default=10.0)
    ap.add_argument('--scale', type=float, default=0.25, help='相对 3264×2548 的缩放，自测用')
    ap.add_argument('--back', type=float, default=6.0)
    ap.add_argument('--down', type=float, default=3.0)
    ap.add_argument('--pitch-down', type=float, default=45.0)
    ap.add_argument('--pitch', type=float, nargs=3, default=[2.0, 2.0, 2.0])
    ap.add_argument('--imb', type=float, nargs=3, default=[0.6, 0.0, 0.0],
                    help='逐叶片额外挥舞(米)，模拟气动不平衡')
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--spin', type=int, default=1, choices=[1, -1], help='转向，见 model.TurbineConfig.spin')
    ap.add_argument('--write-masks', action='store_true',
                    help='同时写真值掩码(仅调试/评分用，重建默认不读)')
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    cfg = TurbineConfig(spin=args.spin)
    rotor = Rotor(cfg)
    cams = build_rig(rotor, args.back, args.down, args.pitch_down)
    cams_s = [c.scaled(args.scale) for c in cams]
    faces = rotor.tpl.faces()
    light = np.array([0.3, -0.5, 0.8]); light /= np.linalg.norm(light)
    rng = np.random.default_rng(args.seed)

    writers = {}
    for c in cams_s:
        if args.write_masks:
            os.makedirs(os.path.join(args.out, 'truth_mask_' + c.name), exist_ok=True)
        writers[c.name] = cv2.VideoWriter(os.path.join(args.out, c.name + '.mp4'),
                                          cv2.VideoWriter_fourcc(*'mp4v'), args.fps,
                                          (c.W, c.H), False)
    n = int(round(args.seconds * args.fps))
    truth = []
    for k in range(n):
        t = k / args.fps
        s = true_state(t, pitch=args.pitch, imb=args.imb, spin=args.spin)
        truth.append({'frame': k, 't': t, 'state': s.tolist()})
        for c in cams_s:
            img, m = render(c, rotor, s, faces, light, rng)
            writers[c.name].write(img)
            if args.write_masks:
                cv2.imwrite(os.path.join(args.out, 'truth_mask_' + c.name, '%06d.png' % k), m)
        if k % 10 == 0:
            print('frame %d/%d' % (k, n))
    for w in writers.values():
        w.release()
    save_json(os.path.join(args.out, 'cameras.json'),
              {'turbine': cfg.to_dict(), 'fps': args.fps,
               'cameras': [c.to_dict() for c in cams_s]})
    save_json(os.path.join(args.out, 'truth.json'),
              {'note': '仅评分使用，重建器不读', 'turbine': cfg.to_dict(), 'frames': truth})
    print('wrote', args.out)


if __name__ == '__main__':
    main()
