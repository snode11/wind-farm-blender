"""
多模态信号合成（FLORIS 后端）：把每台机组的振动/载荷代理量 + 局部 wake 图像
合成出来，供多模态 MAPPO 观测和后续 VLM 特征提取使用。

- 振动：steady-state FLORIS 没有时间轴，用物理量合成"载荷/振动签名"特征向量：
    TI（湍流强度，疲劳主因）、Ct≈4a(1-a)（推力载荷）、|yaw| 失准（1P 非对称载荷）、
    归一化功率，以及由这些量导出的 1P/3P 谐波幅值代理。
- 视觉：每步算一张低分辨率全场水平切面，再在每台机组周围切一块局部风速图像
    （"这台机组正对着的来流/尾流长什么样"），可直接喂 CNN / 视觉编码器。

对外主接口：
    vibration_features(fi)            -> (n_turb, VIB_DIM)  float32
    wake_images(fi, hub_h)            -> (n_turb, 1, H, W)  float32  (0..1)
    vibration_waveform(fi, i, n=128)  -> (n,)  合成时序，仅用于可视化/VLM demo
"""
import numpy as np

VIB_DIM = 6                 # 振动特征向量维度
IMG_HW = (48, 48)           # 每台机组 wake 图像分辨率
PATCH_M = 500.0             # 局部图像覆盖的物理半宽（米），中心为机组位置
PLANE_RES = (128, 72)       # 全场平面 (x_res, y_res)，一步算一次再切
P_RATED = 5.0e6            # 额定功率（W），用于功率归一化
YAW_MAX = 40.0             # yaw 归一化基准（度）


# ---------------------------------------------------------------------------
# 振动 / 载荷代理量
# ---------------------------------------------------------------------------
def _turbine_scalars(fi):
    """取每台机组的 (TI, a, power, yaw)；均为 (n_turb,) 一维数组。"""
    ti = np.ravel(fi.get_turbine_TIs()).astype(np.float64)
    a = np.ravel(fi.get_turbine_ais()).astype(np.float64)
    power = np.ravel(fi.get_turbine_powers()).astype(np.float64)
    yaw = np.ravel(fi.floris.farm.yaw_angles).astype(np.float64)
    return ti, a, power, yaw


def vibration_features(fi):
    """返回 (n_turb, VIB_DIM) 的振动/载荷签名，各分量已粗归一化到 ~[0,1]。

    列含义：[TI, Ct, |yaw|/YAW_MAX, P/P_RATED, load_1P, load_3P]
      Ct      = 4a(1-a)                 推力系数（轴向载荷）
      load_1P ∝ Ct·(TI + |sin yaw|)     1P 非对称载荷（yaw 失准 + 湍流）
      load_3P ∝ Ct·TI                   3P/叶片通过载荷（湍流驱动）
    """
    ti, a, power, yaw = _turbine_scalars(fi)
    ct = 4.0 * a * (1.0 - a)
    yaw_abs = np.abs(yaw) / YAW_MAX
    p_norm = np.clip(power / P_RATED, 0.0, 1.5)
    load_1p = ct * (ti + np.abs(np.sin(np.deg2rad(yaw))))
    load_3p = ct * ti
    feats = np.stack([ti, ct, yaw_abs, p_norm, load_1p, load_3p], axis=1)
    return feats.astype(np.float32)


def vibration_waveform(fi, i, n=128, fs=50.0, rotor_hz=0.2):
    """为第 i 台机组合成一段"振动传感器"时序（仅可视化/VLM demo 用，不进 RL 观测）。

    1P = 转子频率，3P = 叶片通过频率；幅值由该机组的载荷签名调制。
    """
    feats = vibration_features(fi)[i]
    ti, ct, yaw_abs, p_norm, load_1p, load_3p = feats
    t = np.arange(n) / fs
    sig = (load_1p * np.sin(2 * np.pi * rotor_hz * t)
           + load_3p * np.sin(2 * np.pi * 3 * rotor_hz * t + 0.3)
           + 0.15 * ti * _det_noise(n, i))
    return sig.astype(np.float32)


def _det_noise(n, seed_idx):
    """确定性伪噪声（不依赖全局 RNG，避免破坏可复现性）。"""
    k = np.arange(n)
    return np.sin(k * (7.1 + seed_idx) + 1.3 * seed_idx) * np.cos(k * 0.37 + seed_idx)


# ---------------------------------------------------------------------------
# 视觉：局部 wake 图像
# ---------------------------------------------------------------------------
def farm_wake_plane(fi, hub_h, res=PLANE_RES, margin=PATCH_M):
    """算一张覆盖全场（+margin）的低分辨率水平风速切面。

    风向非正北时 FLORIS 返回的 x1/x2 是旋转网格、每点坐标不同，不能用 pivot；
    按已知分辨率 reshape 即可（df 为 C-order，x1 变化最快 → [y, x]）。
    返回 (U, x0, x1, y0, y1)，U 形状 (y_res, x_res)，U[iy, ix]。
    """
    lx, ly = np.asarray(fi.layout_x), np.asarray(fi.layout_y)
    x0, x1 = lx.min() - margin, lx.max() + margin
    y0, y1 = ly.min() - margin, ly.max() + margin
    xr, yr = res
    plane = fi.calculate_horizontal_plane(
        height=hub_h, x_resolution=xr, y_resolution=yr,
        x_bounds=(x0, x1), y_bounds=(y0, y1),
    )
    U = plane.df["u"].values.reshape(yr, xr)   # [x2(y) 慢, x1(x) 快]
    return U.astype(np.float32), float(x0), float(x1), float(y0), float(y1)


def _resize_nn(arr, out_hw):
    """最近邻缩放到 out_hw=(H, W)。"""
    H, W = out_hw
    iy = np.linspace(0, arr.shape[0] - 1, H).round().astype(int)
    ix = np.linspace(0, arr.shape[1] - 1, W).round().astype(int)
    return arr[np.ix_(iy, ix)]


def wake_images(fi, hub_h, res=PLANE_RES, patch_m=PATCH_M, out_hw=IMG_HW):
    """返回 (n_turb, 1, H, W)：每台机组 ±patch_m 的局部风速图像，按自由来流归一化 ~[0,1]。

    小风偏（网格轻微剪切）忽略，用线性索引把机组位置映射到网格再切固定窗口。
    """
    U, x0, x1, y0, y1 = farm_wake_plane(fi, hub_h, res=res)
    ny, nx = U.shape
    lx, ly = np.asarray(fi.layout_x), np.asarray(fi.layout_y)
    u_free = float(np.ravel(fi.floris.flow_field.wind_speeds)[0])
    dx, dy = (x1 - x0) / (nx - 1), (y1 - y0) / (ny - 1)
    half_x, half_y = int(round(patch_m / dx)), int(round(patch_m / dy))

    imgs = np.zeros((len(lx), 1, out_hw[0], out_hw[1]), dtype=np.float32)
    for k, (xc, yc) in enumerate(zip(lx, ly)):
        cx = int(round((xc - x0) / dx))
        cy = int(round((yc - y0) / dy))
        ix_lo, ix_hi = max(cx - half_x, 0), min(cx + half_x + 1, nx)
        iy_lo, iy_hi = max(cy - half_y, 0), min(cy + half_y + 1, ny)
        patch = U[iy_lo:iy_hi, ix_lo:ix_hi]
        patch = _resize_nn(patch, out_hw) / max(u_free, 1e-6)
        imgs[k, 0] = np.clip(patch, 0.0, 1.5)
    return imgs
