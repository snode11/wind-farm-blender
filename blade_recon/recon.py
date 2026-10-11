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

from camera import Camera
from model import Rotor, TurbineConfig, N_STATE, STATE_NAMES, default_state, load_json, save_json

EST = list(range(10))          # 估计 psi, pitch×3, flap×3, edge×3；flap2 固定为 0
LB = np.array([-1e9] + [-5.0] * 3 + [-3.0] * 3 + [-2.0] * 3)
UB = np.array([1e9] + [90.0] * 3 + [12.0] * 3 + [2.0] * 3)
# 帧间变化先验(1σ)：方位角相对转速外推，其余相对上一帧
SIG_STEP = np.array([3.0] + [1.5] * 3 + [0.8] * 3 + [0.25] * 3)
# 弱绝对先验：看不到的叶片不会漂到不合理的值
PRIOR_MU = np.array([0.0] + [0.0] * 3 + [3.0] * 3 + [0.0] * 3)
PRIOR_SIG = np.array([1e9] + [25.0] * 3 + [4.0] * 3 + [1.0] * 3)
EDGE_PX = 2.0                  # 鲁棒损失尺度(像素)


# ───────────────────────── 输入 ─────────────────────────
class FrameSource:
    def __init__(self, data_dir, name):
        v = os.path.join(data_dir, name + '.mp4')
        self.cap, self.files = None, None
        if os.path.exists(v):
            self.cap = cv2.VideoCapture(v)
        else:
            self.files = sorted(glob.glob(os.path.join(data_dir, name, '*.png'))
                                + glob.glob(os.path.join(data_dir, name, '*.jpg')))
            if not self.files:
                raise FileNotFoundError('找不到 %s.mp4 或 %s/ 帧目录' % (name, name))
        self.k = 0

    def read(self):
        if self.cap is not None:
            ok, img = self.cap.read()
            if not ok:
                return None
        else:
            if self.k >= len(self.files):
                return None
            img = cv2.imread(self.files[self.k])
        self.k += 1
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img


MIN_CONTRAST = 12             # 自动阈值的最低对比度(灰度级)
MIN_AREA_FRAC = 0.002         # 连通域最小面积(占画面比例)
POLARITY = 'dark'             # 叶片相对天空：dark=更暗(可见光逆光)，bright=更亮(近红外补光)


def segment(gray, mask_path=None):
    """叶片掩码。有外部掩码(YOLO 等)就用外部的；否则按天空背景做 Otsu 自动阈值。
    背景取逐行分位数(天空上下渐变；叶片占半行以上时中位数会失效，所以用 95/5 分位)。"""
    if mask_path and os.path.exists(mask_path):
        m = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
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

    def __init__(self, mask, n_pts=200, border=3):
        self.mask = mask
        H, W = mask.shape
        edge = cv2.morphologyEx(mask, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
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


# ───────────────────────── 模型轮廓 ─────────────────────────
def silhouettes(rotor, cam, state, with_rings=False):
    """每片叶片每个截面的左/右轮廓点 (3, S, 2, 2)，不可见为 nan。

    轮廓 = 各截面投影曲线的包络。包络条件：像面上展向切向量 ∂p/∂s 与周向切向量 ∂p/∂c
    平行(叉积为 0)。沿周线找叉积变号处线性插值，取离展向轴最远的左右两个。
    with_rings=True 时另返回根/尖端面的投影环 (3, 2, N, 2)，它们也是可见轮廓的一部分。"""
    V, A = rotor.forward(state)
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
    return np.sign(r) * np.sqrt(2.0 * f * f * (np.sqrt(1.0 + (r / f) ** 2) - 1.0))


class Fitter:
    def __init__(self, rotor, cams):
        self.rotor, self.cams = rotor, cams
        self.S = len(rotor.tpl.r)
        self.mu, self.sig = PRIOR_MU.copy(), PRIOR_SIG.copy()   # 物理先验(由 PhysPrior 每帧更新)

    def full(self, p):
        s = np.zeros(N_STATE)
        s[EST] = p
        return s

    def residuals(self, p, obs, x_pred, w_prior):
        st = self.full(p)
        r_model, r_obs = [], []
        for cam, o in zip(self.cams, obs):
            sil0, rings = silhouettes(self.rotor, cam, st, with_rings=True)
            sil = densify(sil0)                                       # (3,S',2,2)
            if o.has:
                d = o.sample_dt(sil.reshape(-1, 2))
                r_model.append(np.where(np.isfinite(d), d, 0.0))
                dd = np.fmin(point_to_polylines(o.pts, np.concatenate([sil[:, :, 0], sil[:, :, 1]], 0)),
                             point_to_polylines(o.pts, rings.reshape(-1, rings.shape[2], 2)))
                rr = np.where(np.isfinite(dd), dd, 30.0)
                r_obs.append(np.pad(rr, (0, 200 - len(rr))))
            else:
                r_model.append(np.zeros(3 * ((self.S - 1) * DENSE + 1) * 2))
                r_obs.append(np.zeros(200))
        r_prior = w_prior * (p - x_pred) / SIG_STEP
        d_abs = p - self.mu
        d_abs[0] = 0.0
        r_abs = d_abs / self.sig
        r_img = robust(np.concatenate(r_model + [0.7 * np.concatenate(r_obs)]))
        # 先验保持二次型(不经鲁棒变换)，否则大偏离时惩罚变线性、拉不住看不见的叶片
        return np.concatenate([r_img, r_prior * EDGE_PX, r_abs * EDGE_PX])

    def cost(self, p, obs):  # noqa: D401
        r = self.residuals(p, obs, p, 0.0)
        return float(np.sum(r ** 2))

    def solve(self, obs, x0, x_pred, w_prior=1.0, max_nfev=60, active=None):
        """active：哪些参数参与优化(布尔，长 10)。不可观的参数固定在 x0(=物理先验推算值)，
        不让它们去吸收别处的残差。"""
        act = np.ones(len(x0), bool) if active is None else np.asarray(active, bool)
        x0 = np.clip(x0, LB, UB)

        def f(q):
            p = x0.copy(); p[act] = q
            return self.residuals(p, obs, x_pred, w_prior)

        xs = np.array([1.0] + [1.0] * 3 + [0.5] * 3 + [0.1] * 3)
        res = least_squares(f, x0[act], bounds=(LB[act], UB[act]), loss='linear',
                            x_scale=xs[act], diff_step=1e-3, max_nfev=max_nfev)
        x = x0.copy(); x[act] = res.x
        std = self.sig.copy()                          # 固定参数的不确定度 = 先验散布
        try:
            J = res.jac
            cov = np.linalg.pinv(J.T @ J) * EDGE_PX ** 2
            std[act] = np.sqrt(np.clip(np.diag(cov), 0, None))
        except Exception:
            std[act] = np.nan
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


def init_psi(fitter, obs, lo=0.0, hi=120.0, step=2.0, pitch=0.0, flap=3.0):
    """方位角网格搜索(三片叶片外形相同，搜 0~120° 即可)。"""
    best = (1e18, lo)
    for psi in np.arange(lo, hi, step):
        p = np.array([psi] + [pitch] * 3 + [flap] * 3 + [0.0] * 3)
        c = fitter.cost(p, obs)
        if c < best[0]:
            best = (c, psi)
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
    ap.add_argument('--overlay', action='store_true', help='输出模型轮廓叠加视频(质检用)')
    ap.add_argument('--polarity', choices=['dark', 'bright'], default='dark',
                    help='无外部掩码时：叶片比天空暗(dark)还是亮(bright)')
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    global POLARITY
    POLARITY = args.polarity

    meta = load_json(os.path.join(args.data, 'cameras.json'))
    cfg = TurbineConfig.from_dict(meta.get('turbine'))
    fps = float(meta.get('fps', 10.0))
    rotor = Rotor(cfg)
    cams_full = [Camera.from_dict(d) for d in meta['cameras']]
    cams = [c.scaled(args.fit_scale) for c in cams_full] if args.fit_scale != 1.0 else cams_full
    fitter = Fitter(rotor, cams)
    srcs = [FrameSource(args.data, c.name) for c in cams_full]
    writers = None

    frames, x_prev, omega = [], None, None
    phys = PhysPrior()
    last_obs = np.array([np.nan] + [0.0] * 3 + [np.nan] * 3 + [0.0] * 3)
    last_obs[4:7] = 3.0
    k, t0 = 0, time.time()
    while True:
        imgs = [s.read() for s in srcs]
        if any(i is None for i in imgs) or (args.max_frames and k >= args.max_frames):
            break
        obs = []
        for c, cf, img in zip(cams, cams_full, imgs):
            mp = os.path.join(args.masks, c.name, '%06d.png' % k) if args.masks else None
            m = segment(img, mp)
            if args.fit_scale != 1.0:
                m = cv2.resize(m, (c.W, c.H), interpolation=cv2.INTER_NEAREST)
            obs.append(Obs(m))

        dt = 1.0 / fps
        if x_prev is None:                                   # 第 1 帧：网格搜方位角
            psi0 = init_psi(fitter, obs)
            x0 = np.array([psi0] + [0.0] * 3 + [3.0] * 3 + [0.0] * 3)
            x, std, res = fitter.solve(obs, x0, x0, w_prior=0.0, max_nfev=150)
        elif omega is None:                                  # 第 2 帧：在 ±最大转角内搜(转向由数据决定)
            span = args.rpm_max * 6.0 * dt
            psi1 = init_psi(fitter, obs, x_prev[0] - span, x_prev[0] + span, step=max(0.5, span / 40),
                            pitch=float(np.mean(x_prev[1:4])), flap=float(np.mean(x_prev[4:7])))
            x0 = x_prev.copy(); x0[0] = psi1
            x, std, res = fitter.solve(obs, x0, x0, w_prior=0.2, max_nfev=150)
            omega = (x[0] - x_prev[0]) / dt
        else:
            xp = x_prev.copy(); xp[0] += omega * dt          # 转速外推
            phys.apply(fitter, xp[0])
            n_all, n_out = fitter.in_frame(xp)
            active = np.ones(10, bool)
            ready = phys.flap.n_upd > 6
            for b in range(3):
                if n_out[b] < 3:                             # 外侧看不见 → 挥舞/摆振不可观，取推算值
                    active[[4 + b, 7 + b]] = False
                    xp[4 + b] = fitter.mu[4 + b] if ready else last_obs[4 + b]
                    xp[7 + b] = fitter.mu[7 + b] if ready else last_obs[7 + b]
                if n_all[b] < 3:                             # 整片看不见 → 桨距也取推算值
                    active[1 + b] = False
                    xp[1 + b] = fitter.mu[1 + b] if ready else last_obs[1 + b]
            x, std, res = fitter.solve(obs, xp, xp, w_prior=0.5, active=active)
            omega = 0.8 * omega + 0.2 * (x[0] - x_prev[0]) / dt
        seen = fitter.observed_sections(x, obs)
        phys.update(x, seen, rotor.tpl.xi)
        # 记录每片叶片最近一次"真正看清"时的值，先验未就绪时用来填补
        outer = rotor.tpl.xi > 0.4
        for b in range(3):
            if seen[b, outer].mean() >= 0.15:
                last_obs[[4 + b, 7 + b]] = x[[4 + b, 7 + b]]
            if seen[b].mean() >= 0.15:
                last_obs[1 + b] = x[1 + b]
        if not np.isfinite(last_obs[4:7]).all() and np.isfinite(last_obs[4:7]).any():
            for g in (1, 4, 7):                              # 没看清过的叶片：先借其他叶片的值
                blk = last_obs[g:g + 3]
                blk[~np.isfinite(blk)] = np.nanmean(blk)
        st = fitter.full(x)
        frames.append({
            'frame': k, 't': k * dt, 'state': st.tolist(),
            'std': (np.concatenate([std, np.zeros(N_STATE - len(std))])).tolist(),
            'rpm': abs(omega or 0.0) / 6.0, 'cost': float(res.cost), 'nfev': int(res.nfev),
            'observed_sections': seen.astype(int).tolist(),
        })
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

    if writers:
        for w in writers:
            w.release()
    save_json(os.path.join(args.out, 'recon.json'), {
        'turbine': cfg.to_dict(), 'fps': fps, 'state_names': STATE_NAMES,
        'note': 'state 由 model.Rotor.forward 还原网格；observed_sections=1 为图像约束，0 为模型推断',
        'frames': frames})
    print('完成 %d 帧，平均 %.2f s/帧 → %s' % (k, (time.time() - t0) / max(k, 1),
                                         os.path.join(args.out, 'recon.json')))


if __name__ == '__main__':
    main()
