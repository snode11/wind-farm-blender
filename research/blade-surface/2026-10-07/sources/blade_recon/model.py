"""
model.py —— 物理参数化叶片前向模型：状态向量 → 三片叶片的三维网格。

叶片 = 设计几何(NREL 5MW 公开弦长/扭角/翼型族) + 结构振型(ElastoDyn 公开振型多项式)
       + 刚体姿态(方位角 / 逐叶片桨距 / 轴倾 / 预锥 / 预弯)。
网格只能由这几样生成，所以重建结果天然是"物理上可能的叶片"，不会出现扭曲折断。

坐标系(机组系，米)：原点在塔底，z 向上，x 指向上风(塔筒 → 叶轮)，y 由右手定则。
纯 numpy，Blender 内置 Python 也能直接 import。
"""
import json
import math
from dataclasses import dataclass, field, asdict

import numpy as np

# ── NREL 5MW 叶片公开数据（Jonkman 2009, Table 2-1；RNodes 从轮毂中心量）──────────
NREL_R = [1.5, 2.8667, 5.6000, 8.3333, 11.7500, 15.8500, 19.9500, 24.0500, 28.1500,
          32.2500, 36.3500, 40.4500, 44.5500, 48.6500, 52.7500, 56.1667, 58.9000,
          61.6333, 63.0]
NREL_CHORD = [3.542, 3.542, 3.854, 4.167, 4.557, 4.652, 4.458, 4.249, 4.007, 3.748,
              3.502, 3.256, 3.010, 2.764, 2.518, 2.313, 2.086, 1.419, 0.80]
NREL_TWIST = [13.308, 13.308, 13.308, 13.308, 13.308, 11.480, 10.162, 9.011, 7.795,
              6.544, 5.361, 4.188, 3.125, 2.319, 1.526, 0.863, 0.370, 0.106, 0.0]
# 相对厚度：Cylinder(1.0) → DU40 → DU35 → DU30 → DU25 → DU21 → NACA64-618
NREL_TC = [1.0, 1.0, 1.0, 1.0, 0.405, 0.35, 0.35, 0.30, 0.25, 0.25, 0.21, 0.21,
           0.18, 0.18, 0.18, 0.18, 0.18, 0.18, 0.18]

# ElastoDyn NRELOffshrBsline5MW_Blade.dat 振型多项式系数 (x^2 .. x^6)，x = 展向归一化
MODE_FLAP1 = [0.0622, 1.7254, -3.2452, 4.7131, -2.2555]
MODE_FLAP2 = [-0.5809, 1.2067, -15.5349, 29.7347, -13.8255]
MODE_EDGE1 = [0.3627, 2.5337, -3.5772, 2.3760, -0.6952]

# 状态向量布局（每帧）
#   psi            叶片 1 的方位角(度)，0 = 竖直向上，朝 +ê_lat 方向为正；叶片 b 为 psi + 120*b
#                  spin=+1 时随时间增大(从上风看顺时针)，spin=-1 时随时间减小
#   pitch[3]       逐叶片桨距(度)，正 = 朝顺桨
#   flap[3]        挥舞 1 阶叶尖幅值(米)，正 = 朝下风/塔筒
#   edge[3]        摆振 1 阶叶尖幅值(米)，正 = 朝前缘(旋转方向)
#   flap2[3]       挥舞 2 阶叶尖幅值(米)，默认不估计(固定 0)
STATE_NAMES = (['psi'] + ['pitch%d' % b for b in range(3)] + ['flap%d' % b for b in range(3)]
               + ['edge%d' % b for b in range(3)] + ['flap2_%d' % b for b in range(3)])
N_STATE = len(STATE_NAMES)


def poly_mode(coef, x):
    """ElastoDyn 振型：sum c_k x^(k+2)，x∈[0,1]，phi(1)=1。"""
    y = np.zeros_like(x)
    for k, c in enumerate(coef):
        y = y + c * x ** (k + 2)
    return y


@dataclass
class TurbineConfig:
    hub_height_m: float = 90.0
    overhang_m: float = 5.0          # 轮毂中心相对塔轴向上风的水平距离
    tilt_deg: float = 5.0            # 轴倾，正 = 叶轮面朝上仰
    cone_deg: float = 2.5            # 预锥，正 = 叶片朝上风(远离塔筒)
    hub_radius_m: float = 1.5
    tip_radius_m: float = 63.0
    prebend_tip_m: float = 0.0       # 叶尖预弯(朝上风为正)，原版 NREL 5MW = 0
    n_sections: int = 40
    n_ring: int = 32                 # 每个截面周线点数(偶数)
    spin: int = 1                    # 转向：+1 = 从上风看顺时针(NREL 5MW)，-1 = 逆时针；决定前缘朝向

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d):
        c = TurbineConfig()
        for k, v in (d or {}).items():
            if hasattr(c, k):
                setattr(c, k, type(getattr(c, k))(v))
        return c


def airfoil_ring(tc, n_ring):
    """单位弦长翼型周线(前缘 x=0，后缘 x=1)，y 为厚度方向(吸力面为正)。
    tc>=1 为圆；0.4~1 之间在 NACA00xx 与圆之间混合。返回 (n_ring,2)，从后缘经吸力面、前缘、压力面回到后缘。"""
    half = n_ring // 2
    beta = np.linspace(0.0, math.pi, half + 1)
    x = 0.5 * (1.0 - np.cos(beta))                       # 余弦加密前后缘
    t = min(tc, 0.40)
    yt = 5 * t * (0.2969 * np.sqrt(x) - 0.1260 * x - 0.3516 * x ** 2
                  + 0.2843 * x ** 3 - 0.1036 * x ** 4)
    camber = 0.02 * (1.0 - min(1.0, tc / 0.4)) * 4 * x * (1 - x) if tc < 0.4 else 0 * x
    up = np.stack([x, camber + yt], 1)
    lo = np.stack([x, camber - yt], 1)
    foil = np.concatenate([up[::-1], lo[1:-1]], 0)       # TE→upper→LE→lower→(TE)
    if tc >= 0.4:
        ang = np.linspace(0, 2 * math.pi, len(foil), endpoint=False)
        circ = np.stack([0.5 + 0.5 * np.cos(ang), 0.5 * np.sin(ang)], 1)
        w = min(1.0, (tc - 0.4) / 0.6)
        foil = (1 - w) * foil + w * circ
    return foil[:n_ring]


class BladeTemplate:
    """一片叶片的未变形截面库(展向半径、弦长、扭角、翼型、桨距轴位置)。三片叶片共用。"""

    def __init__(self, cfg: TurbineConfig):
        self.cfg = cfg
        R0, R1 = cfg.hub_radius_m, cfg.tip_radius_m
        # 截面：根部和叶尖加密
        u = np.linspace(0, 1, cfg.n_sections)
        u = 0.5 * (1 - np.cos(math.pi * u)) * 0.6 + 0.4 * u
        self.r = R0 + (R1 - R0) * u
        scale = R1 / 63.0                                   # 按叶尖半径缩放 NREL 外形
        rr = np.array(NREL_R) * scale
        self.chord = np.interp(self.r, rr, np.array(NREL_CHORD) * scale)
        self.twist = np.interp(self.r, rr, NREL_TWIST)
        self.tc = np.interp(self.r, rr, NREL_TC)
        self.x_pa = np.interp(self.tc, [0.18, 0.4, 1.0], [0.25, 0.30, 0.5])   # 桨距轴弦向位置
        self.xi = (self.r - R0) / (R1 - R0)                 # 振型自变量
        self.phi_f1 = poly_mode(MODE_FLAP1, self.xi)
        self.phi_f2 = poly_mode(MODE_FLAP2, self.xi)
        self.phi_e1 = poly_mode(MODE_EDGE1, self.xi)
        self.prebend = cfg.prebend_tip_m * self.xi ** 2
        self.foils = np.stack([airfoil_ring(tc, cfg.n_ring) for tc in self.tc], 0)  # (S,N,2)

    def faces(self):
        """四边形→三角形面索引（单片叶片，按 S×N 顶点排列）。"""
        S, N = len(self.r), self.cfg.n_ring
        f = []
        for i in range(S - 1):
            for j in range(N):
                a, b = i * N + j, i * N + (j + 1) % N
                c, d = (i + 1) * N + (j + 1) % N, (i + 1) * N + j
                f.append((a, b, c))
                f.append((a, c, d))
        # 叶尖封口
        tip0 = (S - 1) * N
        for j in range(1, N - 1):
            f.append((tip0, tip0 + j + 1, tip0 + j))
        return np.array(f, dtype=np.int64)


class Rotor:
    """前向模型。forward(state) → verts (3, S, N, 3)，axis (3, S, 3)。"""

    def __init__(self, cfg: TurbineConfig):
        self.cfg = cfg
        self.tpl = BladeTemplate(cfg)
        tau = math.radians(cfg.tilt_deg)
        self.n = np.array([math.cos(tau), 0.0, math.sin(tau)])        # 叶轮轴，指向上风
        self.e_up = np.array([-math.sin(tau), 0.0, math.cos(tau)])    # 面内"上"
        self.e_lat = np.cross(self.n, self.e_up) * -1.0              # 面内"横向"
        self.hub = np.array([cfg.overhang_m, 0.0, cfg.hub_height_m])

    # —— 单片叶片 ——
    def blade(self, psi_deg, pitch_deg, flap, edge, flap2=0.0):
        cfg, T = self.cfg, self.tpl
        psi, cone = math.radians(psi_deg), math.radians(cfg.cone_deg)
        radial = math.cos(psi) * self.e_up + math.sin(psi) * self.e_lat
        e_rot = cfg.spin * (-math.sin(psi) * self.e_up + math.cos(psi) * self.e_lat)  # 旋转(前缘)方向
        a = math.cos(cone) * radial + math.sin(cone) * self.n               # 叶片展向轴
        n_b = -math.sin(cone) * radial + math.cos(cone) * self.n            # 垂直展向、朝上风
        th = math.radians(pitch_deg)
        ch_p = math.cos(th) * e_rot + math.sin(th) * n_b                    # 桨距后弦线方向
        tk_p = -math.sin(th) * e_rot + math.cos(th) * n_b
        flap_dir, edge_dir = -tk_p, ch_p                                    # 振型方向(随桨距转)

        d_f = flap * T.phi_f1 + flap2 * T.phi_f2                            # (S,)
        d_e = edge * T.phi_e1
        # 弦长缩短：弯曲后弧长守恒，展向投影缩短 0.5∫(δ')² dr
        dr = np.diff(T.r)
        slope2 = (np.diff(d_f) / dr) ** 2 + (np.diff(d_e) / dr) ** 2
        shorten = np.concatenate([[0.0], np.cumsum(0.5 * slope2 * dr)])
        axis = (self.hub[None] + (T.r - shorten)[:, None] * a[None]
                + T.prebend[:, None] * n_b[None]
                + d_f[:, None] * flap_dir[None] + d_e[:, None] * edge_dir[None])  # (S,3)

        beta = np.radians(T.twist) + th                                     # 截面总转角
        cb, sb = np.cos(beta)[:, None], np.sin(beta)[:, None]
        ch = cb * e_rot[None] + sb * n_b[None]                              # (S,3) 前缘方向
        tk = -sb * e_rot[None] + cb * n_b[None]
        x = T.foils[..., 0]; y = T.foils[..., 1]                            # (S,N)
        off_c = (T.x_pa[:, None] - x) * T.chord[:, None]                    # 沿 ch，前缘为正
        off_t = y * T.chord[:, None]
        verts = (axis[:, None, :] + off_c[..., None] * ch[:, None, :]
                 + off_t[..., None] * tk[:, None, :])
        return verts, axis

    def forward(self, state):
        s = np.asarray(state, float)
        V, A = [], []
        for b in range(3):
            v, ax = self.blade(s[0] + 120.0 * b, s[1 + b], s[4 + b], s[7 + b], s[10 + b])
            V.append(v); A.append(ax)
        return np.stack(V), np.stack(A)

    def tips(self, state):
        _, A = self.forward(state)
        return A[:, -1, :]


def default_state(psi=0.0, pitch=0.0):
    s = np.zeros(N_STATE)
    s[0] = psi
    s[1:4] = pitch
    return s


def save_json(path, obj):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)
