"""
atlas.py —— 叶片表面纹理坐标（每片叶片一张 UV 图集）。纯 numpy，Blender 内也能 import。

图集定义（三片叶片相同）：
  行 = 展向半径 r，第 0 行在叶根，最后一行在叶尖，行距 dr（默认 2.5 cm）；
  列 = 沿截面周线的弧长比例 τ ∈ [0,1)：
        τ = 0 后缘 → 压力面(迎风面，y>0) → 前缘 τ_le ≈ 0.5 → 吸力面(背风面，y<0) → 后缘 τ = 1。
  相机装在叶轮后方，只能看到吸力面(背风面)和前后缘的一部分；压力面永远看不到。
表面坐标 (r, c)：c = 从前缘量起的弧长(米)，正 = 吸力面一侧，负 = 压力面一侧。缺陷位置用它表示。
Blender UV：u = τ，v = 1 − (r − r0)/(r1 − r0)（PNG 第 0 行 = 叶根 = v 接近 1）。
"""
import numpy as np


def validate_atlas_meta(meta, shape=None):
    """核对尺寸和物理坐标合同；不得把任意无元数据图集当作默认图集。"""
    for key, expected in (('schema', 'blade-atlas.v1'), ('row_direction', 'root_to_tip'),
                          ('column_coordinate', 'normalized_closed_arc')):
        if key in meta and meta[key] != expected:
            raise ValueError('unsupported atlas coordinate contract: ' + key)
    try:
        r0, r1, dr = (float(meta[k]) for k in ('r0', 'r1', 'dr'))
        H, W = (int(meta[k]) for k in ('H', 'W'))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('atlas metadata missing/invalid r0,r1,dr,H,W') from exc
    if (not np.isfinite([r0, r1, dr]).all() or r1 <= r0 or dr <= 0 or H <= 0 or W <= 0
            or H != meta['H'] or W != meta['W']
            or not np.isclose(dr * H, r1 - r0, rtol=1e-8, atol=1e-8)):
        raise ValueError('atlas metadata has inconsistent dimensions or physical span')
    if shape is not None and tuple(shape[-2:]) != (H, W):
        raise ValueError('atlas image shape %s differs from metadata %s' % (tuple(shape[-2:]), (H, W)))
    geom_keys = ('radii_m', 'perimeter_m', 'tau_le')
    if any(k in meta for k in geom_keys):
        if not all(k in meta for k in geom_keys):
            raise ValueError('atlas physical surface mapping is incomplete')
        r, p, le = (np.asarray(meta[k], float) for k in geom_keys)
        if (r.ndim != 1 or len(r) < 2 or p.shape != r.shape or le.shape != r.shape
                or not np.isfinite(np.concatenate([r, p, le])).all()
                or not (np.diff(r) > 0).all() or (p <= 0).any()
                or (le < 0).any() or (le > 1).any()
                or not np.isclose(r[0], r0) or not np.isclose(r[-1], r1)):
            raise ValueError('atlas physical surface mapping is invalid')
    return dict(meta)


class Atlas:
    def __init__(self, tpl, dr=0.025, n_cols=768):
        if not np.isfinite(dr) or dr <= 0 or int(n_cols) != n_cols or n_cols < 2:
            raise ValueError('atlas dr must be positive and n_cols must be an integer >=2')
        self.tpl = tpl
        S, N = len(tpl.r), tpl.cfg.n_ring
        self.S, self.N = S, N
        self.r0, self.r1 = float(tpl.r[0]), float(tpl.r[-1])
        self.H = int(round((self.r1 - self.r0) / dr))
        if self.H < 2:
            raise ValueError('atlas must have at least two span rows')
        self.dr = (self.r1 - self.r0) / self.H
        self.W = int(n_cols)
        self.r_rows = self.r0 + (np.arange(self.H) + 0.5) * self.dr
        self.tau_cols = (np.arange(self.W) + 0.5) / self.W

        # 每个截面周线的弧长表（闭合，N+1 个点，最后一点回到后缘）
        pts = tpl.foils * tpl.chord[:, None, None]                       # (S,N,2) 米
        closed = np.concatenate([pts, pts[:, :1]], 1)
        seg = np.linalg.norm(np.diff(closed, axis=1), axis=-1)            # (S,N)
        cum = np.concatenate([np.zeros((S, 1)), np.cumsum(seg, 1)], 1)    # (S,N+1)
        self.perim_v = cum[:, -1]                                         # 周长 (S,)
        self.tau_v = cum / self.perim_v[:, None]                          # (S,N+1)，最后一列 = 1
        self.tau_le_v = self.tau_v[:, N // 2]                             # 前缘(x=0)

        # 图集每个纹素 → 网格参数(截面分数下标, 周线分数下标)，与状态无关，预计算
        i_f = np.interp(self.r_rows, tpl.r, np.arange(S))
        self.i0 = np.minimum(np.floor(i_f).astype(int), S - 2)
        self.wi = (i_f - self.i0).astype(np.float32)
        tau_tab = (1 - self.wi)[:, None] * self.tau_v[self.i0] + self.wi[:, None] * self.tau_v[self.i0 + 1]
        j_f = np.stack([np.interp(self.tau_cols, tau_tab[h], np.arange(N + 1)) for h in range(self.H)])
        self.j0 = np.minimum(np.floor(j_f).astype(int), N - 1)
        self.wj = (j_f - self.j0).astype(np.float32)
        self.j1 = (self.j0 + 1) % N
        self.perim_rows = np.interp(self.r_rows, tpl.r, self.perim_v)
        self.tau_le_rows = np.interp(self.r_rows, tpl.r, self.tau_le_v)
        # 纹素的表面坐标 c(米)，正 = 吸力面
        self.c_grid = (self.tau_cols[None, :] - self.tau_le_rows[:, None]) * self.perim_rows[:, None]

    # —— 几何 ——
    def row_of_r(self, r):
        return (np.asarray(r) - self.r0) / self.dr - 0.5

    def col_of_c(self, r, c):
        """表面坐标 (r,c) → 图集列(浮点)。"""
        tau = np.interp(r, self.tpl.r, self.tau_le_v) + np.asarray(c) / np.interp(r, self.tpl.r, self.perim_v)
        return (tau % 1.0) * self.W - 0.5

    def suction_arc(self, r):
        """前缘到后缘沿吸力面的弧长(米)。"""
        return (1.0 - np.interp(r, self.tpl.r, self.tau_le_v)) * np.interp(r, self.tpl.r, self.perim_v)

    def surface(self, Vb, rows=slice(None)):
        """单片叶片网格顶点 (S,N,3) → 指定行的纹素三维坐标 (h,W,3)，在网格四边形内双线性插值。"""
        i0, wi = self.i0[rows], self.wi[rows][:, None, None]
        j0, j1, wj = self.j0[rows], self.j1[rows], self.wj[rows][..., None]
        ii = i0[:, None]
        a = Vb[ii, j0]; b = Vb[ii, j1]; c = Vb[ii + 1, j0]; d = Vb[ii + 1, j1]
        return (1 - wi) * ((1 - wj) * a + wj * b) + wi * ((1 - wj) * c + wj * d)

    def rows_between(self, r_lo, r_hi):
        a = int(np.clip(np.floor(self.row_of_r(r_lo)), 0, self.H))
        b = int(np.clip(np.ceil(self.row_of_r(r_hi)) + 1, 0, self.H))
        return slice(a, b)

    # —— 顶点属性(渲染、Blender UV 用) ——
    def vertex_r_tau(self):
        """每个顶点的 (r, τ)，形状 (S,N)。"""
        r = np.repeat(self.tpl.r[:, None], self.N, 1)
        return r, self.tau_v[:, :self.N].copy()

    def corner_r_tau(self, faces):
        """逐面角的 (r, τ)，(F,3,2)。跨后缘接缝的面把 j=0 顶点的 τ 取 1，避免插值穿过整张图。"""
        N = self.N
        r, tau = self.vertex_r_tau()
        rf, tf = r.reshape(-1)[faces], tau.reshape(-1)[faces]
        j = faces % N
        seam = (j == N - 1).any(1, keepdims=True) & (j == 0)
        n_side = (self.S - 1) * N * 2                                   # 侧面三角形数；其后是叶尖封口
        side = (np.arange(len(faces)) < n_side)[:, None]
        tf = np.where(seam & side, 1.0, tf)
        # 叶尖封口面：扇形跨整圈，统一取第一个顶点的 τ(封口很小，只是避免整张图被拉伸)
        tf = np.where(side, tf, tf[:, :1])
        return np.stack([rf, tf], -1)

    def corner_uv(self, faces):
        """Blender 逐面角 UV (F,3,2)。"""
        rt = self.corner_r_tau(faces)
        u = rt[..., 1]
        v = 1.0 - (rt[..., 0] - self.r0) / (self.r1 - self.r0)
        return np.stack([u, v], -1)

    def meta(self):
        return {'schema': 'blade-atlas.v1', 'r0': self.r0, 'r1': self.r1, 'dr': self.dr, 'H': self.H, 'W': self.W,
                'row_direction': 'root_to_tip', 'column_coordinate': 'normalized_closed_arc',
                'radii_m': self.tpl.r.tolist(), 'perimeter_m': self.perim_v.tolist(),
                'tau_le': self.tau_le_v.tolist(),
                'rows': '第 0 行 = 叶根', 'cols': 'τ：后缘0→压力面→前缘→吸力面→后缘1'}
