"""
recon.py —— 三路视频 → 逐帧叶片物理状态 → 三维叶片序列（供 Blender 可视化）。

方法(先合成再比较)：
  1. 每路每帧分割叶片(外部 YOLO 掩码，或天空背景下的自动阈值)，取轮廓 → 距离变换 DT
  2. 用 model.Rotor 把状态 x 生成三片叶片网格，按截面算出投影轮廓左右两条边
  3. 双向倒角残差：模型轮廓点落在观测轮廓上(查 DT) + 观测轮廓点被模型轮廓解释(点到折线)
  4. 加时序先验(方位角按转速外推，其余量帧间平滑)与物理边界，鲁棒最小二乘求 x
  5. 输出 x(t)、参数标准差、逐截面"观测/推断"标记；Blender 用同一模型还原网格

    python recon.py --data data/synth --out out/synth
    python recon.py --data <目录> --masks <掩码根目录>      # 用你们的 YOLO 分割
数据目录需要：cameras.json + 每路一个视频(<name>.mp4) 或帧目录(<name>/*.png)。
"""
import argparse
import glob
import math
import os
import time

import cv2
import numpy as np
from scipy.optimize import least_squares

from input_adapter import InputSequence, RGBSource

from camera import Camera
from model import Rotor, TurbineConfig, N_STATE, STATE_NAMES, default_state, load_json, save_json
from texture_geometry import TextureGeometry, image_information

EST = list(range(N_STATE))          # CLI estimates all existing global model directions
LB = np.array([-np.inf] + [-5.0] * 3 + [-3.0] * 3 + [-2.0] * 3 + [-3.0] * 3)
UB = np.array([np.inf] + [90.0] * 3 + [12.0] * 3 + [2.0] * 3 + [3.0] * 3)
# 帧间变化先验(1σ)：方位角相对转速外推，其余相对上一帧
SIG_STEP = np.array([3.0] + [1.5] * 3 + [0.8] * 3 + [0.25] * 3 + [0.3] * 3)
# 弱绝对先验：看不到的叶片不会漂到不合理的值
PRIOR_MU = np.array([0.0] + [0.0] * 3 + [3.0] * 3 + [0.0] * 6)
PRIOR_SIG = np.array([np.inf] + [25.0] * 3 + [4.0] * 3 + [1.0] * 3 + [1.0] * 3)
EDGE_PX = 2.0                  # 鲁棒损失尺度(像素)


# ───────────────────────── 输入 ─────────────────────────
class FrameSource(RGBSource):
    """Backward-compatible grayscale reader with strict RGBSource diagnostics."""
    def read(self):
        img = super().read()
        return None if img is None else cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)


MIN_CONTRAST = 12             # 自动阈值的最低对比度(灰度级)
MIN_AREA_FRAC = 0.002         # 连通域最小面积(占画面比例)
POLARITY = 'dark'             # 叶片相对天空：dark=更暗(可见光逆光)，bright=更亮(近红外补光)


def segment(gray, mask_path=None):
    """叶片掩码。有外部掩码(YOLO 等)就用外部的；否则按天空背景做 Otsu 自动阈值。
    背景取逐行分位数(天空上下渐变；叶片占半行以上时中位数会失效，所以用 95/5 分位)。"""
    if gray is None or gray.ndim != 2:
        raise ValueError('segmentation requires a readable grayscale frame')
    if mask_path is not None:
        if not os.path.exists(mask_path):
            raise ValueError('missing explicit mask: ' + str(mask_path))
        m = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
        if m is None:
            raise ValueError('unreadable explicit mask: ' + str(mask_path))
        if m.shape != gray.shape:
            raise ValueError('explicit mask/image size mismatch: ' + str(mask_path))
        return (m > 127).astype(np.uint8) * 255
    g = cv2.GaussianBlur(gray, (5, 5), 0)
    # 背景近似为逐行中位数(天空自上而下渐变)，减去后再阈值，抗渐变
    gf = g.astype(np.float32)
    if POLARITY == 'dark':
        bg = np.percentile(gf, 95, axis=1, keepdims=True)
        d = np.clip(bg - gf, 0, 255).astype(np.uint8)
    else:
        bg = np.percentile(gf, 5, axis=1, keepdims=True)
        d = np.clip(gf - bg, 0, 255).astype(np.uint8)
    thr, m = cv2.threshold(d, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if thr < MIN_CONTRAST:                 # 叶片与天空对比度不足 → 整幅是天空(Otsu 只是在分噪声)
        return np.zeros_like(m)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    # 去掉小连通域(噪声、鸟、云边)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    keep = np.zeros(n, bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= MIN_AREA_FRAC * m.size
    return (keep[lab] * 255).astype(np.uint8)


class Obs:
    """一路相机一帧的观测：轮廓距离变换 + 轮廓采样点(去掉贴画面边框的点)。"""

    def __init__(self, mask, n_pts=200, border=3, known=None):
        self.mask = mask
        H, W = mask.shape
        known = np.ones(mask.shape, bool) if known is None else np.asarray(known, bool)
        if known.shape != mask.shape:
            raise ValueError('known contour support size mismatch')
        self.known = known
        self.support_dt = (None if known.all() else
            cv2.distanceTransform(known.astype(np.uint8), cv2.DIST_L2, 5).astype(np.float32))
        edge = cv2.morphologyEx(mask, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
        safe = cv2.erode(known.astype(np.uint8), np.ones((3,3),np.uint8)) > 0
        edge[~safe] = 0
        self.has = edge.any()
        self.dt = cv2.distanceTransform(255 - edge, cv2.DIST_L2, 5).astype(np.float32)
        ys, xs = np.nonzero(edge)
        keep = (xs > border) & (xs < W - 1 - border) & (ys > border) & (ys < H - 1 - border)
        xs, ys = xs[keep], ys[keep]
        if len(xs) > n_pts:
            idx = np.linspace(0, len(xs) - 1, n_pts).astype(int)
            xs, ys = xs[idx], ys[idx]
        self.pts = np.stack([xs, ys], 1).astype(np.float64)

    def sample_dt(self, uv):
        """双线性取 DT；画面外返回 nan。"""
        H, W = self.dt.shape
        u, v = uv[..., 0], uv[..., 1]
        ok = np.isfinite(u) & (u >= 0) & (u <= W - 1) & (v >= 0) & (v <= H - 1)
        out = np.full(u.shape, np.nan)
        uu, vv = u[ok], v[ok]
        x0 = np.floor(uu).astype(int); y0 = np.floor(vv).astype(int)
        x1 = np.minimum(x0 + 1, W - 1); y1 = np.minimum(y0 + 1, H - 1)
        ax, ay = uu - x0, vv - y0
        d = self.dt
        out[ok] = ((1 - ax) * (1 - ay) * d[y0, x0] + ax * (1 - ay) * d[y0, x1]
                   + (1 - ax) * ay * d[y1, x0] + ax * ay * d[y1, x1])
        return out

    def model_residual(self, uv):
        """Continuous censored model edge distance at the image boundary.

        Outside-image contours are unknown. Fade their contribution to zero
        within a three-pixel border instead of dropping a nonzero DT sample
        discontinuously when the projected contour leaves the image.
        """
        d = self.sample_dt(uv)
        H, W = self.dt.shape
        u, v = uv[..., 0], uv[..., 1]
        margin = np.minimum.reduce([u, W - 1 - u, v, H - 1 - v])
        weight = np.clip(margin / 3.0, 0.0, 1.0)
        weight = weight ** 2 * (3.0 - 2.0 * weight)
        if self.support_dt is not None:
            d_saved = self.dt
            self.dt = self.support_dt
            try:
                margin_known = self.sample_dt(uv)
            finally:
                self.dt = d_saved
            w_known = np.clip(np.nan_to_num(margin_known, nan=0.)/3., 0., 1.)
            weight *= w_known**2 * (3.-2.*w_known)
        return np.where(np.isfinite(d), d * weight, 0.0)


# ───────────────────────── 模型轮廓 ─────────────────────────
def silhouettes(rotor, cam, state, with_rings=False, geometry=None):
    """每片叶片每个截面的左/右轮廓点 (3, S, 2, 2)，不可见为 nan。

    轮廓 = 各截面投影曲线的包络。包络条件：像面上展向切向量 ∂p/∂s 与周向切向量 ∂p/∂c
    平行(叉积为 0)。沿周线找叉积变号处线性插值，取离展向轴最远的左右两个。
    with_rings=True 时另返回根/尖端面的投影环 (3, 2, N, 2)，它们也是可见轮廓的一部分。
    geometry 可复用同一次状态求值的 (V,A)，仅避免逐相机重复生成相同网格。"""
    V, A = rotor.forward(state) if geometry is None else geometry
    S, N = V.shape[1], V.shape[2]
    out = np.full((3, S, 2, 2), np.nan)
    rings = np.full((3, 2, N + 1, 2), np.nan)
    for b in range(3):
        uvr, zr = cam.project(V[b])                      # (S,N,2)
        uva, za = cam.project(A[b])
        ds = np.gradient(uvr, axis=0)
        dc = 0.5 * (np.roll(uvr, -1, axis=1) - np.roll(uvr, 1, axis=1))
        cr = ds[..., 0] * dc[..., 1] - ds[..., 1] * dc[..., 0]          # (S,N)
        cr1 = np.roll(cr, -1, axis=1)
        p1 = np.roll(uvr, -1, axis=1)
        chg = (cr * cr1 <= 0) & np.isfinite(cr) & np.isfinite(cr1)
        t = np.where(chg, cr / np.where(cr - cr1 == 0, 1e-12, cr - cr1), 0.0)
        q = uvr + np.clip(t, 0, 1)[..., None] * (p1 - uvr)               # 候选轮廓点
        d = np.gradient(uva, axis=0)
        nrm = np.stack([-d[:, 1], d[:, 0]], 1)
        nrm /= np.linalg.norm(nrm, axis=1, keepdims=True) + 1e-9
        off = ((q - uva[:, None, :]) * nrm[:, None, :]).sum(-1)
        off_ext = ((uvr - uva[:, None, :]) * nrm[:, None, :]).sum(-1)
        hi = np.where(chg, off, -np.inf); lo = np.where(chg, off, np.inf)
        i_hi, i_lo = hi.argmax(1), lo.argmin(1)
        has = chg.any(1)
        # 无变号(端面正对相机等)时退回"最外点"
        e_hi = np.nan_to_num(off_ext, nan=-np.inf).argmax(1)
        e_lo = np.nan_to_num(off_ext, nan=np.inf).argmin(1)
        ar = np.arange(S)
        out[b, :, 0] = np.where(has[:, None], q[ar, i_hi], uvr[ar, e_hi])
        out[b, :, 1] = np.where(has[:, None], q[ar, i_lo], uvr[ar, e_lo])
        good = np.isfinite(uvr).all((1, 2)) & (za > 0.5)
        out[b, ~good] = np.nan
        if with_rings:
            for k, i in enumerate((0, S - 1)):
                if good[i]:
                    rings[b, k] = np.concatenate([uvr[i], uvr[i, :1]], 0)
    return (out, rings) if with_rings else out


DENSE = 4                      # 截面之间轮廓插值倍数


def densify(sil, k=DENSE):
    """(3,S,2,2) → (3,(S-1)k+1,2,2)：在相邻截面之间线性插值轮廓点(近处叶片截面在像面上相隔很远)。"""
    S = sil.shape[1]
    t = np.linspace(0, S - 1, (S - 1) * k + 1)
    i0 = np.minimum(np.floor(t).astype(int), S - 2)
    w = (t - i0)[None, :, None, None]
    return (1 - w) * sil[:, i0] + w * sil[:, i0 + 1]


def point_to_polylines(P, L):
    """P (K,2) 到一组折线 L (M,S,2)(nan 截断) 的最近距离 (K,)。"""
    a, b = L[:, :-1].reshape(-1, 2), L[:, 1:].reshape(-1, 2)
    ok = np.isfinite(a).all(1) & np.isfinite(b).all(1)
    a, b = a[ok], b[ok]
    if len(a) == 0 or len(P) == 0:
        return np.full(len(P), np.nan)
    ab = b - a
    t = ((P[:, None, :] - a[None]) * ab[None]).sum(-1) / ((ab ** 2).sum(-1)[None] + 1e-9)
    t = np.clip(t, 0, 1)
    q = a[None] + t[..., None] * ab[None]
    return np.sqrt(((P[:, None, :] - q) ** 2).sum(-1)).min(1)


# ───────────────────────── 单帧求解 ─────────────────────────
def robust(r, f=EDGE_PX):
    """把残差变换成 soft_l1 等价形式：sum(robust(r)²) = sum 2f²(√(1+(r/f)²)−1)。"""
    return r * np.sqrt(2.0 / (np.sqrt(1.0 + (r / f) ** 2) + 1.0))


class Fitter:
    def __init__(self, rotor, cams):
        self.rotor, self.cams = rotor, cams
        self.texture_features = None
        self.S = len(rotor.tpl.r)
        self.mu, self.sig = PRIOR_MU.copy(), PRIOR_SIG.copy()   # 物理先验(由 PhysPrior 每帧更新)

    def full(self, p):
        p = np.asarray(p, float)
        if p.ndim != 1 or len(p) not in (10, N_STATE):
            raise ValueError('state must contain 10 legacy or 13 full model directions')
        s = np.zeros(N_STATE)
        s[:len(p)] = p
        return s

    def residuals(self, p, obs, x_pred, w_prior):
        st = self.full(p)
        r_model, r_obs = [], []
        use_texture = self.texture_features is not None and self.texture_features.count > 0
        geometry = self.rotor.forward(st) if any(o.has for o in obs) or use_texture else None
        for cam, o in zip(self.cams, obs):
            if o.has:
                sil0, rings = silhouettes(self.rotor, cam, st, with_rings=True, geometry=geometry)
                sil = densify(sil0)                                   # (3,S',2,2)
                d = o.model_residual(sil.reshape(-1, 2))
                r_model.append(d)
                dd = np.fmin(point_to_polylines(o.pts, np.concatenate([sil[:, :, 0], sil[:, :, 1]], 0)),
                             point_to_polylines(o.pts, rings.reshape(-1, rings.shape[2], 2)))
                rr = np.where(np.isfinite(dd), dd, 30.0)
                r_obs.append(np.pad(rr, (0, 200 - len(rr))))
            else:
                r_model.append(np.zeros(3 * ((self.S - 1) * DENSE + 1) * 2))
                r_obs.append(np.zeros(200))
        r_prior = w_prior * (p - x_pred) / SIG_STEP[:len(p)]
        d_abs = p - self.mu[:len(p)]
        d_abs[0] = 0.0
        r_abs = d_abs / self.sig[:len(p)]
        r_img = robust(np.concatenate(r_model + [0.7 * np.concatenate(r_obs)]))
        if use_texture:
            r_img = np.concatenate([r_img, robust(self.texture_features.residuals(geometry[0]))])
        # 先验保持二次型(不经鲁棒变换)，否则大偏离时惩罚变线性、拉不住看不见的叶片
        return np.concatenate([r_img, r_prior * EDGE_PX, r_abs * EDGE_PX])

    def cost(self, p, obs):  # noqa: D401
        r = self.residuals(p, obs, p, 0.0)
        return float(np.sum(r ** 2))

    def solve(self, obs, x0, x_pred, w_prior=1.0, max_nfev=60, active=None):
        """Use absolute increments independent of unfolded azimuth magnitude.

        std describes information from image observations, not regularization.
        A direction having a component in the image nullspace is unavailable
        (NaN internally, JSON null in observation_std). The optimizer's original
        status is retained separately from reconstruction availability.
        """
        if len(obs) != len(self.cams):
            raise ValueError('observation/camera slots differ')
        act = np.ones(len(x0), bool) if active is None else np.asarray(active, bool)
        if act.shape != (len(x0),) or not act.any():
            raise ValueError('at least one valid active state direction is required')
        x0 = np.asarray(x0, float)
        self.full(x0)  # validate explicit dimensional contract before broadcasting
        x_pred = np.asarray(x_pred, float)
        if x_pred.shape != x0.shape:
            raise ValueError('state and temporal prediction dimensions differ')
        n = len(x0)
        x0 = np.clip(x0, LB[:n], UB[:n])

        xs = np.array([1.0] * 4 + [0.5] * 3 + [0.1] * 3 + [0.2] * 3)[:n]
        # A constant origin prevents SciPy's strictly-feasible adjustment at
        # an active bound from turning a zero increment into a tiny nonzero
        # initial TRF radius. Physical x0, bounds and residuals stay identical.
        q_ref = np.zeros(act.sum())
        q_ref[0] = xs[act][0]

        def f(q):
            p = x0.copy(); p[act] += q - q_ref
            return self.residuals(p, obs, x_pred, w_prior)

        # Optimize frame-local increments: xtol and trust geometry must not
        # depend on the accumulated revolutions of the saved azimuth.
        # A 1e-3 degree stencil crossed near-view pixel/nearest-edge branches
        # and even reversed the cost gradient. 1e-6 steps were checked against
        # 1e-7 on the frozen failures; their minima and states agree.
        # No modulo is applied to the saved state or temporal prediction.
        steps = np.full(n, 1e-6)[act]
        lower, upper = (LB[:n] - x0)[act] + q_ref, (UB[:n] - x0)[act] + q_ref
        def jac(q):
            columns = []
            for j, h in enumerate(steps):
                plus, minus = q.copy(), q.copy()
                plus[j] = min(q[j] + h, upper[j])
                minus[j] = max(q[j] - h, lower[j])
                columns.append((f(plus) - f(minus)) / (plus[j] - minus[j]))
            return np.stack(columns, axis=1)

        res = least_squares(f, q_ref, bounds=(lower, upper), loss='linear',
                            x_scale=xs[act], jac=jac, max_nfev=max_nfev)
        x = x0.copy(); x[act] += res.x - q_ref
        res.x = x[act].copy()
        res.coordinate_reference = q_ref.copy()
        std = np.full(len(x0), np.nan)
        # Last 2*n_state residuals are temporal/physical priors. They may keep
        # the solve finite but cannot establish image identifiability.
        J = res.jac[:-2 * len(x0)]
        scaled_J = J * xs[act][None, :]
        _, singular, vh = np.linalg.svd(scaled_J, full_matrices=False)
        threshold = max(float(singular[0]) * 1e-7, 1e-9) if len(singular) else 1e-9
        rank = int(np.count_nonzero(singular > threshold))
        identifiable = np.zeros(len(x0), bool)
        if rank:
            null_energy = np.clip(1.0 - (vh[:rank] ** 2).sum(axis=0), 0.0, 1.0)
            good = null_energy < 1e-8
            identifiable[act] = good
            cov_scaled = (vh[:rank].T / singular[:rank] ** 2) @ vh[:rank] * EDGE_PX ** 2
            sigma = np.sqrt(np.clip(np.diag(cov_scaled), 0, None)) * xs[act]
            std[np.flatnonzero(act)[good]] = sigma[good]
        res.observation_rank = rank
        res.contour_observation_camera_count = sum(bool(o.has) for o in obs)
        texture_slots = (set(e['camera_index'] for e in self.texture_features.entries)
                         if self.texture_features is not None else set())
        res.observation_camera_count = len({i for i, o in enumerate(obs) if o.has} | texture_slots)
        res.identifiable = identifiable
        res.reconstruction_valid = bool(res.success and rank > 0)
        res.reconstruction_status = ('no_observations' if not res.observation_camera_count else
                                     'unidentifiable' if rank == 0 else
                                     'image_constrained' if identifiable.all() else 'prior_assisted')
        res.posterior_std = np.full(len(x0), np.nan)
        try:
            _, ps, pv = np.linalg.svd(res.jac * xs[act][None, :], full_matrices=False)
            pr = int(np.count_nonzero(ps > max(ps[0] * 1e-7, 1e-9)))
            pg = np.clip(1.0 - (pv[:pr] ** 2).sum(axis=0), 0.0, 1.0) < 1e-8
            if pr:
                pcov = (pv[:pr].T / ps[:pr] ** 2) @ pv[:pr] * EDGE_PX ** 2
                psigma = np.sqrt(np.clip(np.diag(pcov), 0, None)) * xs[act]
                res.posterior_std[np.flatnonzero(act)[pg]] = psigma[pg]
        except np.linalg.LinAlgError:
            pass
        if self.texture_features is not None:
            texture_rows = 2 * self.texture_features.count
            contour_rows = len(J) - texture_rows
            names = STATE_NAMES[:len(x0)]
            res.image_information = {
                'contour_only': image_information(J[:contour_rows], xs, names, act),
                'texture_only': image_information(J[contour_rows:], xs, names, act),
                'combined': image_information(J, xs, names, act)}
            # Before/after scores are in original pixels even with --fit-scale.
            before = self.texture_features.summary(self.rotor.forward(self.full(x0))[0])
            after = self.texture_features.summary(self.rotor.forward(self.full(x))[0])
            res.texture_geometry = after
            res.texture_geometry['before_solve'] = {k: before[k] for k in (
                'original_pixel_error_rms', 'original_pixel_error_p95', 'weighted_robust_cost')}
            res.texture_geometry['contour_robust_cost'] = float(.5 * res.fun[:contour_rows] @ res.fun[:contour_rows])
            res.texture_geometry['prior_cost'] = float(.5 * res.fun[-2 * len(x0):] @ res.fun[-2 * len(x0):])
            res.texture_geometry['image_information'] = res.image_information
        return x, std, res

    def in_frame(self, p, xi_outer=0.4):
        """仅凭几何预测：每片叶片有多少截面(全部 / 外侧)落在任一路画面内。"""
        st = self.full(p)
        inside = np.zeros((3, self.S), bool)
        for cam in self.cams:
            sil = silhouettes(self.rotor, cam, st)
            u, v = sil[..., 0], sil[..., 1]
            ok = (np.isfinite(u) & (u >= 0) & (u < cam.W) & (v >= 0) & (v < cam.H)).all(-1)
            inside |= ok
        outer = self.rotor.tpl.xi > xi_outer
        return inside.sum(1), inside[:, outer].sum(1)

    def observed_sections(self, p, obs, tol=3.0):
        """逐叶片逐截面：至少一路相机里两条轮廓点都入画且贴合观测轮廓(<tol px)。"""
        st = self.full(p)
        seen = np.zeros((3, self.S), bool)
        for cam, o in zip(self.cams, obs):
            if not o.has:
                continue
            sil = densify(silhouettes(self.rotor, cam, st))
            d = o.sample_dt(sil.reshape(-1, 2)).reshape(3, -1, 2)
            ok = np.all(np.isfinite(d) & (d < tol), axis=2)          # (3,S')
            for i in range(self.S):                                  # 截面 i 管辖其两侧半格
                lo, hi = max(0, i * DENSE - DENSE // 2), min(ok.shape[1], i * DENSE + DENSE // 2 + 1)
                seen[:, i] |= ok[:, lo:hi].any(1)
        return seen


class RLS:
    """带遗忘因子的递推最小二乘：y ≈ h·θ。"""

    def __init__(self, n, lam=0.99, p0=100.0):
        self.th = np.zeros(n)
        self.P = np.eye(n) * p0
        self.lam, self.n_upd = lam, 0

    def update(self, h, y, w=1.0):
        h = np.asarray(h, float)
        Ph = self.P @ h
        k = Ph / (self.lam / w + h @ Ph)
        self.th = self.th + k * (y - h @ self.th)
        self.P = (self.P - np.outer(k, Ph)) / self.lam
        self.n_upd += 1

    def predict(self, h):
        h = np.asarray(h, float)
        return float(h @ self.th), float(np.sqrt(max(h @ self.P @ h, 0.0)))


class PhysPrior:
    """周期物理先验 —— 只用"看得见"的叶片拟合，给看不见的叶片按自己的方位角推算：
        挥舞 flap_b = F0 + F1·cos(ψ_b) + o_b      (平均推力 + 风切变 1P + 逐叶偏置/气动不平衡)
        摆振 edge_b = E0 + Es·sin(ψ_b) + Ec·cos(ψ_b)  (重力 1P，叶片水平时最大)
        桨距 pitch_b = P0 + q_b                    (统一桨距 + 逐叶偏差)
    逐叶偏置 o_b、q_b 也是拟合量，所以不平衡不会被先验抹平；约束 Σo_b=0、Σq_b=0 消除冗余。"""

    SIG_FLAP, SIG_EDGE, SIG_PITCH = 0.4, 0.12, 0.8     # 物理先验的残余散布(1σ)

    def __init__(self):
        self.flap = RLS(5); self.edge = RLS(3); self.pitch = RLS(4)
        self.flap.th[0] = 3.0
        for _ in range(3):                              # 软约束：偏置和为零
            self.flap.update([0, 0, 1, 1, 1], 0.0, w=5.0)
            self.pitch.update([0, 1, 1, 1], 0.0, w=5.0)
        self.flap.n_upd = self.edge.n_upd = self.pitch.n_upd = 0

    @staticmethod
    def _h(psi_b, b):
        c, s = math.cos(math.radians(psi_b)), math.sin(math.radians(psi_b))
        oh = [1.0 if i == b else 0.0 for i in range(3)]
        return [1.0, c] + oh, [1.0, s, c], [1.0] + oh

    def update(self, x, seen, xi, min_frac=0.15):
        """seen (3,S) 逐截面观测标记；挥舞/摆振主要体现在叶片外侧，只用 ξ>0.4 的观测比例加权。"""
        outer = xi > 0.4
        for b in range(3):
            w_all, w_out = seen[b].mean(), seen[b, outer].mean()
            hf, he, hp = self._h(x[0] + 120 * b, b)
            if w_out >= min_frac:
                self.flap.update(hf, x[4 + b], w_out)
                self.edge.update(he, x[7 + b], w_out)
            if w_all >= min_frac:
                self.pitch.update(hp, x[1 + b], w_all)

    def apply(self, fitter, psi):
        mu, sig = PRIOR_MU.copy(), PRIOR_SIG.copy()
        ready = min(self.flap.n_upd, self.edge.n_upd) > 6
        for b in range(3):
            hf, he, hp = self._h(psi + 120 * b, b)
            for idx, rls, h, s0 in ((4 + b, self.flap, hf, self.SIG_FLAP),
                                    (7 + b, self.edge, he, self.SIG_EDGE),
                                    (1 + b, self.pitch, hp, self.SIG_PITCH)):
                m, sp = rls.predict(h)
                if ready:
                    mu[idx], sig[idx] = m, math.hypot(s0, sp)
        fitter.mu, fitter.sig = mu, sig


def init_psi(fitter, obs, lo=0.0, hi=120.0, step=2.0, pitch=0.0, flap=3.0, state=None):
    """Phase search; a supplied state preserves per-blade fit directions.

    The second image uses a 0.1 degree grid and 0.01 degree local refinement
    because near-view cost basins can be narrower than the former 0.5+ grid.
    The first-image 0--120 search and its default step remain unchanged.
    """
    started = time.perf_counter()
    coarse_count, fine_count = 0, 0
    best = (1e18, lo)
    base = (np.array([lo] + [pitch] * 3 + [flap] * 3 + [0.0] * 6)
            if state is None else np.asarray(state, float).copy())
    for psi in np.arange(lo, hi, step):
        p = base.copy(); p[0] = psi
        c = fitter.cost(p, obs)
        coarse_count += 1
        if c < best[0]:
            best = (c, psi)
    if state is not None:
        center = best[1]
        for psi in np.arange(max(lo, center - step), min(hi, center + step), step / 10.0):
            p = base.copy(); p[0] = psi
            c = fitter.cost(p, obs)
            fine_count += 1
            if c < best[0]:
                best = (c, psi)
    fitter.phase_search = {'lo_deg': float(lo), 'hi_deg': float(hi),
                           'coarse_step_deg': float(step),
                           'fine_step_deg': None if state is None else float(step / 10.0),
                           'coarse_residual_calls': coarse_count,
                           'fine_residual_calls': fine_count,
                           'elapsed_s': time.perf_counter() - started,
                           'state_source': 'default_symmetric' if state is None else 'previous_per_blade_image_state',
                           'selected_psi_deg': float(best[1]), 'selected_cost': float(best[0])}
    return best[1]


# ───────────────────────── 主流程 ─────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--masks', default=None, help='外部掩码根目录：<masks>/<相机名>/%%06d.png')
    ap.add_argument('--fit-scale', type=float, default=1.0, help='求解时把图像再缩放(提速)')
    ap.add_argument('--max-frames', type=int, default=0)
    ap.add_argument('--rpm-max', type=float, default=20.0)
    ap.add_argument('--initial-max-nfev', type=int, default=150,
                    help='首次和第二次有效图像的最大求解评估次数(默认150)')
    ap.add_argument('--tracking-max-nfev', type=int, default=60,
                    help='后续有效图像的最大求解评估次数(默认60)')
    ap.add_argument('--overlay', action='store_true', help='输出模型轮廓叠加视频(质检用)')
    ap.add_argument('--polarity', choices=['dark', 'bright'], default='dark',
                    help='无外部掩码时：叶片比天空暗(dark)还是亮(bright)')
    ap.add_argument('--texture-observations', default=None,
                    help='声明RGB固定身份侧车；缺省关闭纹理辅助几何')
    ap.add_argument('--texture-weight', type=float, default=1.0,
                    help='纹理重投影残差权重；0为保留门控审计的轮廓消融')
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    global POLARITY
    POLARITY = args.polarity

    if not 0 < args.fit_scale <= 1:
        raise ValueError('--fit-scale must be in (0,1]')
    if args.max_frames < 0:
        raise ValueError('--max-frames must be nonnegative')
    if args.initial_max_nfev < 1 or args.tracking_max_nfev < 1:
        raise ValueError('--initial-max-nfev and --tracking-max-nfev must be >= 1')
    inputs = InputSequence(args.data, masks_dir=args.masks, segmenter=segment,
                           max_frames=args.max_frames)
    if inputs.metadata['mask_source'] == 'automatic segmentation':
        inputs.metadata.update({'mask_polarity': POLARITY, 'min_contrast': MIN_CONTRAST,
                                'min_area_frac': MIN_AREA_FRAC})
    cfg, fps = inputs.cfg, inputs.fps
    rotor = Rotor(cfg)
    fitter = Fitter(rotor, [])
    texture_geometry = (TextureGeometry(args.texture_observations, inputs, rotor, args.texture_weight)
                        if args.texture_observations is not None else None)
    writers = None

    frames, x_prev, omega = [], None, None
    # Finite solver candidates can seed another solve; only accepted image
    # states establish motion, temporal predictions and physical priors.
    x_image_prev, image_previous_t = None, None
    x_candidate_prev, candidate_previous_t = None, None
    phys = PhysPrior()
    last_obs = np.array([np.nan] + [0.0] * 3 + [np.nan] * 3 + [0.0] * 6)
    last_obs[4:7] = 3.0
    k, t0 = 0, time.time()
    while not args.max_frames or k < args.max_frames:
        record = inputs.read()
        if record is None:
            break
        cams_full, rgbs = record.cameras, record.rgbs
        cams = [c.scaled(args.fit_scale) for c in cams_full] if args.fit_scale != 1.0 else cams_full
        fitter.cams = cams
        if texture_geometry is not None:
            fitter.texture_features = texture_geometry.prepare(record, cams)
        imgs = [cv2.cvtColor(img, cv2.COLOR_RGB2GRAY) for img in rgbs]
        obs = []
        for camera_index, (c, m) in enumerate(zip(cams, record.masks)):
            supports = getattr(record, 'known_contours', None)
            known = None if supports is None else supports[camera_index]
            if args.fit_scale != 1.0:
                m = cv2.resize(m, (c.W, c.H), interpolation=cv2.INTER_NEAREST)
                if known is not None:
                    known = cv2.resize(known.astype(np.uint8),(c.W,c.H),interpolation=cv2.INTER_NEAREST)>0
            obs.append(Obs(m, known=known))

        has_image = (any(o.has for o in obs) or
                     (fitter.texture_features is not None and fitter.texture_features.count > 0))
        image_dt = None if image_previous_t is None or not has_image else record.t - image_previous_t
        prediction = PRIOR_MU.copy() if x_image_prev is None else x_image_prev.copy()
        if omega is not None:
            prediction[0] += omega * (record.t - image_previous_t)
        phase_search = None
        if not has_image:
            x0 = prediction.copy()
            x, std, res = fitter.solve(obs, x0, x0, w_prior=0.0, max_nfev=2)
            res.reconstruction_status = 'no_observations' if x_prev is None else 'prior_prediction'
        elif x_image_prev is None:                         # 首次有效图像：全局网格搜方位角
            psi0 = init_psi(fitter, obs)
            phase_search = fitter.phase_search.copy()
            x0 = np.array([psi0] + [0.0] * 3 + [3.0] * 3 + [0.0] * 6)
            x, std, res = fitter.solve(obs, x0, x0, w_prior=0.0, max_nfev=args.initial_max_nfev)
        elif omega is None:                               # 第二次有效图像：用真实观测间隔估初速
            span = args.rpm_max * 6.0 * image_dt
            psi1 = init_psi(fitter, obs, x_image_prev[0] - span, x_image_prev[0] + span,
                            step=0.1, pitch=float(np.mean(x_image_prev[1:4])),
                            flap=float(np.mean(x_image_prev[4:7])), state=x_image_prev)
            phase_search = fitter.phase_search.copy()
            x0 = x_image_prev.copy(); x0[0] = psi1
            x, std, res = fitter.solve(obs, x0, x0, w_prior=0.2, max_nfev=args.initial_max_nfev)
        else:
            xp = prediction.copy()
            phys.apply(fitter, xp[0])
            n_all, n_out = fitter.in_frame(xp)
            active = np.ones(N_STATE, bool)
            ready = phys.flap.n_upd > 6
            for b in range(3):
                if n_out[b] < 3:                             # 外侧看不见 → 挥舞/摆振不可观，取推算值
                    active[[4 + b, 7 + b, 10 + b]] = False
                    xp[4 + b] = fitter.mu[4 + b] if ready else last_obs[4 + b]
                    xp[7 + b] = fitter.mu[7 + b] if ready else last_obs[7 + b]
                    xp[10 + b] = last_obs[10 + b]
                if n_all[b] < 3:                             # 整片看不见 → 桨距也取推算值
                    active[1 + b] = False
                    xp[1 + b] = fitter.mu[1 + b] if ready else last_obs[1 + b]
            x0 = x_candidate_prev.copy()
            x0[0] += omega * (record.t - candidate_previous_t)
            x0[~active] = xp[~active]
            x, std, res = fitter.solve(obs, x0, xp, w_prior=0.5, active=active,
                                       max_nfev=args.tracking_max_nfev)
        finite_solution = bool(np.isfinite(x).all())
        res.reconstruction_valid = bool(has_image and res.success and
                                         res.reconstruction_valid and finite_solution)
        if has_image and finite_solution:
            x_candidate_prev, candidate_previous_t = x.copy(), record.t
        if not finite_solution:
            x = prediction.copy()
            std = np.full(len(x), np.nan)
            res.posterior_std = np.full(len(x), np.nan)
            res.reconstruction_status = 'nonfinite_solution'
        seen = np.zeros((3, fitter.S), bool)
        if res.reconstruction_valid:
            if x_image_prev is not None:
                observed_omega = (x[0] - x_image_prev[0]) / image_dt
                omega = observed_omega if omega is None else 0.8 * omega + 0.2 * observed_omega
            x_image_prev, image_previous_t = x.copy(), record.t
            seen = fitter.observed_sections(x, obs)
            phys.update(x, seen, rotor.tpl.xi)
        # 记录每片叶片最近一次"真正看清"时的值，先验未就绪时用来填补
        outer = rotor.tpl.xi > 0.4
        for b in range(3):
            if seen[b, outer].mean() >= 0.15:
                last_obs[[4 + b, 7 + b, 10 + b]] = x[[4 + b, 7 + b, 10 + b]]
            if seen[b].mean() >= 0.15:
                last_obs[1 + b] = x[1 + b]
        if not np.isfinite(last_obs[4:7]).all() and np.isfinite(last_obs[4:7]).any():
            for g in (1, 4, 7):                              # 没看清过的叶片：先借其他叶片的值
                blk = last_obs[g:g + 3]
                blk[~np.isfinite(blk)] = np.nanmean(blk)
        st = fitter.full(x)
        if texture_geometry is not None:
            texture_geometry.bind_anchors(record, st,
                geometry_valid=bool(res.reconstruction_valid and any(o.has for o in obs)))
        def nullable(a):
            return [float(v) if np.isfinite(v) else None for v in a]
        saved_cams = []
        for c in cams_full:
            d = c.to_dict()
            d['T_cv_from_model'] = d.pop('T_cv_from_world')
            saved_cams.append(d)
        row = {
            'frame': record.frame, 't': record.t, 'state': st.tolist(),
            'observation_std': nullable(np.concatenate([std, np.full(N_STATE - len(std), np.nan)])),
            'uncertainty_status': ['image_identified' if np.isfinite(v) else 'unbounded_or_unobserved' for v in std]
                                  + ['fixed_model_direction'] * (N_STATE - len(std)),
            'posterior_std': nullable(res.posterior_std),
            'reconstruction_status': res.reconstruction_status,
            'reconstruction_valid': res.reconstruction_valid,
            'observation_rank': res.observation_rank,
            'observation_camera_count': res.observation_camera_count,
            'geometry_acceptance': 'NOT_EVALUATED',
            'contour_support': [{'camera': c.name,
                'unknown_pixel_fraction': 0.0 if getattr(record, 'known_contours', None) is None
                   else float(1.0-np.mean(record.known_contours[i]))}
                  for i,c in enumerate(cams_full)],
            'solver': {'success': bool(res.success), 'status': int(res.status),
                       'message': str(res.message), 'optimality': float(res.optimality),
                       'gradient_inf_norm': float(np.linalg.norm(res.grad, ord=np.inf)),
                       'parameterization': 'frame_local_increments_with_constant_origin',
                       'coordinate_reference': res.coordinate_reference.tolist(), 'jacobian_absolute_step': 1e-6},
            'initialization_search': phase_search,
            'cameras': saved_cams,
            'rpm': abs(omega or 0.0) / 6.0,
            'rpm_status': 'not_initialized' if omega is None else 'image_estimated',
            'rpm_observation_dt': image_dt if res.reconstruction_valid else None,
            'image_initialized': x_image_prev is not None,
            'cost': float(res.cost), 'nfev': int(res.nfev),
            'observed_sections': seen.astype(int).tolist(),
        }
        if texture_geometry is not None:
            row['texture_geometry'] = res.texture_geometry
        # The legacy display reader validates finite std when present. Unknown
        # image uncertainty is represented by explicit nullable fields, never
        # by zero or nonstandard NaN/Infinity JSON.
        if np.isfinite(std).all():
            row['std'] = np.concatenate([std, np.zeros(N_STATE - len(std))]).tolist()
        frames.append(row)
        x_prev = x

        if args.overlay:
            if writers is None:
                writers = [cv2.VideoWriter(os.path.join(args.out, 'overlay_%s.mp4' % c.name),
                                           cv2.VideoWriter_fourcc(*'mp4v'), fps, (c.W, c.H))
                           for c in cams_full]
            for c, img, w in zip(cams_full, imgs, writers):
                vis = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
                sil = silhouettes(rotor, c, st)
                for b, col in enumerate([(0, 0, 255), (0, 200, 0), (255, 120, 0)]):
                    for side in range(2):
                        pts = sil[b, :, side]
                        okp = np.isfinite(pts).all(1)
                        if okp.sum() > 1:
                            cv2.polylines(vis, [np.round(pts[okp]).astype(np.int32)], False, col, 1)
                w.write(vis)

        if k % 10 == 0:
            print('frame %4d  psi=%7.2f  pitch=%s  flap=%s  edge=%s  rpm=%.2f  (%.2fs/帧)'
                  % (k, x[0] % 360, np.round(x[1:4], 2), np.round(x[4:7], 2),
                     np.round(x[7:10], 2), (omega or 0) / 6.0, (time.time() - t0) / (k + 1)))
        k += 1

    inputs.close()
    if writers:
        for w in writers:
            w.release()
    result = {
        'turbine': cfg.to_dict(), 'fps': fps, 'state_names': STATE_NAMES,
        'params': {'initial_max_nfev': args.initial_max_nfev,
                   'tracking_max_nfev': args.tracking_max_nfev,
                   'no_observation_max_nfev': 2},
        'note': 'state 由 model.Rotor.forward 还原网格；observed_sections=1 为图像约束，0 为模型推断；求解器终止不代表真实精度验收',
        'input_contract': inputs.metadata, 'input_access_log': inputs.access_log,
        'uncertainty_contract': 'observation_std uses image Jacobian only; null means unbounded/unobserved; posterior_std includes priors',
        'frames': frames}
    if texture_geometry is not None:
        result['texture_geometry'] = texture_geometry.audit()
        result['params']['texture_weight'] = args.texture_weight
        result['input_access_log'] = inputs.access_log + texture_geometry.access_log
    save_json(os.path.join(args.out, 'recon.json'), result)
    print('完成 %d 帧，平均 %.2f s/帧 → %s' % (k, (time.time() - t0) / max(k, 1),
                                         os.path.join(args.out, 'recon.json')))


if __name__ == '__main__':
    main()
