"""
synth.py —— 生成自测用的三路近红外风格视频 + 相机参数 + 真值状态。

本机没有 Blender 渲染出的三摄视频时用它跑通管线；有 Blender 视频后直接用 recon.py，
本文件只用于对照。真值只写进 truth.json，重建器不读。

    python synth.py --out data/synth --seconds 6 --fps 10 --scale 0.25
带表面纹理与已知缺陷(裂纹/修补块/鼓包/前缘腐蚀)，原分辨率，用于验证纹理反投影：
    python synth.py --out data/synth_tex --seconds 20 --fps 10 --scale 1 --texture
缺陷真值写入 truth.json 的 "defects"，真值纹理写入 truth_texture.npz，重建器都不读。
"""
import argparse
import json
import math
import os
import shutil
import subprocess

import cv2
import numpy as np

from atlas import Atlas
from camera import build_rig
from model import Rotor, TurbineConfig, N_STATE, save_json
from raster import render_textured


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


# ───────────────────────── 表面纹理与缺陷 ─────────────────────────
# 位置用表面坐标：r = 展向半径(m)，c = 从前缘量起的表面弧长(m)，正 = 吸力面(背风面，相机能看到的一面)。
# 尺寸：span = 展向长度，chord = 沿表面弦向长度。全部写进 truth.json。
DEFECTS = [
    {'id': 'D1', 'blade': 0, 'type': 'crack', 'name': '弦向裂纹 60×1.5 cm', 'r': 57.0, 'c': 0.70,
     'span': 0.015, 'chord': 0.60, 'albedo': 0.30},
    {'id': 'D2', 'blade': 0, 'type': 'crack', 'name': '展向裂纹 60×1.5 cm', 'r': 51.3, 'c': 0.80,
     'span': 0.60, 'chord': 0.015, 'albedo': 0.30},
    {'id': 'D3', 'blade': 0, 'type': 'patch', 'name': '修补块 40×30 cm(新漆，偏亮)', 'r': 59.2, 'c': 0.45,
     'span': 0.40, 'chord': 0.30, 'albedo': 1.30},
    {'id': 'D4', 'blade': 0, 'type': 'bump', 'name': '鼓包 50×50 cm 高 2 cm(只改变明暗)', 'r': 54.0, 'c': 1.00,
     'span': 0.50, 'chord': 0.50, 'height': 0.02},
    {'id': 'D5', 'blade': 0, 'type': 'erosion', 'name': '前缘腐蚀 4 m', 'r': 60.5, 'c': 0.0,
     'span': 4.0, 'chord': 0.16, 'albedo': 0.55},
    {'id': 'D6', 'blade': 1, 'type': 'patch', 'name': '修补块 50×40 cm(偏暗)', 'r': 45.0, 'c': 0.60,
     'span': 0.50, 'chord': 0.40, 'albedo': 0.60},
]


def make_textures(atlas, rng, defects=DEFECTS):
    """三片叶片的反照率图集(≈1，带低频脏污与细颗粒)和高度图(米)。"""
    H, W = atlas.H, atlas.W
    rr = atlas.r_rows[:, None]
    cc = atlas.c_grid
    albedo, height = [], [None, None, None]
    for b in range(3):
        low = cv2.resize(rng.normal(0, 1, (max(H // 150, 2), max(W // 64, 2))).astype(np.float32),
                         (W, H), interpolation=cv2.INTER_CUBIC)
        fine = cv2.GaussianBlur(rng.normal(0, 1, (H, W)).astype(np.float32), (0, 0), 1.0)
        albedo.append(0.95 + 0.04 * low + 0.02 * fine)
    for d in defects:
        b = d['blade']
        A = albedo[b]
        dr_, dc = rr - d['r'], cc - d['c']
        if d['type'] in ('crack', 'patch'):
            # 软边矩形，边缘 1 cm 过渡；修补块边缘略不规则
            jit = 0.0
            if d['type'] == 'patch':
                jit = 0.015 * cv2.GaussianBlur(rng.normal(0, 1, (H, W)).astype(np.float32), (0, 0), 3.0)
            # 展向按纹素面积覆盖率(裂纹宽 1.5 cm < 纹素 2.5 cm，点采样会整条丢掉)；弦向 1 cm 软边
            h = atlas.dr / 2
            m_r = np.clip((np.minimum(dr_ + h, d['span'] / 2 + jit) - np.maximum(dr_ - h, -d['span'] / 2 - jit))
                          / atlas.dr, 0, 1)
            m = m_r * np.clip(0.5 + (d['chord'] / 2 - np.abs(dc) + jit) / 0.01, 0, 1)
            A[:] = A * (1 - m) + d['albedo'] * m * (A / 0.95)
        elif d['type'] == 'erosion':
            band = np.exp(-0.5 * (dc / (d['chord'] / 2)) ** 2) * (np.abs(dr_) < d['span'] / 2)
            spk = cv2.GaussianBlur(rng.random((H, W)).astype(np.float32), (0, 0), 1.5)
            spk = (spk > np.percentile(spk, 55)).astype(np.float32)
            m = band * spk
            A[:] = A * (1 - m) + d['albedo'] * m
        elif d['type'] == 'bump':
            s_r, s_c = d['span'] / 4, d['chord'] / 4      # 2σ = 半宽
            h = d['height'] * np.exp(-0.5 * ((dr_ / s_r) ** 2 + (dc / s_c) ** 2))
            height[b] = h if height[b] is None else height[b] + h
    return [a.astype(np.float32) for a in albedo], height


class VideoOut:
    """原分辨率时用 ffmpeg 输出高质量 H.264(细裂纹经不起 mp4v 压缩)；没有 ffmpeg 时退回 OpenCV。"""

    def __init__(self, path, W, H, fps, hq):
        self.p = self.w = None
        if hq and shutil.which('ffmpeg'):
            self.p = subprocess.Popen(
                ['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'gray',
                 '-s', '%dx%d' % (W, H), '-r', str(fps), '-i', '-', '-c:v', 'libx264',
                 '-preset', 'medium', '-crf', '12', '-pix_fmt', 'yuv420p', path], stdin=subprocess.PIPE)
        else:
            self.w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*'mp4v'), fps, (W, H), False)

    def write(self, img):
        if self.p is not None:
            self.p.stdin.write(np.ascontiguousarray(img).tobytes())
        else:
            self.w.write(img)

    def release(self):
        if self.p is not None:
            self.p.stdin.close(); self.p.wait()
        else:
            self.w.release()


def render_exposure(cam, rotor, state_at, t, faces, atlas, albedo, height, light, exposure_s, rpm=12.1):
    """曝光时间内的平均(运动模糊)。子帧数按叶尖在像面上的位移取(距离按 40 m 粗估)。"""
    if exposure_s <= 0:
        img, m, _ = render_textured(cam, rotor, state_at(t), faces, atlas, albedo, height, light)
        return img, m
    tip_px = rpm * 2 * math.pi / 60 * rotor.cfg.tip_radius_m * exposure_s * cam.K[0, 0] / 40.0
    k = int(min(16, max(2, math.ceil(tip_px * 1.5))))
    acc, mm = None, None
    for j in range(k):
        tj = t + exposure_s * ((j + 0.5) / k - 0.5)
        img, m, _ = render_textured(cam, rotor, state_at(tj), faces, atlas, albedo, height, light)
        acc = img if acc is None else acc + img
        mm = m if mm is None else np.maximum(mm, m)
    return acc / k, mm


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
    ap.add_argument('--texture', action='store_true', help='带表面纹理和已知缺陷(见 DEFECTS)')
    ap.add_argument('--exposure-ms', type=float, default=0.0, help='曝光时间(ms)，>0 时渲染运动模糊')
    ap.add_argument('--frames', type=int, nargs='*', default=None, help='只渲染这些帧号(出图用，配合 --png)')
    ap.add_argument('--png', action='store_true', help='写 PNG 帧目录而不是 mp4')
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    cfg = TurbineConfig(spin=args.spin)
    rotor = Rotor(cfg)
    cams = build_rig(rotor, args.back, args.down, args.pitch_down)
    cams_s = [c.scaled(args.scale) for c in cams]
    faces = rotor.tpl.faces()
    light = np.array([0.3, -0.5, 0.8]); light /= np.linalg.norm(light)
    rng = np.random.default_rng(args.seed)

    def state_at(t):
        return true_state(t, pitch=args.pitch, imb=args.imb, spin=args.spin)

    if args.texture:
        atlas = Atlas(rotor.tpl)
        albedo, height = make_textures(atlas, np.random.default_rng(args.seed + 1000))
        hgt = np.stack([h if h is not None else np.zeros_like(albedo[0]) for h in height])
        np.savez_compressed(os.path.join(args.out, 'truth_texture.npz'),
                            albedo=np.stack(albedo).astype(np.float16), height=hgt.astype(np.float16),
                            atlas_meta=np.array(json.dumps(atlas.meta(), ensure_ascii=False)))

    writers = {}
    for c in cams_s:
        if args.write_masks:
            os.makedirs(os.path.join(args.out, 'truth_mask_' + c.name), exist_ok=True)
        if args.png:
            os.makedirs(os.path.join(args.out, c.name), exist_ok=True)
        else:
            writers[c.name] = VideoOut(os.path.join(args.out, c.name + '.mp4'), c.W, c.H, args.fps,
                                       args.texture or args.scale >= 0.5)
    n = int(round(args.seconds * args.fps))
    ks = args.frames if args.frames else range(n)
    truth = []
    for k in ks:
        t = k / args.fps
        s = state_at(t)
        truth.append({'frame': k, 't': t, 'state': s.tolist()})
        for c in cams_s:
            if args.texture:
                img, m = render_exposure(c, rotor, state_at, t, faces, atlas, albedo, height, light,
                                         args.exposure_ms / 1000.0)
                img = img + rng.normal(0, 2.0, img.shape).astype(np.float32)
                img = np.clip(img, 0, 255).astype(np.uint8)
            else:
                img, m = render(c, rotor, s, faces, light, rng)
            if args.png:
                cv2.imwrite(os.path.join(args.out, c.name, '%06d.png' % k), img)
            else:
                writers[c.name].write(img)
            if args.write_masks:
                cv2.imwrite(os.path.join(args.out, 'truth_mask_' + c.name, '%06d.png' % k), m)
        if k % 10 == 0:
            print('frame %d/%d' % (k, n), flush=True)
    for w in writers.values():
        w.release()
    save_json(os.path.join(args.out, 'cameras.json'),
              {'turbine': cfg.to_dict(), 'fps': args.fps,
               'cameras': [c.to_dict() for c in cams_s]})
    tr = {'note': '仅评分使用，重建器不读', 'frames': truth}
    if args.texture:
        tr['atlas'] = atlas.meta()
        tr['turbine'] = cfg.to_dict()
        tr['defects'] = DEFECTS
        tr['defect_coords'] = 'r = 展向半径(m)；c = 从前缘量起的表面弧长(m)，正 = 吸力面/背风面；blade 为真值编号(0 起)'
        tr['exposure_ms'] = args.exposure_ms
    save_json(os.path.join(args.out, 'truth.json'), tr)
    print('wrote', args.out)


if __name__ == '__main__':
    main()
