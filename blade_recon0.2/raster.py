"""
raster.py —— 三片叶片网格在某一路相机下的光栅化（像素 → 叶片、三角面、重心坐标、深度）。

合成渲染(synth.py)、纹理反投影(texture.py)、带纹理的预览(preview.py)共用。
做法：背面剔除 + 像素中心处的透视深度 z-test，再对可见像素返回透视校正重心坐标。
面平均深度不能决定相交/重叠三角形的可见性；每个像素单独比较相机系深度。
"""
import cv2
import numpy as np


def vertex_frames(Vb):
    """单片叶片顶点 (S,N,3) → 外法向 n、展向切向 t_s、周向切向 t_c（τ 增大方向），均为单位向量。"""
    t_s = np.gradient(Vb, axis=0)
    t_c = np.roll(Vb, -1, 1) - np.roll(Vb, 1, 1)
    n = np.cross(t_c, t_s)
    out = Vb - Vb.mean(1, keepdims=True)
    sgn = np.sign((n * out).sum(-1, keepdims=True))
    sgn[sgn == 0] = 1.0
    n = n * sgn

    def unit(a):
        return a / (np.linalg.norm(a, axis=-1, keepdims=True) + 1e-12)
    return unit(n), unit(t_s), unit(t_c)


def triangulated_atlas_surface(atlas, Vb, rows=slice(None)):
    """Atlas texel -> the same side triangle used by rasterize/Blender UV.

    The atlas' r/tau are linearly interpolated on each triangle.  A bilinear
    quad surface is generally different, including for the undeformed loft,
    and is not the inverse of Raster.interp(corner_r_tau).  This also works for
    interpolated vertex attributes such as normals; no state-dependent map is
    cached.  The root/tip and closed seam follow Atlas.corner_r_tau exactly.
    """
    i = atlas.i0[rows][:, None]
    j, j1 = atlas.j0[rows], atlas.j1[rows]
    s = atlas.wi[rows][:, None].astype(np.float64)
    # Unwrapped j+1 is intentional: the last side cell ends at tau=1.
    t00 = atlas.tau_v[i, j]
    t01 = atlas.tau_v[i, j + 1]
    t10 = atlas.tau_v[i + 1, j]
    t11 = atlas.tau_v[i + 1, j + 1]
    tau = atlas.tau_cols[None, :]
    diag = (1 - s) * t00 + s * t11
    first = tau >= diag
    b = (tau - diag) / (t01 - t00)
    c = (tau - ((1 - s) * t00 + s * t10)) / (t11 - t10)
    a0, b0 = Vb[i, j], Vb[i, j1]
    c0, d0 = Vb[i + 1, j1], Vb[i + 1, j]
    left = ((1 - s - b)[..., None] * a0 + b[..., None] * b0
            + s[..., None] * c0)
    right = ((1 - s)[..., None] * a0 + c[..., None] * c0
             + (s - c)[..., None] * d0)
    return np.where(first[..., None], left, right)


class Raster:
    """rasterize() 的结果。像素按 (ys, xs) 列出，只含叶片像素。"""

    def __init__(self, H, W, F, fid, ys, xs, bary, depth):
        self.H, self.W, self.F = H, W, F
        self.fid = fid                       # (H,W) int32，全局面编号 = 叶片×F + 面，-1 = 天空
        self.ys, self.xs = ys, xs
        self.blade = fid[ys, xs] // F
        self.face = fid[ys, xs] % F
        self.bary = bary                     # (P,3) 透视校正
        self.depth = depth                   # (P,) 相机系 z

    def interp(self, corner_attr):
        """corner_attr: (3, F, 3, k) 逐叶片逐面角属性 → (P,k)。"""
        a = corner_attr[self.blade, self.face]                       # (P,3,k)
        return np.einsum('pck,pc->pk', a, self.bary)

    def image(self, values, fill=0.0):
        k = values.shape[1] if values.ndim == 2 else 1
        out = np.full((self.H, self.W, k), fill, np.float32)
        out[self.ys, self.xs] = values.reshape(len(self.ys), k)
        return out if k > 1 else out[..., 0]

    def depth_map(self):
        z = np.full((self.H, self.W), np.inf, np.float32)
        z[self.ys, self.xs] = self.depth
        return z


def rasterize(cam, V, faces, normals=None):
    """V: (3,S,N,3) 三片叶片顶点；faces: 单片叶片三角面 (F,3)；normals: 可选 (3,S,N,3) 外法向。"""
    H, W = cam.H, cam.W
    F = len(faces)
    C = cam.center
    fid = np.full((H, W), -1, np.int32)
    UV = np.zeros((3, F, 3, 2), np.float64)
    Z = np.zeros((3, F, 3), np.float64)
    zbuf = np.full((H, W), np.inf, np.float64)
    for b in range(3):
        v = V[b].reshape(-1, 3)
        uv, z = cam.project(v)
        UV[b], Z[b] = uv[faces], z[faces]
        nv = (normals[b] if normals is not None else vertex_frames(V[b])[0]).reshape(-1, 3)
        nf = nv[faces].mean(1)
        cen = v[faces].mean(1)
        front = ((C - cen) * nf).sum(1) > 0
        uvt = UV[b]
        ok = front & np.isfinite(uvt).all((1, 2)) & (Z[b] > 0.1).all(1)
        ok &= ~((uvt[..., 0] < 0).all(1) | (uvt[..., 0] >= W).all(1)
                | (uvt[..., 1] < 0).all(1) | (uvt[..., 1] >= H).all(1))
        for f in np.nonzero(ok)[0]:
            t = uvt[f]
            v0, v1 = t[1] - t[0], t[2] - t[0]
            den = v0[0] * v1[1] - v1[0] * v0[1]
            if abs(den) < 1e-9:
                continue
            x0, x1 = max(0, int(np.ceil(t[:, 0].min()))), min(W - 1, int(np.floor(t[:, 0].max())))
            y0, y1 = max(0, int(np.ceil(t[:, 1].min()))), min(H - 1, int(np.floor(t[:, 1].max())))
            if x1 < x0 or y1 < y0:
                continue
            yy, xx = np.mgrid[y0:y1 + 1, x0:x1 + 1]
            dx, dy = xx - t[0, 0], yy - t[0, 1]
            l1 = (dx * v1[1] - v1[0] * dy) / den
            l2 = (v0[0] * dy - dx * v0[1]) / den
            l0 = 1.0 - l1 - l2
            inside = (l0 >= -1e-9) & (l1 >= -1e-9) & (l2 >= -1e-9)
            inv_z = l0 / Z[b, f, 0] + l1 / Z[b, f, 1] + l2 / Z[b, f, 2]
            depth = np.divide(1.0, inv_z, out=np.full_like(inv_z, np.inf), where=inv_z > 0)
            old = zbuf[y0:y1 + 1, x0:x1 + 1]
            nearer = inside & (depth < old)
            old[nearer] = depth[nearer]
            fid[y0:y1 + 1, x0:x1 + 1][nearer] = b * F + f

    ys, xs = np.nonzero(fid >= 0)
    g = fid[ys, xs]
    b, f = g // F, g % F
    t = UV[b, f]                                                  # (P,3,2)
    p = np.stack([xs, ys], 1).astype(np.float64)
    v0, v1 = t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]
    v2 = p - t[:, 0]
    den = v0[:, 0] * v1[:, 1] - v1[:, 0] * v0[:, 1]
    den = np.where(np.abs(den) < 1e-9, 1e-9, den)
    l1 = (v2[:, 0] * v1[:, 1] - v1[:, 0] * v2[:, 1]) / den
    l2 = (v0[:, 0] * v2[:, 1] - v2[:, 0] * v0[:, 1]) / den
    lam = np.clip(np.stack([1 - l1 - l2, l1, l2], 1), 0, 1)
    lam /= lam.sum(1, keepdims=True) + 1e-12
    w = lam / Z[b, f]                                             # 透视校正
    inv_z = w.sum(1)
    bary = (w / inv_z[:, None]).astype(np.float32)
    return Raster(H, W, F, fid, ys, xs, bary, (1.0 / inv_z).astype(np.float32))


def face_screen_jacobian(cam, V, faces, corner_vals):
    """每个面：屏幕坐标 → 面角属性(k 维)的仿射雅可比 (3,F,k,2)。用于算像素在纹理上的覆盖范围。"""
    out = []
    for b in range(3):
        uv, _ = cam.project(V[b].reshape(-1, 3))
        t = uv[faces]                                             # (F,3,2)
        A = np.stack([t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]], 2)   # (F,2,2) 列 = 边
        D = np.stack([corner_vals[b][:, 1] - corner_vals[b][:, 0],
                      corner_vals[b][:, 2] - corner_vals[b][:, 0]], 2)   # (F,k,2)
        det = A[:, 0, 0] * A[:, 1, 1] - A[:, 0, 1] * A[:, 1, 0]
        det = np.where(np.abs(det) < 1e-9, np.nan, det)
        inv = np.stack([np.stack([A[:, 1, 1], -A[:, 0, 1]], 1),
                        np.stack([-A[:, 1, 0], A[:, 0, 0]], 1)], 1) / det[:, None, None]
        out.append(np.einsum('fkc,fcd->fkd', D, inv))
    return np.nan_to_num(np.stack(out), nan=1e3)


# ───────────────────────── 纹理采样与带纹理渲染 ─────────────────────────
def sample(img, rows, cols, wrap_cols=True, interp=cv2.INTER_LINEAR):
    """在 img 上按浮点 (rows, cols) 取值。展向钳制，仅周向列可周期环绕。"""
    shp = np.shape(rows)
    r = np.asarray(rows, np.float32).reshape(-1)
    c = np.asarray(cols, np.float32).reshape(-1)
    n = len(r)
    if n == 0:
        return np.zeros(shp, np.float32)
    w = 4096                                           # remap 要求每维 < 32767，折成二维
    h = (n + w - 1) // w
    pad = h * w - n
    mr = np.concatenate([r, np.zeros(pad, np.float32)]).reshape(h, w)
    mc = np.concatenate([c, np.zeros(pad, np.float32)]).reshape(h, w)
    mr = np.clip(mr, 0, img.shape[0] - 1)
    source = img.astype(np.float32, copy=False)
    if wrap_cols:
        # 给周向接缝补两列；BORDER_WRAP 会同时环绕展向，污染叶根/叶尖。
        mc = np.mod(mc, img.shape[1]) + 1
        source = np.concatenate([source[:, -1:], source, source[:, :1]], axis=1)
    out = cv2.remap(source, mc, mr, interp, borderMode=cv2.BORDER_REPLICATE)
    return out.reshape(-1)[:n].reshape(shp)


def texture_lookup(cam, V, faces, atlas, R, maps, aa=4):
    """对光栅化结果 R 的每个叶片像素，在图集 maps[b](H,W) 上做面积积分取值(像素脚印内 aa×aa 采样)。
    像素脚印用面上的屏幕→图集雅可比算，所以展向被压缩(一个像素覆盖十几厘米)时会正确地平均掉细节，
    不会出现点采样的锯齿。返回 (P,)。"""
    crt = atlas.corner_r_tau(faces)                                     # (F,3,2) r, τ
    rc = np.stack([atlas.row_of_r(crt[..., 0]), crt[..., 1] * atlas.W - 0.5], -1)   # 行、列
    rc3 = np.repeat(rc[None], 3, 0)
    J = face_screen_jacobian(cam, V, faces, rc3)                        # (3,F,2,2)
    c0 = R.interp(rc3)                                                  # (P,2)
    Jp = J[R.blade, R.face]                                             # (P,2,2)
    off = (np.arange(aa) + 0.5) / aa - 0.5
    acc = np.zeros(len(R.ys), np.float32)
    for b in range(3):
        sel = R.blade == b
        if not sel.any():
            continue
        cb, Jb = c0[sel], Jp[sel]
        s = np.zeros(sel.sum(), np.float32)
        for dx in off:
            for dy in off:
                p = cb + Jb[:, :, 0] * dx + Jb[:, :, 1] * dy
                s += sample(maps[b], p[:, 0], p[:, 1])
        acc[sel] = s / (aa * aa)
    return acc, c0


def render_textured(cam, rotor, state, faces, atlas, albedo, height=None, light=None,
                    sky=(205, 175), base=40.0, gain=110.0, aa=4):
    """带反照率纹理(和可选高度图)的近红外灰度渲染。返回 (img float32, mask uint8, R)。
    亮度 = 反照率 × (base + gain × 朗伯)，朗伯用高度图扰动后的法向(起伏只体现在明暗上)。"""
    H, W = cam.H, cam.W
    img = np.linspace(sky[0], sky[1], H, dtype=np.float32)[:, None].repeat(W, 1)
    V, _ = rotor.forward(state)
    frames = [vertex_frames(V[b]) for b in range(3)]
    R = rasterize(cam, V, faces, normals=np.stack([f[0] for f in frames]))
    if len(R.ys) == 0:
        return img, np.zeros((H, W), np.uint8), R
    A, rc = texture_lookup(cam, V, faces, atlas, R, albedo, aa)
    F = len(faces)
    corner = np.zeros((3, F, 3, 9))
    for b in range(3):
        n, ts, tc = frames[b]
        corner[b] = np.concatenate([n, ts, tc], -1).reshape(-1, 9)[faces]
    nts = R.interp(corner)
    n, ts, tc = nts[:, 0:3], nts[:, 3:6], nts[:, 6:9]
    if height is not None:
        # 高度图梯度(米/米)：行方向 = 展向，列方向 = 周向
        gr = np.zeros(len(R.ys)); gc = np.zeros(len(R.ys))
        for b in range(3):
            sel = R.blade == b
            if height[b] is None or not sel.any():
                continue
            hr, hc = np.gradient(height[b])
            per = np.interp(atlas.r0 + (rc[sel, 0] + 0.5) * atlas.dr, atlas.tpl.r, atlas.perim_v)
            gr[sel] = sample(hr, rc[sel, 0], rc[sel, 1]) / atlas.dr
            gc[sel] = sample(hc, rc[sel, 0], rc[sel, 1]) / (per / atlas.W)
        n = n - gr[:, None] * ts - gc[:, None] * tc
        n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-12
    if light is None:
        light = np.array([0.3, -0.5, 0.8]) / np.linalg.norm([0.3, -0.5, 0.8])
    lam = 0.35 + 0.65 * np.abs(n @ light)
    img[R.ys, R.xs] = A * (base + gain * lam)
    mask = (R.fid >= 0).astype(np.uint8) * 255
    return img, mask, R
