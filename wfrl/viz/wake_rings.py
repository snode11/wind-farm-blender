"""尾流场的圆环包络可视化（转子平面往下游扩散，动画版）。

参考效果：转子转动的圆盘平面被气流带向下游，一边平移一边扩张、一边变淡，
形成一叠向下游流动并扩散的同心圆环。**不是**叶尖甩出的螺旋线。

这一版是动画的：
  · 环沿下游连续流动，流速 ∝ 转速 rpm ⇒ 转得快、扩散得快
  · 半径随下游站位扩张（尾流张开）
  · 透明度沿下游渐隐（近尾流清晰、远尾流化开），两端都淡出 ⇒ 循环无跳变
  · 每台机组颜色略有不同（绿基调各偏一点色相），便于区分各自的尾流

**合成量（SYNTH），纯几何、不进 reward/约束**：半径与流速都是视觉设定，没有
流场求解。真实亏损看 FLORIS/FAST.Farm 的速度切面（DIRECT/DERIVED）。

用法（build 一次给定颜色，每帧 update 传相位）：
    wr = build_wake_rings(plotter, color=(r,g,b))
    update_wake_rings(wr, hub_xyz, r_rotor, wind_dir_deg, turb_scale, phase)
相位 phase∈[0,1) 由调用方按 rpm 累进（见 SceneView.tick）。
"""
from __future__ import annotations

import colorsys

import numpy as np

N_RINGS = 26           # 下游放几个环（多一点更连续、更像"化开"）
N_SEG = 40             # 每个环几段（越多越圆）
N_LONG = 8             # 纵向线（管的棱），须整除 N_SEG
LENGTH_D = 2.5         # 尾流管往下游延伸几个转子直径。机位是真实坐标而转子画 5×
                       # 大，管长活在机位空间里，2.5D≈1575m 刚罩住三台再多一点
EXPAND = 0.7           # 末端半径相对根部的扩张比例（r_end = R·(1+EXPAND)）
#: 相位每秒每 rpm 推进多少（∝rpm 的下游流速）。0.03 ⇒ 9 rpm 下约 4 s 走完一程
RING_SPEED = 0.03


def turbine_color(i, n):
    """第 i/共 n 台的颜色：绿基调（H≈0.33）上按机组序小幅偏色相，便于区分。"""
    h = (0.33 + 0.10 * (i - (n - 1) / 2.0) / max(n, 1)) % 1.0
    r, g, b = colorsys.hsv_to_rgb(h, 0.75, 0.9)
    return (r, g, b)


def _basis(wind_dir_deg):
    """下游单位向量 d 与环平面正交基 (e1,e2)。风向 270°⇒尾流沿 +x。"""
    ang = np.deg2rad(wind_dir_deg - 270.0)
    d = np.array([np.cos(ang), np.sin(ang), 0.0])
    e1 = np.array([0.0, 0.0, 1.0])          # 竖直
    e2 = np.array([d[1], -d[0], 0.0])       # d × e1，水平面内
    return d, e1, e2


def _fracs(phase, n_rings=N_RINGS):
    """各环的下游归一化站位 frac∈[0,1)，随相位整体向下游滚动。"""
    return (np.arange(n_rings) / n_rings + float(phase)) % 1.0


def _alpha(fracs):
    """沿下游的透明度：近尾流快速起、远尾流缓慢化开，两端趋 0（循环无跳变）。"""
    fade_in = np.clip(fracs / 0.08, 0.0, 1.0)     # 头 8% 淡入（免得循环处跳）
    fade_out = (1.0 - fracs) ** 1.3               # 往下游缓慢淡出
    return fade_in * fade_out


def ring_points(hub, r_rotor, wind_dir_deg, turb_scale, phase,
                n_rings=N_RINGS, n_seg=N_SEG, length_d=LENGTH_D, expand=EXPAND):
    """算所有环的点 (n_rings*n_seg, 3)，纯 numpy。phase 决定下游滚动相位。"""
    hub = np.asarray(hub, float)
    d, e1, e2 = _basis(wind_dir_deg)
    L = length_d * (2.0 * r_rotor)
    fracs = _fracs(phase, n_rings)
    s = fracs * L
    radius = r_rotor * (1.0 + expand * fracs)
    ang = np.linspace(0.0, 2.0 * np.pi, n_seg, endpoint=False)
    circ = np.cos(ang)[:, None] * e1[None, :] + np.sin(ang)[:, None] * e2[None, :]
    pts = np.empty((n_rings * n_seg, 3), float)
    for k in range(n_rings):
        pts[k * n_seg:(k + 1) * n_seg] = (hub + s[k] * d)[None, :] \
            + radius[k] * circ
    return pts


def ring_rgba(color, phase, n_rings=N_RINGS, n_seg=N_SEG, base_opacity=0.5):
    """逐点 RGBA (uint8)：RGB=机组色，A=沿下游渐隐（虚化）。"""
    a = _alpha(_fracs(phase, n_rings)) * base_opacity
    rgb = np.array([int(c * 255) for c in color], np.uint8)
    rgba = np.empty((n_rings * n_seg, 4), np.uint8)
    rgba[:, :3] = rgb[None, :]
    for k in range(n_rings):
        rgba[k * n_seg:(k + 1) * n_seg, 3] = int(np.clip(a[k], 0, 1) * 255)
    return rgba


def _connectivity(n_rings=N_RINGS, n_seg=N_SEG, n_long=N_LONG):
    """环（闭合）+ 纵向线的 VTK lines 连接表。"""
    lines = []
    for k in range(n_rings):
        base = k * n_seg
        idx = list(range(base, base + n_seg)) + [base]
        lines.append([len(idx)] + idx)
    step = max(1, n_seg // n_long)
    for j in range(0, n_seg, step):
        idx = [k * n_seg + j for k in range(n_rings)]
        lines.append([len(idx)] + idx)
    return np.concatenate([np.array(l, np.int64) for l in lines])


def build_wake_rings(plotter, color=(0.15, 0.85, 0.25), line_width=2,
                     n_rings=N_RINGS, n_seg=N_SEG, n_long=N_LONG):
    """建尾流管的线框 actor，逐点 RGBA 上色+透明。每帧只更新点与 rgba。"""
    import pyvista as pv
    poly = pv.PolyData()
    poly.points = np.zeros((n_rings * n_seg, 3))
    poly.lines = _connectivity(n_rings, n_seg, n_long)
    poly["rgba"] = ring_rgba(color, 0.0, n_rings, n_seg)
    actor = plotter.add_mesh(poly, scalars="rgba", rgba=True,
                             line_width=line_width, render_lines_as_tubes=True,
                             lighting=False)
    return {"poly": poly, "actor": actor, "color": tuple(color)}


def update_wake_rings(wr, hub, r_rotor, wind_dir_deg, turb_scale, phase,
                      n_rings=N_RINGS, n_seg=N_SEG):
    """把新点与新 rgba 贴上去。碰 VTK，只能在建 actor 的线程（主线程）调。"""
    wr["poly"].points = ring_points(hub, r_rotor, wind_dir_deg, turb_scale,
                                    phase, n_rings=n_rings, n_seg=n_seg)
    wr["poly"]["rgba"] = ring_rgba(wr["color"], phase, n_rings, n_seg)


def set_visible(wr, on):
    wr["actor"].SetVisibility(bool(on))
