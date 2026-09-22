# -*- coding: utf-8 -*-
"""
叶尖三维轨迹 —— 逐帧可视化 demo(cv2.imshow)。

你只需要提供【每帧的叶尖像素坐标】，本模块负责：像素 → 三维空间坐标 → 实时绘制。

左panel：视频原帧 + 叶尖检测点 + 二维拖尾 + 模型投影的标称叶尖圆弧
右panel：世界系三维场景实时渲染(塔筒/机舱/三叶/旋转圆 + 叶尖空间轨迹)
         三维场景是【用几何参数画线画出来的】，不需要任何 3D 模型文件。

━━━ 接入你自己的检测器（推荐用法）━━━

    import cv2
    from tip_3d import TurbineGeometry, CameraModel
    from tip_3d_vis import TipVisualizer

    geom = TurbineGeometry(hub_height_m=110, overhang_m=5, tilt_deg=5,
                           tip_radius_m=68, tower_radius_m=2)
    cam  = CameraModel.look_at(W, H, fov_deg=49.2, vfov_deg=31.36,
                               pos_w=geom.hub + np.array([-8., 0., -3.]),
                               target_w=geom.tip_position(180.),
                               right_hint=geom.e_lat, upright=True)
    vis = TipVisualizer(geom, cam)

    for i, frame in enumerate(your_video):
        dets = your_detector(frame)        # {blade_id: (u, v)}  或  (u, v)  或  None
        canvas = vis.feed(frame, dets, rpm=8.0, t=i / fps)

        for bid, r in vis.results.items(): # ★ 三维坐标在这里 ★
            print(bid, r['P'], r['azimuth_deg'], r['clearance_m'], r['out_of_plane_m'])

        cv2.imshow('demo', canvas)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

━━━ 命令行 ━━━
    python tip_3d_vis.py                                   # 合成数据自检，直接能跑
    python tip_3d_vis.py --video x.mp4 --dets tips.csv      # 离线：csv 喂检测结果
    python tip_3d_vis.py --out demo.mp4 --no-show           # 出视频不弹窗

    tips.csv 格式(带表头)： frame,u,v[,blade,rpm]
交互键：q 退出  空格 暂停  a/d 三维视角左右转  w/s 俯仰  r 清轨迹
"""
import argparse
import csv
import math

import cv2
import numpy as np

from tip_3d import (TurbineGeometry, CameraModel,
                    tip_3d_from_pixel, visible_arc, RpmAzimuthIntegrator)
# 不平衡度内核住在独立模块(只依赖 numpy)，这样 Blender 自带 Python 也能 import。
# 本文件因为要 cv2 画图，Blender 里用不了 —— 详见 imbalance_3d.py 顶部说明。
from imbalance_3d import ImbalanceTracker

# 三片叶的配色(BGR)
BLADE_COLORS = [(90, 255, 90), (255, 210, 80), (200, 120, 255)]
DIM = 0.38   # 外推段颜色衰减


def _dim(c, k=DIM):
    return tuple(int(x * k) for x in c)


# ────────────────────────────────────────────────────────────────────────
#  三维场景渲染器：把世界系几何画进一个 panel（纯 cv2 画线，无需 3D 模型文件）
# ────────────────────────────────────────────────────────────────────────
class Scene3D:
    def __init__(self, geom, w=640, h=392, dist=None, az_deg=62.0, el_deg=16.0):
        self.geom = geom
        self.w, self.h = w, h
        span = geom.H + geom.R
        vfov = math.radians(42.0 * h / w)
        self.dist = dist or (span / (2.0 * math.tan(vfov / 2.0)) * 1.15)
        self.az = math.radians(az_deg)
        self.el = math.radians(el_deg)
        self.target = np.array([0.0, 0.0, span * 0.5], float)
        self._rebuild()

    def _rebuild(self):
        ca, sa, ce, se = (math.cos(self.az), math.sin(self.az),
                          math.cos(self.el), math.sin(self.el))
        pos = self.target + self.dist * np.array([ce * ca, ce * sa, se])
        self.cam = CameraModel.look_at(self.w, self.h, 42.0, 42.0 * self.h / self.w,
                                       pos, self.target, np.array([-sa, ca, 0.0]))

    def orbit(self, d_az_deg=0.0, d_el_deg=0.0):
        self.az += math.radians(d_az_deg)
        self.el = max(math.radians(-80.0),
                      min(math.radians(80.0), self.el + math.radians(d_el_deg)))
        self._rebuild()

    def p(self, P):
        u, v, _ = self.cam.project(P)
        if u is None or abs(u) > 1e4 or abs(v) > 1e4:
            return None
        return (int(round(u)), int(round(v)))

    def line(self, img, A, B, color, thick=1):
        a, b = self.p(A), self.p(B)
        if a and b:
            cv2.line(img, a, b, color, thick, cv2.LINE_AA)

    def polyline(self, img, pts, color, thick=1):
        """pts 中的 None 表示断点：叶尖出视野造成的间断不连线。"""
        prev = None
        for P in pts:
            if P is None:
                prev = None
                continue
            cur = self.p(P)
            if prev and cur:
                cv2.line(img, prev, cur, color, thick, cv2.LINE_AA)
            prev = cur

    # ── 静态机组：塔筒 + 机舱 + 旋转圆 ──
    def _draw_turbine(self, img, azim0=None):
        g = self.geom
        for c in range(-100, 101, 25):                       # 地面网格
            self.line(img, [c, -100, 0], [c, 100, 0], (52, 52, 52))
            self.line(img, [-100, c, 0], [100, c, 0], (52, 52, 52))

        # 塔筒：锥形筒(两条边 + 若干圈)
        rad = lambda z: g.r_tower * (1.0 - 0.42 * z / g.H)
        for zz in np.linspace(0, g.H, 9):
            self.polyline(img, [[rad(zz) * math.cos(a), rad(zz) * math.sin(a), zz]
                                for a in np.linspace(0, 2 * math.pi, 25)],
                          (128, 128, 128), 1)
        for sgn in (-1, 1):
            for ax in (0, 1):
                self.polyline(img, [[sgn * rad(z) if ax else 0.0,
                                     0.0 if ax else sgn * rad(z), z]
                                    for z in np.linspace(0, g.H, 30)],
                              (205, 205, 205), 2)
        self.polyline(img, [[g.r_tower * math.cos(a), g.r_tower * math.sin(a), 0.0]
                            for a in np.linspace(0, 2 * math.pi, 41)],
                      (120, 200, 255), 2)                    # 塔筒底面圆 = 世界系原点所在

        # 机舱：沿 −n̂ 从轮毂往下风侧伸出的短方箱
        back = g.hub - g.n_axis * 9.0
        for du in (-1.6, 1.6):
            for dz in (-1.4, 1.4):
                off = g.e_lat * du + g.e_up * dz
                self.line(img, g.hub + off, back + off, (185, 185, 185), 1)
        self.line(img, g.hub, back, (205, 205, 205), 3)
        hp = self.p(g.hub)
        if hp:
            cv2.circle(img, hp, 5, (235, 235, 235), -1, cv2.LINE_AA)

        # 标称旋转圆
        self.polyline(img, [g.tip_position(a) for a in np.linspace(0, 360, 181)],
                      (72, 72, 72), 1)

        # 三片叶连杆（若已知任一叶方位角，其余按 ±120° 推）
        if azim0 is not None:
            for k in range(3):
                tipk = g.tip_position(azim0 + 120.0 * k)
                self.line(img, g.hub, tipk, _dim(BLADE_COLORS[k], 0.55), 2)

    def render(self, trajs, tips, arc_segs=None, azim0=None):
        """
        trajs : {blade_id: [P|None, ...]}  已积累的三维轨迹(含断点)
        tips  : {blade_id: (P, measured)}  当前帧叶尖
        """
        img = np.full((self.h, self.w, 3), 26, np.uint8)
        self._draw_turbine(img, azim0)

        if arc_segs:                                          # 相机真能看到的弧段
            for lo, hi in arc_segs:
                self.polyline(img, [self.geom.tip_position(a)
                                    for a in np.linspace(lo, hi, 60)], (190, 190, 55), 2)

        for bid, pts in sorted(trajs.items()):
            col = BLADE_COLORS[bid % 3]
            self.polyline(img, pts, col, 2)
            for P in pts[::3]:
                if P is None:
                    continue
                q = self.p(P)
                if q:
                    cv2.circle(img, q, 2, col, -1, cv2.LINE_AA)

        for bid, (P, meas) in sorted(tips.items()):
            q = self.p(P)
            if not q:
                continue
            col = BLADE_COLORS[bid % 3] if meas else _dim(BLADE_COLORS[bid % 3], 0.6)
            cv2.circle(img, q, 7, (0, 0, 255) if meas else (90, 90, 160), -1, cv2.LINE_AA)
            cv2.circle(img, q, 10, col, 2, cv2.LINE_AA)

        for vec, col, nm in (([28, 0, 0], (60, 60, 255), 'X'),
                             ([0, 28, 0], (60, 255, 60), 'Y'),
                             ([0, 0, 28], (255, 160, 60), 'Z')):
            self.line(img, [0, 0, 0], vec, col, 2)
            q = self.p(vec)
            if q:
                cv2.putText(img, nm, q, cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1, cv2.LINE_AA)

        cv2.putText(img, 'WORLD 3D   origin = tower base center,  Z up,  meters',
                    (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.44, (205, 205, 205), 1, cv2.LINE_AA)
        cv2.putText(img, 'bright = measured (scheme B)   dim = extrapolated (scheme A)',
                    (8, self.h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.38,
                    (165, 165, 165), 1, cv2.LINE_AA)
        return img


# ────────────────────────────────────────────────────────────────────────
#  逐帧可视化器（多叶）
# ────────────────────────────────────────────────────────────────────────
class TipVisualizer:
    """
    每帧调用 feed()，内部完成 像素→三维坐标 并绘制。
    调用后 self.results = {blade_id: 该帧解算结果 dict}，其中 r['P'] 就是三维坐标。
    """

    def __init__(self, geom, cam, trail=120, max_pts=6000):
        self.geom = geom
        self.cam = cam
        self.trail = trail
        self.max_pts = max_pts
        self.scene = Scene3D(geom, w=640, h=cam.H)
        self.arc = visible_arc(cam, geom)
        # 不平衡度状态
        self.imb = ImbalanceTracker(tip_radius_m=geom.R)
        self.reset()
        # 标称弧在原图上的投影（标定自检：检测点应贴着它走）
        self.arc_px = []
        for lo, hi in self.arc['segments']:
            seg = [(int(round(u)), int(round(v)))
                   for u, v, _ in (cam.project(geom.tip_position(a))
                                   for a in np.linspace(lo, hi, 150)) if u is not None]
            if len(seg) > 1:
                self.arc_px.append(np.array(seg, np.int32))

    def reset(self):
        self.trajs = {}        # {bid: [P|None]}   三维轨迹
        self.trail_px = {}     # {bid: [(u,v)|None]} 二维拖尾
        self.integ = {}        # {bid: RpmAzimuthIntegrator}
        self.results = {}      # 本帧结果
        self.last = {}         # 各叶最后一次成功结果
        self._gap = {}
        self._pass_buf = {}    # {bid: [(t,φ,δ)...]} 当前这次划过的轨迹累积
        self.imb.reset()

    @staticmethod
    def _norm_dets(dets):
        """接受 (u,v) / {bid:(u,v)} / [(u,v),...] / None → 统一成 {bid:(u,v)}"""
        if dets is None:
            return {}
        if isinstance(dets, dict):
            return {int(k): v for k, v in dets.items() if v is not None}
        if isinstance(dets, (tuple, list)) and len(dets) == 2 \
                and all(isinstance(x, (int, float, np.floating)) for x in dets):
            return {0: (float(dets[0]), float(dets[1]))}
        return {i: v for i, v in enumerate(dets) if v is not None}

    def feed(self, frame, dets, rpm=0.0, t=0.0):
        """
        frame : BGR 原帧(可为 None)
        dets  : {blade_id:(u,v)} / (u,v) / None
        返回：拼好的可视化图。三维坐标见 self.results[bid]['P']
        """
        d = self._norm_dets(dets)
        self.results = {}
        tips = {}

        for bid in set(list(d.keys()) + list(self.integ.keys())):
            self.trajs.setdefault(bid, [])
            self.trail_px.setdefault(bid, [])
            self._gap.setdefault(bid, True)
            self._pass_buf.setdefault(bid, [])

            uv = d.get(bid)
            r = tip_3d_from_pixel(self.cam, self.geom, uv[0], uv[1]) if uv else None

            if r is not None:                       # ── 方案B：实测 ──
                r['measured'] = True
                r['blade_id'] = bid
                self.results[bid] = r
                self.last[bid] = r
                self.trajs[bid].append(r['P'])
                self.trail_px[bid].append((int(uv[0]), int(uv[1])))
                self._gap[bid] = False
                # 积累当前 pass
                self._pass_buf[bid].append((t, r['azimuth_deg'], r['out_of_plane_m']))

                ig = self.integ.get(bid)
                if ig is None:
                    ig = self.integ[bid] = RpmAzimuthIntegrator(self.geom)
                    ig.seed(r['azimuth_deg'], t)
                else:                               # 用量测把积分相位拉回来
                    ig.t, ig.phi = t, r['azimuth_deg']
                tips[bid] = (r['P'], True)
            else:                                   # ── 方案A：外推（仅用于显示）──
                if not self._gap[bid]:
                    self.trajs[bid].append(None)
                    self.trail_px[bid].append(None)
                    self._gap[bid] = True
                    # 划过结束,喂给不平衡度跟踪器
                    if len(self._pass_buf[bid]) >= 5:
                        ts, phis, delts = zip(*self._pass_buf[bid])
                        self.imb.feed_pass(bid, ts, phis, delts, rpm)
                    self._pass_buf[bid] = []
                ig = self.integ.get(bid)
                if ig is not None and ig.phi is not None:
                    ra = ig.step(t, rpm)
                    if ra is not None:
                        ra['measured'] = False
                        tips[bid] = (ra['P'], False)

            if len(self.trajs[bid]) > self.max_pts:
                self.trajs[bid] = self.trajs[bid][-self.max_pts:]
            if len(self.trail_px[bid]) > self.trail:
                self.trail_px[bid] = self.trail_px[bid][-self.trail:]

        azim0 = None
        for bid in sorted(set(list(self.results.keys()) + list(self.last.keys()))):
            src = self.results.get(bid) or self.last.get(bid)
            if src:
                azim0 = src['azimuth_deg'] - 120.0 * bid
                break

        left = self._draw_frame(frame, d, rpm)
        right = self.scene.render(self.trajs, tips, self.arc['segments'], azim0)
        return np.hstack([left, right])

    def _draw_frame(self, frame, d, rpm):
        if frame is None:
            img = np.zeros((self.cam.H, self.cam.W, 3), np.uint8)
        else:
            img = frame.copy()
            if img.shape[:2] != (self.cam.H, self.cam.W):
                img = cv2.resize(img, (self.cam.W, self.cam.H))

        for seg in self.arc_px:                     # 模型标称弧
            cv2.polylines(img, [seg], False, (190, 190, 55), 1, cv2.LINE_AA)

        for bid, pts in sorted(self.trail_px.items()):
            col = BLADE_COLORS[bid % 3]
            seg = []
            for q in pts + [None]:
                if q is None:
                    if len(seg) > 1:
                        cv2.polylines(img, [np.array(seg, np.int32)], False,
                                      col, 2, cv2.LINE_AA)
                    seg = []
                else:
                    seg.append(q)

        for bid, uv in sorted(d.items()):
            c = (int(uv[0]), int(uv[1]))
            cv2.circle(img, c, 5, (0, 0, 255), -1, cv2.LINE_AA)
            cv2.circle(img, c, 9, BLADE_COLORS[bid % 3], 2, cv2.LINE_AA)

        # HUD: 上部=逐帧实时，下部=不平衡度指标
        lines = ['rpm %5.2f    arc in FOV %.0f deg (%.0f%% of circle)'
                 % (rpm, self.arc['total_deg'], self.arc['coverage_pct'])]
        if self.results:
            for bid, r in sorted(self.results.items()):
                lines.append('B%d  XYZ %7.2f %7.2f %7.2f m   az %6.2f  clr %5.2f  defl %+5.2f'
                             % (bid, r['P'][0], r['P'][1], r['P'][2],
                                r['azimuth_deg'], r['clearance_m'], r['out_of_plane_m']))
        else:
            lines.append('no detection this frame -> 3D point extrapolated (scheme A)')

        y = 20
        for i, s in enumerate(lines):
            col = (225, 225, 225) if i == 0 else BLADE_COLORS[(i - 1) % 3]
            cv2.putText(img, s, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(img, s, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.42, col, 1, cv2.LINE_AA)
            y += 18

        # 不平衡度 HUD(底部块)
        imb_st = self.imb.get_status()
        aero, mass = imb_st['aero'], imb_st['mass']
        y_base = img.shape[0] - 58
        cv2.rectangle(img, (5, y_base - 3), (img.shape[1] - 5, img.shape[0] - 5),
                      (50, 50, 50), -1)
        cv2.rectangle(img, (5, y_base - 3), (img.shape[1] - 5, img.shape[0] - 5),
                      (120, 120, 120), 1)

        if aero['status'] == 'ok':
            txt_a = 'AERO imb  A=%.3f m (%.1f%% of mean defl)  -> blade %d  a=%.0f deg  [%d rev]' \
                    % (aero['A'], aero['pct'], aero['blade'], aero['alpha_deg'], aero['n_rev'])
            col_a = BLADE_COLORS[aero['blade'] % 3]
        else:
            txt_a = 'AERO imb  %s' % aero['status']
            col_a = (160, 160, 160)

        if mass['status'] == 'ok':
            txt_m = 'MASS imb  M=%.2f ms (tip %.3f m)  -> blade %d  a=%.0f deg  [%d rev]' \
                    % (mass['M_ms'], mass['tangential_m'], mass['blade'],
                       mass['alpha_deg'], mass['n_rev'])
            col_m = BLADE_COLORS[mass['blade'] % 3]
        else:
            txt_m = 'MASS imb  %s' % mass['status']
            col_m = (160, 160, 160)

        cv2.putText(img, txt_a, (10, y_base + 13), cv2.FONT_HERSHEY_SIMPLEX, 0.40,
                    (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(img, txt_a, (10, y_base + 13), cv2.FONT_HERSHEY_SIMPLEX, 0.40,
                    col_a, 1, cv2.LINE_AA)
        cv2.putText(img, txt_m, (10, y_base + 30), cv2.FONT_HERSHEY_SIMPLEX, 0.40,
                    (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(img, txt_m, (10, y_base + 30), cv2.FONT_HERSHEY_SIMPLEX, 0.40,
                    col_m, 1, cv2.LINE_AA)

        leg = 'yellow=nominal  colored=detected  gap=deflection  imb: vector-avg over rev window'
        cv2.putText(img, leg, (10, y_base + 48), cv2.FONT_HERSHEY_SIMPLEX, 0.34,
                    (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(img, leg, (10, y_base + 48), cv2.FONT_HERSHEY_SIMPLEX, 0.34,
                    (190, 190, 190), 1, cv2.LINE_AA)

        cv2.rectangle(img, (0, 0), (img.shape[1] - 1, img.shape[0] - 1), (90, 90, 90), 1)
        return img


# ────────────────────────────────────────────────────────────────────────
#  数据源
# ────────────────────────────────────────────────────────────────────────
def load_dets(path):
    """csv: frame,u,v[,blade,rpm] → {frame: ({bid:(u,v)}, rpm|None)}"""
    out = {}
    with open(path, 'r', newline='', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            k = {c.lower().strip(): v for c, v in row.items() if c}
            try:
                fr = int(float(k['frame']))
                u, v = float(k['u']), float(k['v'])
            except (KeyError, TypeError, ValueError):
                continue
            bid = int(float(k['blade'])) if k.get('blade') not in (None, '') else 0
            rpm = float(k['rpm']) if k.get('rpm') not in (None, '') else None
            d, r0 = out.setdefault(fr, ({}, None))
            d[bid] = (u, v)
            out[fr] = (d, rpm if rpm is not None else r0)
    return out


def synth_frame(cam, geom, azim0, defl_deg=(2.2, 1.6, 2.9), noise_px=1.2):
    """合成一帧"净空相机"画面 + 三叶叶尖检测(仅在无真实视频时用)。"""
    img = np.full((cam.H, cam.W, 3), 46, np.uint8)

    def poly(pts):
        segs, cur = [], []
        for P in pts:
            u, v, _ = cam.project(P)
            if u is None or abs(u) > 1e4 or abs(v) > 1e4:
                if len(cur) > 1:
                    segs.append(np.array(cur, np.int32))
                cur = []
            else:
                cur.append((int(round(u)), int(round(v))))
        if len(cur) > 1:
            segs.append(np.array(cur, np.int32))
        return segs

    for c in range(-120, 121, 20):
        for s in poly([[c, y, 0.0] for y in np.linspace(-120, 120, 40)]):
            cv2.polylines(img, [s], False, (66, 74, 62), 1, cv2.LINE_AA)
        for s in poly([[x, c, 0.0] for x in np.linspace(-120, 120, 40)]):
            cv2.polylines(img, [s], False, (66, 74, 62), 1, cv2.LINE_AA)
    rad = lambda z: geom.r_tower * (1.0 - 0.45 * z / geom.H)
    for sgn in (-1.0, 1.0):
        for s in poly([[0.0, sgn * rad(z), z] for z in np.linspace(0, geom.H, 60)]):
            cv2.polylines(img, [s], False, (150, 150, 150), 2, cv2.LINE_AA)

    dets = {}
    for k in range(3):
        phi = azim0 + 120.0 * k
        psi = defl_deg[k] * math.cos(math.radians(phi - 180.0))
        tip = geom.tip_position(phi, psi)
        for s in poly([geom.hub + (tip - geom.hub) * q for q in np.linspace(0, 1, 60)]):
            cv2.polylines(img, [s], False, (205, 205, 205), 5, cv2.LINE_AA)
        u, v, ok = cam.project(tip)
        if ok:
            dets[k] = (u + np.random.normal(0, noise_px), v + np.random.normal(0, noise_px))

    img = cv2.GaussianBlur(img, (3, 3), 0)
    img = np.clip(img.astype(np.int16) +
                  np.random.normal(0, 4, img.shape).astype(np.int16), 0, 255).astype(np.uint8)
    return img, dets


# ────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--video')
    ap.add_argument('--dets', help='csv: frame,u,v[,blade,rpm]')
    ap.add_argument('--out', help='输出 mp4')
    ap.add_argument('--no-show', action='store_true')
    ap.add_argument('--frames', type=int, default=500)
    ap.add_argument('--dump'); ap.add_argument('--dump-at', default='')
    ap.add_argument('--rpm', type=float, default=8.0)
    ap.add_argument('--fps', type=float, default=25.0)
    ap.add_argument('--hub-height', type=float, default=110.0)
    ap.add_argument('--overhang', type=float, default=5.0)
    ap.add_argument('--tilt', type=float, default=5.0)
    ap.add_argument('--tip-radius', type=float, default=68.0)
    ap.add_argument('--tower-radius', type=float, default=2.0)
    ap.add_argument('--fov', type=float, default=49.20)
    ap.add_argument('--vfov', type=float, default=31.36)
    ap.add_argument('--cam-back', type=float, default=8.0)
    ap.add_argument('--cam-down', type=float, default=3.0)
    a = ap.parse_args()

    geom = TurbineGeometry(a.hub_height, a.overhang, a.tilt, a.tip_radius, a.tower_radius)
    cap, n_frames, W, H = None, a.frames, 640, 392
    if a.video:
        cap = cv2.VideoCapture(a.video)
        if not cap.isOpened():
            raise SystemExit('打不开视频: %s' % a.video)
        W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or W
        H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or H
        n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 10 ** 9
        a.fps = cap.get(cv2.CAP_PROP_FPS) or a.fps

    cam = CameraModel.look_at(W, H, a.fov, a.vfov,
                              geom.hub + np.array([-a.cam_back, 0.0, -a.cam_down]),
                              geom.tip_position(180.0), geom.e_lat, upright=True)
    dets_tbl = load_dets(a.dets) if a.dets else None
    vis = TipVisualizer(geom, cam)
    print('可见弧段 %s，共 %.1f°(整圈的 %.1f%%)'
          % (vis.arc['segments'], vis.arc['total_deg'], vis.arc['coverage_pct']))

    dump_at = {int(s) for s in a.dump_at.split(',') if s.strip()}
    writer, paused, i, azim0 = None, False, 0, 150.0
    while i < n_frames:
        t, rpm = i / a.fps, a.rpm
        if cap is not None:
            ok, frame = cap.read()
            if not ok:
                break
            dets = {}
            if dets_tbl and i in dets_tbl:
                dets, r = dets_tbl[i]
                if r is not None:
                    rpm = r
        else:
            azim0 = (azim0 + 360.0 * rpm / 60.0 / a.fps) % 360.0
            frame, dets = synth_frame(cam, geom, azim0)

        canvas = vis.feed(frame, dets, rpm, t)

        if a.out:
            if writer is None:
                writer = cv2.VideoWriter(a.out, cv2.VideoWriter_fourcc(*'mp4v'),
                                         a.fps, (canvas.shape[1], canvas.shape[0]))
            writer.write(canvas)
        if a.dump and i in dump_at:
            cv2.imwrite('%s_%03d.png' % (a.dump, i), canvas)

        if not a.no_show:
            cv2.imshow('tip 3D | q quit  space pause  a/d/w/s orbit  r reset', canvas)
            k = cv2.waitKey(0 if paused else max(1, int(1000 / a.fps))) & 0xFF
            if k == ord('q'):
                break
            elif k == ord(' '):
                paused = not paused
            elif k == ord('a'):
                vis.scene.orbit(d_az_deg=-6)
            elif k == ord('d'):
                vis.scene.orbit(d_az_deg=6)
            elif k == ord('w'):
                vis.scene.orbit(d_el_deg=4)
            elif k == ord('s'):
                vis.scene.orbit(d_el_deg=-4)
            elif k == ord('r'):
                vis.reset()
        i += 1

    if cap is not None:
        cap.release()
    if writer is not None:
        writer.release()
        print('已写出', a.out)
    cv2.destroyAllWindows()
    for bid, pts in sorted(vis.trajs.items()):
        print('叶%d 三维轨迹点 %d 个' % (bid, sum(1 for p in pts if p is not None)))


if __name__ == '__main__':
    main()
