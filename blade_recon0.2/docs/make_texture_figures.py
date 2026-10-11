"""
make_texture_figures.py —— 《算法说明》第 15 节(表面纹理)的插图，全部来自真实运行结果。

前置(见 README "表面纹理"一节)：
    python synth.py   --out data/synth_tex --seconds 12 --fps 10 --scale 1 --texture
    python recon.py   --data data/synth_tex --out out/synth_tex --fit-scale 0.25
    python texture.py --data data/synth_tex --recon out/synth_tex/recon.json
    python eval_texture.py --recon out/synth_tex/recon.json --truth data/synth_tex/truth.json \
           --truth-tex data/synth_tex/truth_texture.npz --fig docs/fig/fig13_defects.png
    python docs/make_texture_figures.py
"""
import os
import sys

import cv2
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from atlas import Atlas  # noqa: E402
from camera import Camera, build_rig  # noqa: E402
from model import Rotor, TurbineConfig, load_json  # noqa: E402
from preview import draw_textured, viewer  # noqa: E402
from raster import render_textured  # noqa: E402
from synth import make_textures, render_exposure, true_state  # noqa: E402

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DengXian']
plt.rcParams['axes.unicode_minus'] = False
FIG = os.path.join(ROOT, 'docs', 'fig')
DATA = os.path.join(ROOT, 'data', 'synth_tex')
OUT = os.path.join(ROOT, 'out', 'synth_tex')
TEX = os.path.join(OUT, 'texture')
LIGHT = np.array([0.3, -0.5, 0.8]) / np.linalg.norm([0.3, -0.5, 0.8])


def save(fig, name):
    fig.savefig(os.path.join(FIG, name), bbox_inches='tight', dpi=130)
    plt.close(fig)
    print('  ', name)


def load_tex(atlas):
    tex, alpha = [], []
    for b in range(3):
        im = cv2.imread(os.path.join(TEX, 'blade%d_tex.png' % (b + 1)), cv2.IMREAD_UNCHANGED)
        tex.append(im[..., 0].astype(np.float32)); alpha.append(im[..., 3].astype(np.float32) / 255)
    return tex, alpha


def read_frame(name, k):
    cap = cv2.VideoCapture(os.path.join(DATA, name + '.mp4'))
    cap.set(cv2.CAP_PROP_POS_FRAMES, k)
    ok, im = cap.read(); cap.release()
    return cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)


def defect_pixel(rotor, atlas, cam, state, d):
    V, _ = rotor.forward(state)
    row = int(round(atlas.row_of_r(d['r']))); col = int(round(atlas.col_of_c(d['r'], d['c'])))
    P = atlas.surface(V[d['blade']], slice(row, row + 1))[0, col]
    uv, _ = cam.project(P[None])
    return uv[0]


# ── 图 12 展向压缩：分辨率沿展向 ────────────────────────────────
def fig_resolution():
    tj = load_json(os.path.join(TEX, 'texture.json'))
    fig, axs = plt.subplots(1, 2, figsize=(13, 3.8), gridspec_kw={'width_ratios': [1.1, 1]})
    for b, col in zip(tj['blades'], ['#D62728', '#2CA02C', '#1F77B4']):
        bands = [x for x in b['bands'] if x['res_span_cm_median']]
        r = [np.mean(x['r_m']) for x in bands]
        axs[0].plot(r, [x['res_span_cm_median'] for x in bands], 'o-', color=col, ms=3,
                    label='叶片 %d 展向' % b['blade'])
        axs[0].plot(r, [x['res_chord_cm_median'] for x in bands], 's--', color=col, ms=3, alpha=0.6,
                    label='叶片 %d 弦向' % b['blade'])
    axs[0].set_xlabel('展向半径 r (m)'); axs[0].set_ylabel('最佳视角的分辨率 (cm/像素)')
    axs[0].set_title('原分辨率 3264×2548，下俯 45°：展向比弦向粗 5~8 倍'); axs[0].grid(alpha=.3)
    axs[0].legend(fontsize=7, ncol=2)
    im = cv2.imread(os.path.join(TEX, 'blade1_res.png'))[..., ::-1]
    axs[1].imshow(np.rot90(im), aspect='auto')
    axs[1].set_title('叶片 1 图集：最佳视角展向分辨率(红 <2 cm/px，蓝 ≥20 cm/px，灰 = 从未看清)', fontsize=9)
    axs[1].set_xlabel('叶根 → 叶尖'); axs[1].set_ylabel('后缘 → 迎风面 → 前缘 → 背风面 → 后缘')
    axs[1].set_xticks([]); axs[1].set_yticks([])
    save(fig, 'fig12_resolution.png')


# ── 图 14 同一相机下：原图 vs 带纹理的重建模型重投影 ─────────────
def fig_side_by_side(rotor, atlas, cams, R, T):
    tex, alpha = load_tex(atlas)
    d1 = [d for d in T['defects'] if d['id'] == 'D3'][0]
    best = None
    for f in R['frames']:
        st = np.array(f['state'])
        for c in cams:
            uv = defect_pixel(rotor, atlas, c, st, d1)
            if np.isfinite(uv).all() and 200 < uv[0] < c.W - 200 and 200 < uv[1] < c.H - 200:
                seen = np.array(f['observed_sections'], bool)[0]
                if seen[int(np.argmin(abs(rotor.tpl.r - 57)))]:
                    best = (f, c, uv); break
        if best:
            break
    f, c, uv = best
    st = np.array(f['state'])
    orig = read_frame(c.name, f['frame']).astype(np.float32)
    # 用重建位姿 + 重建纹理(当作反照率)重新渲染同一相机
    A = [t / 150.0 * 0.95 for t in tex]
    re, m, _ = render_textured(c, rotor, st, rotor.tpl.faces(), atlas, A, None, LIGHT)
    hw = 260
    x0, y0 = int(uv[0]) - hw, int(uv[1]) - hw
    sl = (slice(max(y0, 0), y0 + 2 * hw), slice(max(x0, 0), x0 + 2 * hw))
    fig, axs = plt.subplots(1, 3, figsize=(13, 4.6))
    axs[0].imshow(orig[sl], cmap='gray', vmin=60, vmax=200)
    axs[0].set_title('%s 原图 第 %d 帧(局部 %d×%d px)' % (c.name, f['frame'], 2 * hw, 2 * hw))
    axs[1].imshow(re[sl], cmap='gray', vmin=60, vmax=200)
    axs[1].set_title('重建位姿 + 重建纹理，从同一相机重新渲染')
    diff = np.abs(orig[sl] - re[sl]) * (m[sl] > 0)
    axs[2].imshow(diff, cmap='magma', vmin=0, vmax=40)
    axs[2].set_title('差值 |原图 − 重渲染|(0~40 灰度级)')
    for ax in axs:
        ax.set_xticks([]); ax.set_yticks([])
    save(fig, 'fig14_reproject.png')
    return f['frame'], c.name


# ── 图 15 曝光时间与运动模糊 ────────────────────────────────────
def fig_blur(rotor, T):
    cfg = TurbineConfig.from_dict(load_json(os.path.join(DATA, 'cameras.json'))['turbine'])
    rotor = Rotor(cfg)
    cams = [Camera.from_dict(d) for d in load_json(os.path.join(DATA, 'cameras.json'))['cameras']]
    atlas = Atlas(rotor.tpl)
    albedo, height = make_textures(atlas, np.random.default_rng(1000))   # 与 synth.py 默认种子一致
    faces = rotor.tpl.faces()
    d1 = [d for d in T['defects'] if d['id'] == 'D1'][0]
    k, cam = None, None
    for kk in range(10, 40):
        st = true_state(kk / 10.0, pitch=(2, 2, 2), imb=(0.6, 0, 0))
        for c in cams:
            uv = defect_pixel(rotor, atlas, c, st, d1)
            if np.isfinite(uv).all() and 300 < uv[0] < c.W - 300 and 300 < uv[1] < c.H - 300:
                k, cam = kk, c; break
        if k is not None:
            break
    uv = defect_pixel(rotor, atlas, cam, true_state(k / 10.0, pitch=(2, 2, 2), imb=(0.6, 0, 0)), d1)

    def state_at(t):
        return true_state(t, pitch=(2, 2, 2), imb=(0.6, 0, 0))
    hw = 200
    sl = (slice(int(uv[1]) - hw, int(uv[1]) + hw), slice(int(uv[0]) - hw, int(uv[0]) + hw))
    fig, axs = plt.subplots(1, 3, figsize=(13, 4.6))
    for ax, ms in zip(axs, (0.0, 0.2, 1.0)):
        img, _ = render_exposure(cam, rotor, state_at, k / 10.0, faces, atlas, albedo, height, LIGHT, ms / 1000)
        ax.imshow(img[sl], cmap='gray', vmin=60, vmax=200)
        tip_mm = 12.1 * 2 * np.pi / 60 * 57 * ms
        ax.set_title('曝光 %.1f ms：r=57 m 处移动 %.0f mm' % (ms, tip_mm) if ms else '理想瞬时曝光')
        ax.set_xticks([]); ax.set_yticks([])
    fig.suptitle('弦向裂纹 D1(60×1.5 cm)，%s 第 %d 帧局部 400×400 px；运动方向≈弦向，模糊直接抹掉沿弦向的细节' % (cam.name, k),
                 fontsize=10)
    save(fig, 'fig15_blur.png')


# ── 图 16 三维叶片近景(带纹理) ─────────────────────────────────
def fig_closeup(rotor, atlas, R):
    tex, alpha = load_tex(atlas)
    f = R['frames'][30]
    st = np.array(f['state']); seen = np.array(f['observed_sections'], bool)
    _, A = rotor.forward(st)
    i = int(np.argmin(abs(rotor.tpl.r - 55)))
    light = np.array([0.4, -0.4, 0.8]) / np.linalg.norm([0.4, -0.4, 0.8])
    fig, axs = plt.subplots(1, 3, figsize=(15, 4.4))
    for ax, (az, el, dist, fov, ttl) in zip(axs, ((180, -10, 25, 50, '从下风侧看叶片 1 外段'),
                                                 (180, -10, 9, 40, '拉近：r = 51~59 m'),
                                                 (-35, 12, 210, 50, '整机视角'))):
        cam = viewer(rotor, az=az, el=el, dist=dist, fov=fov, target=None if dist > 100 else A[0, i])
        img = draw_textured(cam, rotor, rotor.tpl.faces(), st, seen, light, atlas, tex, alpha)
        ax.imshow(img[..., ::-1]); ax.set_title(ttl); ax.set_xticks([]); ax.set_yticks([])
    fig.suptitle('重建的三维叶片贴上视频反投影纹理：灰色 = 真实像素，绿/橙 = 从未看清(证据色)', fontsize=10)
    save(fig, 'fig16_closeup.png')


def main():
    meta = load_json(os.path.join(DATA, 'cameras.json'))
    R = load_json(os.path.join(OUT, 'recon.json'))
    T = load_json(os.path.join(DATA, 'truth.json'))
    rotor = Rotor(TurbineConfig.from_dict(R['turbine']))
    cams = [Camera.from_dict(d) for d in meta['cameras']]
    tj = load_json(os.path.join(TEX, 'texture.json'))
    atlas = Atlas(rotor.tpl, dr=tj['atlas']['dr'], n_cols=tj['atlas']['W'])
    print('生成纹理插图 →', FIG)
    fig_resolution()
    fig_side_by_side(rotor, atlas, cams, R, T)
    fig_closeup(rotor, atlas, R)
    fig_blur(rotor, T)


if __name__ == '__main__':
    main()
