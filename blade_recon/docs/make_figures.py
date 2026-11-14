"""
make_figures.py —— 生成《算法说明》里的全部插图（均来自真实运行结果，不是示意画）。

前置：已跑过 README 第 3 节的自测(data/synth20、out/synth20)。
    cd blade_recon && python docs/make_figures.py
输出：docs/fig/*.png
"""
import json
import os
import sys

import cv2
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from camera import Camera, build_rig  # noqa: E402
from coverage import coverage  # noqa: E402
from evaluate import align  # noqa: E402
from model import Rotor, TurbineConfig, load_json  # noqa: E402
from recon import Obs, densify, segment, silhouettes  # noqa: E402

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DengXian']
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['figure.dpi'] = 130
FIG = os.path.join(ROOT, 'docs', 'fig')
os.makedirs(FIG, exist_ok=True)
DATA = os.path.join(ROOT, 'data', 'synth20')
OUT = os.path.join(ROOT, 'out', 'synth20')
C_OBS, C_INF, C_TRU = '#2E8B57', '#F08C28', '#333333'
C_B = ['#D62728', '#2CA02C', '#1F77B4']


def save(fig, name):
    fig.savefig(os.path.join(FIG, name), bbox_inches='tight')
    plt.close(fig)
    print('  ', name)


# ── 图 1 总体流程 ─────────────────────────────────────────────
def fig_pipeline():
    fig, ax = plt.subplots(figsize=(12, 5.2))
    ax.set_xlim(0, 12); ax.set_ylim(0, 5.2); ax.axis('off')

    def box(x, y, w, h, title, body, color):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle='round,pad=0.05,rounding_size=0.12',
                                    fc=color, ec='#555555', lw=1.0))
        ax.text(x + w / 2, y + h - 0.28, title, ha='center', va='top', fontsize=11, weight='bold')
        ax.text(x + w / 2, y + h - 0.68, body, ha='center', va='top', fontsize=8.6, linespacing=1.45)

    def arrow(x0, y0, x1, y1, txt=''):
        ax.annotate('', (x1, y1), (x0, y0), arrowprops=dict(arrowstyle='->', lw=1.4, color='#444444'))
        if txt:
            ax.text((x0 + x1) / 2, (y0 + y1) / 2 + 0.12, txt, ha='center', fontsize=8, color='#444444')

    box(0.1, 2.9, 2.2, 2.1, '① 输入', '三路原图 C1/C2/C3\n各自 K、外参\n(不拼接全景)\n\n先验：叶片设计外形\nElastoDyn 振型', '#EAF2FB')
    box(2.7, 2.9, 2.2, 2.1, '② 观测提取', '叶片分割\n(YOLO 掩码 / 自动阈值)\n→ 轮廓\n→ 距离变换 DT\n→ 轮廓采样点', '#EAF2FB')
    box(5.3, 2.9, 2.6, 2.1, '③ 前向模型', '状态 x(10 维)\n→ 三片叶片网格\n→ 每路投影\n→ 截面包络轮廓\n+ 根/尖端面环', '#FDF3E3')
    box(8.3, 2.9, 3.6, 2.1, '④ 求解', '双向倒角残差(鲁棒)\n+ 时序先验(转速外推)\n+ 周期物理先验\n+ 可观性门控\n有界最小二乘(TRF)', '#FDF3E3')
    box(5.3, 0.25, 2.6, 2.1, '⑥ 输出', 'recon.json\n每帧 x、标准差\n逐截面 观测/推断 标记\n(每帧约 100 字节)', '#EAF7EE')
    box(8.3, 0.25, 3.6, 2.1, '⑤ 物理先验更新', '只用"看清"的叶片：\n挥舞 = F0 + F1·cosψ + o_b\n摆振 = E0 + Es·sinψ + Ec·cosψ\n桨距 = P0 + q_b\n递推最小二乘(遗忘因子)', '#EAF7EE')
    box(0.1, 0.25, 4.8, 2.1, '⑦ 可视化 / 评估', 'Blender：同一前向模型逐帧还原网格\n绿 = 图像观测，橙 = 模型推断\npreview.py：无需 Blender 的三维预览\nevaluate.py：观测区/推断区分开评分', '#EAF7EE')
    arrow(2.3, 3.95, 2.7, 3.95)
    arrow(4.9, 3.95, 5.3, 3.95)
    arrow(7.9, 3.95, 8.3, 3.95, '残差')
    arrow(10.1, 2.9, 10.1, 2.35, 'x(t)')
    arrow(8.3, 1.3, 7.9, 1.3)
    arrow(5.3, 1.3, 4.9, 1.3)
    ax.annotate('', (9.2, 2.9), (9.2, 2.35), arrowprops=dict(arrowstyle='->', lw=1.2, color='#888888',
                                                              connectionstyle='arc3,rad=0'))
    ax.text(9.0, 2.6, '下一帧先验', ha='right', fontsize=8, color='#888888')
    save(fig, 'fig1_pipeline.png')


# ── 图 2 叶片先验模型 ─────────────────────────────────────────
def fig_blade_model(rotor):
    T = rotor.tpl
    fig, axs = plt.subplots(1, 3, figsize=(13, 3.6))
    ax = axs[0]
    ax.plot(T.r, T.chord, 'k-', label='弦长 c (m)')
    ax.plot(T.r, T.twist / 3, 'b--', label='扭角 /3 (°)')
    ax.plot(T.r, T.tc * 5, 'r:', label='相对厚度 t/c ×5')
    ax.set_xlabel('展向半径 r (m)'); ax.set_title('(a) NREL 5MW 外形分布'); ax.legend(fontsize=8); ax.grid(alpha=.3)
    ax = axs[1]
    for i in (0, 12, 20, 39):
        f = T.foils[i]
        ax.plot((f[:, 0] - T.x_pa[i]) * T.chord[i], f[:, 1] * T.chord[i], label='r=%.1f m' % T.r[i])
    ax.set_aspect('equal'); ax.set_title('(b) 截面翼型(圆→DU→NACA64 混合近似)')
    ax.set_xlabel('弦向 (m)，0 = 桨距轴'); ax.legend(fontsize=8); ax.grid(alpha=.3)
    ax = axs[2]
    ax.plot(T.xi, T.phi_f1, label='挥舞 1 阶')
    ax.plot(T.xi, T.phi_f2, label='挥舞 2 阶')
    ax.plot(T.xi, T.phi_e1, label='摆振 1 阶')
    ax.axvline(0.4, color='gray', lw=0.8, ls='--'); ax.text(0.41, 0.85, 'ξ=0.4 外侧', fontsize=8, color='gray')
    ax.set_xlabel('归一化展向 ξ'); ax.set_title('(c) ElastoDyn 振型 φ(ξ)，φ(1)=1'); ax.legend(fontsize=8); ax.grid(alpha=.3)
    save(fig, 'fig2_blade_model.png')


# ── 图 3 状态变量对叶片形状的作用(真实比例) ─────────────────
def fig_state_effect(rotor):
    """叶片置于 3 点钟水平位置(ψ=90°)，分别改变挥舞、摆振、桨距，看叶片怎么动。"""
    fig, axs = plt.subplots(1, 3, figsize=(13, 3.8))
    base = np.zeros(13); base[0] = 90.0
    n, up = rotor.n, rotor.e_up
    lat = np.cross(n, up) * -1.0
    cols = ['#999999', '#1F77B4', '#D62728']
    # (a) 挥舞：俯视(展向 × 上风方向)
    for v, col in zip([0, 4, 8], cols):
        s = base.copy(); s[4] = v
        _, A = rotor.forward(s)
        rel = A[0] - rotor.hub
        axs[0].plot(rel @ lat, rel @ n, color=col, lw=2, label='flap = %d m' % v)
    axs[0].set_title('(a) 挥舞 1 阶：叶尖朝下风(塔筒)弯'); axs[0].set_xlabel('展向 (m)'); axs[0].set_ylabel('沿叶轮轴 (m)，正=上风')
    # (b) 摆振：正视(展向 × 竖直)
    for v, col in zip([-1, 0, 1], cols):
        s = base.copy(); s[7] = v
        _, A = rotor.forward(s)
        rel = A[0] - rotor.hub
        axs[1].plot(rel @ lat, rel @ up, color=col, lw=2, label='edge = %+d m' % v)
    axs[1].set_title('(b) 摆振 1 阶：在旋转平面内弯'); axs[1].set_xlabel('展向 (m)'); axs[1].set_ylabel('竖直 (m)')
    # (c) 桨距：r≈40 m 截面，在 (旋转方向, 叶轮轴) 平面内
    i = int(np.argmin(abs(rotor.tpl.r - 40)))
    for v, col in zip([0, 15, 30], cols):
        s = base.copy(); s[1] = v
        V, A = rotor.forward(s)
        rel = V[0][i] - A[0][i]
        e_rot = -up                                          # ψ=90° 时旋转方向朝下
        axs[2].fill(rel @ e_rot, rel @ n, fc='none', ec=col, lw=2, label='pitch = %d°' % v)
    axs[2].set_title('(c) 桨距：截面绕桨距轴转 (r = %.0f m)' % rotor.tpl.r[i])
    axs[2].set_xlabel('旋转方向 (m)，正=前缘'); axs[2].set_ylabel('沿叶轮轴 (m)')
    for ax in axs:
        ax.set_aspect('equal', adjustable='datalim'); ax.grid(alpha=.3); ax.legend(fontsize=8)
    save(fig, 'fig3_state_effect.png')


# ── 图 4 安装可观性 ───────────────────────────────────────────
def fig_coverage(rotor):
    fig, axs = plt.subplots(1, 3, figsize=(13, 3.6), sharey=True)
    for ax, pd in zip(axs, (0, 45, 60)):
        cams = build_rig(rotor, 6.0, 3.0, pd)
        psis, r, vis, _ = coverage(rotor, cams, n_psi=180)
        ax.imshow(vis, aspect='auto', origin='lower', cmap='Greens',
                  extent=[psis[0], psis[-1], r[0], r[-1]], vmin=0, vmax=1.4)
        far = r[vis.any(1)].max() if vis.any() else 0
        ax.set_title('下俯 %d°：最外可见 r=%.0f m，叶尖可见 %.0f%%' % (pd, far, 100 * vis[-1].mean()), fontsize=10)
        ax.set_xlabel('叶片方位角 ψ (°)，0=竖直向上，180=朝下')
        ax.axvline(180, color='k', lw=0.6, ls=':')
    axs[0].set_ylabel('展向半径 r (m)')
    save(fig, 'fig4_coverage.png')


# ── 图 5 三路输入、分割、模型轮廓叠加 ─────────────────────────
def read_frame(path, k):
    cap = cv2.VideoCapture(path); cap.set(cv2.CAP_PROP_POS_FRAMES, k); ok, im = cap.read(); cap.release()
    return im


def pick_frame(cams, n):
    """挑三路中出现叶片最多(且各路轮廓都较长)的一帧，演示用。"""
    caps = [cv2.VideoCapture(os.path.join(DATA, c.name + '.mp4')) for c in cams]
    best = (-1, 0)
    for k in range(n):
        score = 0
        for cap in caps:
            ok, im = cap.read()
            if not ok:
                continue
            m = segment(cv2.cvtColor(im, cv2.COLOR_BGR2GRAY))
            frac = (m > 0).mean()
            score += (1.0 if 0.01 < frac < 0.6 else 0.0) + frac
        if k >= 50 and score > best[0]:
            best = (score, k)
    for cap in caps:
        cap.release()
    return best[1]


def fig_inputs(rotor, cams, R):
    k = pick_frame(cams, len(R['frames']))
    fig, axs = plt.subplots(3, 3, figsize=(13, 7.6))
    st = np.array(R['frames'][k]['state'])
    for j, c in enumerate(cams):
        im = read_frame(os.path.join(DATA, c.name + '.mp4'), k)
        g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
        m = segment(g)
        o = Obs(m)
        axs[0, j].imshow(g, cmap='gray', vmin=0, vmax=255); axs[0, j].set_title('%s 原图(第 %d 帧)' % (c.name, k))
        axs[1, j].imshow(np.minimum(o.dt, 60), cmap='magma_r', vmin=0, vmax=60)
        if not o.has:
            axs[1, j].text(c.W / 2, c.H / 2, '本帧无叶片', ha='center', va='center', fontsize=12)
        if len(o.pts):
            axs[1, j].plot(o.pts[:, 0], o.pts[:, 1], 'c.', ms=1.5)
        axs[1, j].set_title('轮廓距离变换 DT + 轮廓采样点')
        axs[2, j].imshow(g, cmap='gray', vmin=0, vmax=255)
        sil = silhouettes(rotor, c, st)
        for b in range(3):
            for side in range(2):
                p = sil[b, :, side]; ok = np.isfinite(p).all(1)
                axs[2, j].plot(p[ok, 0], p[ok, 1], '-', color=C_B[b], lw=1.2)
        axs[2, j].set_xlim(0, c.W); axs[2, j].set_ylim(c.H, 0)
        axs[2, j].set_title('重建状态的模型轮廓(彩线)叠加')
    for ax in axs.ravel():
        ax.set_xticks([]); ax.set_yticks([])
    save(fig, 'fig5_inputs_overlay.png')


# ── 图 6 截面轮廓：最外点近似 vs 包络 ─────────────────────────
def sil_naive(rotor, cam, state):
    V, A = rotor.forward(state)
    out = np.full((3, V.shape[1], 2, 2), np.nan)
    for b in range(3):
        uvr, _ = cam.project(V[b]); uva, za = cam.project(A[b])
        d = np.gradient(uva, axis=0); nrm = np.stack([-d[:, 1], d[:, 0]], 1)
        nrm /= np.linalg.norm(nrm, axis=1, keepdims=True) + 1e-9
        s = ((uvr - uva[:, None]) * nrm[:, None]).sum(-1)
        s = np.where(np.isfinite(s), s, 0)
        ar = np.arange(V.shape[1])
        out[b, :, 0] = uvr[ar, s.argmax(1)]; out[b, :, 1] = uvr[ar, s.argmin(1)]
        out[b, ~(za > 0.5)] = np.nan
    return out


def fig_envelope(rotor, cams, T):
    k = 22
    c = cams[1]
    st = np.array(T['frames'][k]['state'])
    im = read_frame(os.path.join(DATA, c.name + '.mp4'), k)
    g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
    o = Obs(segment(g))
    fig, axs = plt.subplots(1, 3, figsize=(13, 4.2), gridspec_kw={'width_ratios': [1, 1, 0.9]})
    res = {}
    for ax, name, fn in ((axs[0], '旧：每截面"最外点"近似', sil_naive),
                         (axs[1], '新：截面投影曲线的包络', silhouettes)):
        sil = densify(fn(rotor, c, st))
        d = o.sample_dt(sil.reshape(-1, 2)); res[name] = d[np.isfinite(d)]
        ax.imshow(g, cmap='gray', vmin=0, vmax=255)
        for b in range(3):
            for side in range(2):
                p = sil[b, :, side]; ok = np.isfinite(p).all(1)
                ax.plot(p[ok, 0], p[ok, 1], 'r-', lw=1)
        ax.set_xlim(0, c.W); ax.set_ylim(c.H, 0); ax.set_xticks([]); ax.set_yticks([])
        ax.set_title('%s\n(真值状态下，模型轮廓→观测轮廓 中位 %.2f px)' % (name, np.median(res[name])), fontsize=10)
    bins = np.linspace(0, 15, 31)
    for (name, d), col in zip(res.items(), ['#D62728', '#2E8B57']):
        axs[2].hist(d, bins=bins, alpha=0.6, color=col, label=name.split('：')[0])
    axs[2].set_xlabel('模型轮廓点到观测轮廓的距离 (px)'); axs[2].set_ylabel('点数')
    axs[2].set_title('近距离叶根视角下两种轮廓的误差'); axs[2].legend()
    save(fig, 'fig6_envelope.png')


# ── 图 7 时间序列：挥舞/摆振/桨距 重建 vs 真值 ─────────────────
def aligned(R, T):
    tf = {f['frame']: np.array(f['state']) for f in T['frames']}
    t, rec, tru, seen_out, psi_err, std = [], [], [], [], [], []
    for f in R['frames']:
        sr = np.array(f['state']); stt = tf[f['frame']]
        m, dpsi = align(sr, stt)
        sa = stt.copy()
        for g in range(4):
            sa[1 + 3 * g:4 + 3 * g] = np.roll(stt[1 + 3 * g:4 + 3 * g], -m)
        seen = np.array(f['observed_sections'], bool)
        t.append(f['t']); rec.append(sr); tru.append(sa); psi_err.append(dpsi)
        seen_out.append(seen[:, len(seen[0]) // 2:].mean(1)); std.append(f['std'])
    return (np.array(t), np.array(rec), np.array(tru), np.array(seen_out),
            np.array(psi_err), np.array(std))


def fig_timeseries(R, T):
    t, rec, tru, so, pe, std = aligned(R, T)
    fig, axs = plt.subplots(4, 1, figsize=(13, 9), sharex=True)
    for b in range(3):
        axs[0].plot(t, tru[:, 4 + b], '-', color=C_B[b], lw=1, alpha=0.6)
        axs[0].plot(t, rec[:, 4 + b], '.', color=C_B[b], ms=3, label='叶片 %d' % (b + 1))
        axs[1].plot(t, tru[:, 7 + b], '-', color=C_B[b], lw=1, alpha=0.6)
        axs[1].plot(t, rec[:, 7 + b], '.', color=C_B[b], ms=3)
        axs[2].plot(t, tru[:, 1 + b], '-', color=C_B[b], lw=1, alpha=0.6)
        axs[2].plot(t, rec[:, 1 + b], '.', color=C_B[b], ms=3)
        vis = so[:, b] >= 0.15
        axs[3].fill_between(t, b + 0.1, b + 0.9, where=vis, color=C_B[b], alpha=0.7, step='mid')
    axs[0].set_ylabel('挥舞 1 阶\n叶尖幅值 (m)'); axs[0].legend(ncol=3, fontsize=8, loc='upper right')
    axs[0].set_title('点 = 重建，线 = 真值（叶片 1 注入 +0.6 m 气动不平衡）')
    axs[1].set_ylabel('摆振 1 阶\n叶尖幅值 (m)')
    axs[2].set_ylabel('桨距 (°)')
    axs[3].set_yticks([0.5, 1.5, 2.5]); axs[3].set_yticklabels(['叶1', '叶2', '叶3'])
    axs[3].set_ylabel('外侧被观测'); axs[3].set_xlabel('时间 (s)')
    axs[3].axvline(t[50], color='k', ls='--', lw=0.8); axs[3].text(t[50] + 0.1, 2.95, '第一圈结束(先验建立)', fontsize=8, va='top')
    for ax in axs:
        ax.grid(alpha=.3)
    save(fig, 'fig7_timeseries.png')


# ── 图 8 误差沿展向：观测区 vs 推断区 ─────────────────────────
def fig_span_error(rotor, R, T):
    tf = {f['frame']: np.array(f['state']) for f in T['frames']}
    S = len(rotor.tpl.r)
    e_obs = [[] for _ in range(S)]; e_inf = [[] for _ in range(S)]
    for f in R['frames'][50:]:
        sr = np.array(f['state']); stt = tf[f['frame']]; m, _ = align(sr, stt)
        sa = stt.copy(); sa[0] = stt[0] + 120 * m
        for g in range(4):
            sa[1 + 3 * g:4 + 3 * g] = np.roll(stt[1 + 3 * g:4 + 3 * g], -m)
        Vr, _ = rotor.forward(sr); Vt, _ = rotor.forward(sa)
        err = np.linalg.norm(Vr - Vt, axis=-1).mean(-1)
        seen = np.array(f['observed_sections'], bool)
        for b in range(3):
            for i in range(S):
                (e_obs if seen[b, i] else e_inf)[i].append(err[b, i])
    r = rotor.tpl.r
    mo = np.array([np.mean(x) if x else np.nan for x in e_obs])
    mi = np.array([np.mean(x) if x else np.nan for x in e_inf])
    pi = np.array([np.percentile(x, 95) if x else np.nan for x in e_inf])
    fo = np.array([len(a) / max(len(a) + len(b), 1) for a, b in zip(e_obs, e_inf)])
    fig, ax = plt.subplots(figsize=(11, 3.8))
    ax.plot(r, 100 * mo, 'o-', color=C_OBS, ms=3, label='观测区 平均')
    ax.plot(r, 100 * mi, 's-', color=C_INF, ms=3, label='推断区 平均')
    ax.plot(r, 100 * pi, ':', color=C_INF, label='推断区 P95')
    ax.set_xlabel('展向半径 r (m)'); ax.set_ylabel('截面网格误差 (cm)'); ax.grid(alpha=.3)
    ax2 = ax.twinx(); ax2.bar(r, 100 * fo, width=1.0, color='gray', alpha=0.2)
    ax2.set_ylabel('该截面被观测的帧占比 (%)', color='gray')
    ax.legend(loc='upper left'); ax.set_title('误差沿展向分布（20 s 自测，跳过第一圈）')
    save(fig, 'fig8_span_error.png')


# ── 图 9 不平衡识别 ───────────────────────────────────────────
def fig_imbalance(R, T):
    t, rec, tru, *_ = aligned(R, T)
    fr, ft = rec[50:, 4:7], tru[50:, 4:7]
    dr, dt = fr.mean(0) - fr.mean(), ft.mean(0) - ft.mean()
    fig, ax = plt.subplots(figsize=(6, 3.6))
    x = np.arange(3)
    ax.bar(x - 0.18, dt, 0.36, color='#BBBBBB', label='真值')
    ax.bar(x + 0.18, dr, 0.36, color=C_B, label='重建')
    for i in range(3):
        ax.text(x[i] + 0.18, dr[i] / 2, '%+.3f' % dr[i], ha='center', va='center', fontsize=9, color='white',
                weight='bold')
        ax.text(x[i] - 0.18, dt[i] / 2, '%+.3f' % dt[i], ha='center', va='center', fontsize=9)
    ax.set_xticks(x); ax.set_xticklabels(['叶片 1', '叶片 2', '叶片 3']); ax.axhline(0, color='k', lw=0.6)
    ax.set_ylabel('平均挥舞 − 三叶均值 (m)'); ax.set_title('气动不平衡识别（叶片 1 注入 +0.6 m）'); ax.legend()
    save(fig, 'fig9_imbalance.png')


# ── 图 10 调试历程（消融）─────────────────────────────────────
def fig_ablation():
    # 数值来自开发过程中 6 s 自测集上的逐版评分记录(见算法说明 §9)
    steps = ['基线', '+周期\n物理先验', '+轮廓加密\n外侧更新', '+先验\n二次型', '+包络轮廓\n分位数分割', '+可观性\n门控']
    obs = [6.7, 4.3, 3.6, 4.2, 3.3, 3.0]
    tipmax = [9.19, 8.78, 8.57, 9.19, 6.10, 1.95]
    tipmean = [1.72, 1.60, 1.58, 1.56, 1.32, 0.78]
    fig, ax = plt.subplots(figsize=(11, 3.8))
    x = np.arange(len(steps))
    ax.bar(x - 0.2, tipmean, 0.4, color=C_INF, label='推断区叶尖误差 平均 (m)')
    ax.bar(x + 0.2, tipmax, 0.4, color='#F6C08A', label='推断区叶尖误差 最大 (m)')
    ax.set_xticks(x); ax.set_xticklabels(steps, fontsize=9); ax.set_ylabel('m')
    ax2 = ax.twinx(); ax2.plot(x, obs, 'o-', color=C_OBS, label='观测区网格误差 平均 (cm)')
    ax2.set_ylabel('cm', color=C_OBS); ax2.set_ylim(0, 8)
    h1, l1 = ax.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=8, loc='upper right'); ax.grid(alpha=.3, axis='y')
    ax.set_title('逐步改进的效果（6 s 自测集）')
    save(fig, 'fig10_ablation.png')


# ── 图 11 三维预览帧 ──────────────────────────────────────────
def fig_preview():
    p = os.path.join(OUT, 'preview.mp4')
    if not os.path.exists(p):
        print('   (跳过 fig11：先运行 preview.py)'); return
    ims = [cv2.cvtColor(read_frame(p, k), cv2.COLOR_BGR2RGB) for k in (60, 75, 92)]
    fig, axs = plt.subplots(1, 3, figsize=(15, 4.2))
    for ax, im, k in zip(axs, ims, (60, 75, 92)):
        ax.imshow(im); ax.set_xticks([]); ax.set_yticks([]); ax.set_title('第 %d 帧' % k)
    save(fig, 'fig11_preview.png')


def main():
    meta = load_json(os.path.join(DATA, 'cameras.json'))
    rotor = Rotor(TurbineConfig.from_dict(meta['turbine']))
    cams = [Camera.from_dict(d) for d in meta['cameras']]
    R = load_json(os.path.join(OUT, 'recon.json'))
    T = load_json(os.path.join(DATA, 'truth.json'))
    print('生成插图 →', FIG)
    fig_pipeline(); fig_blade_model(rotor); fig_state_effect(rotor); fig_coverage(rotor)
    fig_inputs(rotor, cams, R); fig_envelope(rotor, cams, T); fig_timeseries(R, T)
    fig_span_error(rotor, R, T); fig_imbalance(R, T); fig_ablation(); fig_preview()


if __name__ == '__main__':
    main()
