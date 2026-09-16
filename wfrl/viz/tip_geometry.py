# -*- coding: utf-8 -*-
"""
叶尖三维空间坐标与轨迹估计 —— 两方案并行实现(同步验证用)。

世界坐标系(全模块统一)：
    原点 O_w = 塔筒底面圆心(地面)
    Z 轴     = 竖直向上
    默认 X 轴指向上风；可通过 origin_w/yaw_deg 映射到固定世界系。
    Y 轴     = Z × X，水平横向

两个方案的共同地基：
    · 相机 FOV + 叶尖真实距离 → 像素与米的换算；
    · 叶尖做半径 R 确定的圆周运动(R = 叶片长 + 轮毂半径)。

方案 A —— 转速积分(RpmAzimuthIntegrator)
    先算出叶尖圆周落在视野内的弧段(visible_arc)，再由转速推算角速度、按时间积分
    得实时方位角，映射为圆周上的空间点位。
    · 优点：实现简单，丢检也能出点；
    · 局限：需绝对相位初值；相位误差随时间累积；叶尖被强制落在标称旋转面内，
            面外挠度按定义恒为 0。

方案 B —— 逐帧几何反解(tip_3d_from_pixel)
    每帧把叶尖像素反投影成射线，与【以轮毂为心、R 为半径的球面】求交，直接得三维坐标。
    · 逐帧独立的模型约束估计，无积分漂移，但存在标定与形状假设偏差；
    · 用【球面】而非【圆】约束：叶片弯曲时叶尖仍大致保持到轮毂的距离 R，只是移出
      旋转面。输出是面外偏移，包含静态预弯/预锥，不能直接称为弹性位移。

FAST.Farm 三维截面直接交给 TipTracker.update_world，不经过球面约束。
pixel 反解只适合相机观测实验；球外两解通过 ambiguous 标记，不代表唯一深度。

单位：米、度、秒。
"""
import math
import numpy as np


def finite_scalar(value, name='value'):
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(name + ' must be a finite number')
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(name + ' must be finite')
    return value


def finite_vector(value, size=3):
    value = np.asarray(value, dtype=float)
    if value.shape != (size,) or not np.isfinite(value).all():
        raise ValueError(f'Expected a finite ({size},) vector')
    return value.copy()


def rotation_matrix(value):
    value = np.asarray(value, dtype=float)
    if (value.shape != (3, 3) or not np.isfinite(value).all()
            or not np.allclose(value.T @ value, np.eye(3), atol=1e-7)
            or not math.isclose(np.linalg.det(value), 1, abs_tol=1e-7)):
        raise ValueError('Expected a right-handed orthonormal rotation matrix')
    return value.copy()


# ────────────────────────────────────────────────────────────────────────
#  1) 机组几何：世界系下的轮毂位置、旋转面基向量、叶尖正解与反解
# ────────────────────────────────────────────────────────────────────────
class TurbineGeometry:
    """
    机组几何常量 + 旋转面定义。

    方位角约定：φ=0° 在最高点(12 点钟)，绕 +ê₁ 方向增大，φ=180° 为最低点(过塔筒)。
    面外角约定：ψ>0 表示叶尖朝【塔筒侧(下风)】偏出旋转面，即挠度为正。
    """

    def __init__(self, hub_height_m, overhang_m, tilt_deg,
                 tip_radius_m, tower_radius_m=0.0, *, tower_top_radius_m=None,
                 tower_height_m=None, origin_w=(0, 0, 0), yaw_deg=0):
        self.H = finite_scalar(hub_height_m)
        self.d = finite_scalar(overhang_m)
        self.tau = math.radians(finite_scalar(tilt_deg))
        self.R = finite_scalar(tip_radius_m)
        self.r_tower = finite_scalar(tower_radius_m)
        self.r_top = self.r_tower if tower_top_radius_m is None else finite_scalar(tower_top_radius_m)
        self.tower_height = self.H if tower_height_m is None else finite_scalar(tower_height_m)
        if min(self.H, self.R, self.tower_height) <= 0 or min(self.r_tower, self.r_top) < 0:
            raise ValueError('Heights/radius must be positive; tower radii nonnegative')
        self.origin = finite_vector(origin_w)
        self.set_yaw(yaw_deg)

    def set_yaw(self, yaw_deg):
        """World rotation of the original upstream-X frame; update per backend frame."""
        a = math.radians(finite_scalar(yaw_deg))
        self.world_rotation = np.array([[math.cos(a), -math.sin(a), 0],
                                        [math.sin(a), math.cos(a), 0], [0, 0, 1.]])

    def tower_radius(self, world_z):
        z = finite_scalar(world_z) - self.origin[2]
        if z < 0 or z > self.tower_height:
            return None
        return self.r_tower + (self.r_top-self.r_tower) * z/self.tower_height

    # 轮毂中心(世界系)
    @property
    def hub(self):
        return self.origin + self.world_rotation @ np.array([self.d, 0.0, self.H])

    # 旋转轴法向，指向上风侧；倾角使其上翘 τ
    @property
    def n_axis(self):
        return self.world_rotation @ np.array([math.cos(self.tau), 0.0, math.sin(self.tau)])

    # 面内"向上"基向量 ê₂（顶部朝塔筒侧倾，正是倾角增大底部净空的原因）
    @property
    def e_up(self):
        return self.world_rotation @ np.array([-math.sin(self.tau), 0.0, math.cos(self.tau)])

    # 面内"横向"基向量 ê₁
    @property
    def e_lat(self):
        return self.world_rotation @ np.array([0.0, 1.0, 0.0])

    def tip_position(self, azimuth_deg, out_of_plane_deg=0.0):
        """
        正解：方位角(+可选面外角) → 叶尖世界坐标。

        叶尖始终在以轮毂为心、R 为半径的【球面】上：面外偏转沿球面滑动，
        而不是把点推离球面。|P − hub| ≡ R。
        """
        phi = math.radians(finite_scalar(azimuth_deg))
        psi = math.radians(finite_scalar(out_of_plane_deg))
        in_plane = math.cos(phi) * self.e_up + math.sin(phi) * self.e_lat
        # 面外朝下风(塔筒侧) = −n̂
        dir_ = math.cos(psi) * in_plane + math.sin(psi) * (-self.n_axis)
        return self.hub + self.R * dir_

    def decompose(self, P, reference=None):
        """
        反解：叶尖世界坐标 → 方位角 / 面外挠度 / 净空 等派生量。
        """
        P = finite_vector(P)
        rel = P - self.hub
        a = float(np.dot(rel, self.e_lat))     # 面内横向分量
        b = float(np.dot(rel, self.e_up))      # 面内竖直分量
        s = float(-np.dot(rel, self.n_axis))   # 面外分量，正=朝塔筒(下风)
        azim = math.degrees(math.atan2(a, b)) % 360.0
        r_ip = math.hypot(a, b)                # 面内半径
        radius = self.tower_radius(P[2])
        return {
            'azimuth_deg':     azim,
            'out_of_plane_m':  s,                                  # 面外位移(挠度)
            'out_of_plane_deg': math.degrees(math.atan2(s, r_ip)),
            'in_plane_radius_m': r_ip,
            'radius_m':        float(np.linalg.norm(rel)),          # 应≈R
            # 净空：叶尖到塔筒轴的水平距离，再减塔筒半径
            'clearance_m': (None if radius is None else
                            float(np.linalg.norm((P-self.origin)[:2]) - radius)),
            'clearance_definition': 'tip reference to tower section at same height',
            # Plane offset includes static precone/prebend. Elastic displacement
            # needs an independently supplied rigid reference at the same pose.
            'elastic_out_of_plane_m': (None if reference is None else
                                       float(-np.dot(P-finite_vector(reference), self.n_axis))),
            'height_m':        float(P[2]),
        }


# ────────────────────────────────────────────────────────────────────────
#  2) 相机模型：FOV → 焦距(像素)，像素 ↔ 世界射线
# ────────────────────────────────────────────────────────────────────────
class CameraModel:
    """
    针孔相机。相机系约定：+X 右、+Y 下、+Z 光轴向前(与图像坐标一致)。
    R_cw 为 相机系→世界系 的旋转矩阵(列 = 相机三轴在世界系下的方向)。
    """

    def __init__(self, img_w, img_h, fov_deg, vfov_deg, pos_w, R_cw, *,
                 intrinsics=None, distortion=None):
        if (finite_scalar(img_w) != int(img_w) or finite_scalar(img_h) != int(img_h)
                or min(img_w, img_h) <= 0):
            raise ValueError('Image dimensions must be positive integers')
        if not all(0 < finite_scalar(f) < 180 for f in (fov_deg, vfov_deg)):
            raise ValueError('FOV must lie strictly between 0 and 180 degrees')
        self.W = int(img_w)
        self.H = int(img_h)
        self.fx = self.W / (2.0 * math.tan(math.radians(fov_deg) / 2.0))
        self.fy = self.H / (2.0 * math.tan(math.radians(vfov_deg) / 2.0))
        self.cx = self.W / 2.0
        self.cy = self.H / 2.0
        if intrinsics is not None:
            self.fx, self.fy, self.cx, self.cy = finite_vector(intrinsics, 4)
            if min(self.fx, self.fy) <= 0:
                raise ValueError('Focal lengths must be positive')
        self.distortion = np.zeros(5) if distortion is None else np.asarray(distortion, dtype=float).reshape(-1)
        if len(self.distortion) not in (4, 5, 8, 12, 14) or not np.isfinite(self.distortion).all():
            raise ValueError('Invalid OpenCV distortion coefficients')
        self.set_pose(pos_w, R_cw)

    def set_pose(self, pos_w, R_cw):
        pos, rotation = finite_vector(pos_w), rotation_matrix(R_cw)
        self.pos, self.R_cw = pos, rotation

    @property
    def K(self):
        return np.array([[self.fx, 0, self.cx], [0, self.fy, self.cy], [0, 0, 1.]])

    @classmethod
    def from_calibration(cls, width, height, K, distortion, pos_w, R_cw):
        K = np.asarray(K, dtype=float)
        if (K.shape != (3, 3) or not np.isfinite(K).all()
                or not np.allclose(K[2], [0, 0, 1]) or K[0, 1] != 0 or K[1, 0] != 0):
            raise ValueError('Invalid camera intrinsic matrix')
        return cls(width, height, 90, 90, pos_w, R_cw,
                   intrinsics=(K[0, 0], K[1, 1], K[0, 2], K[1, 2]), distortion=distortion)

    @classmethod
    def look_at(cls, img_w, img_h, fov_deg, vfov_deg, pos_w, target_w, right_hint,
                upright=False):
        """
        由"相机位置 + 瞄准点 + 期望的图像右方向"构造位姿。

        right_hint 一般取旋转面内横向 ê₁ —— 这样【图像列 ↔ 方位角】、
        【图像行 ↔ 面外(净空/挠度)】，两个方向近似解耦，正是净空相机的实际装法。

        upright=True：若该 right_hint 定出的图像是上下颠倒的(down 的世界 Z 分量为正)，
        自动翻到 −right_hint。数学上两者都自洽(反投影结果一致)，只影响画面观感，
        所以默认关闭；渲染/看图时打开。
        """
        pos = finite_vector(pos_w)
        fwd = finite_vector(target_w) - pos
        if np.linalg.norm(fwd) < 1e-9:
            raise ValueError('Camera position and target must differ')
        fwd = fwd / np.linalg.norm(fwd)
        rh = finite_vector(right_hint)
        right = rh - np.dot(rh, fwd) * fwd          # 对 fwd 做 Gram-Schmidt
        n = np.linalg.norm(right)
        if n < 1e-9:
            raise ValueError('right_hint 与光轴平行，无法定姿')
        right /= n
        down = np.cross(fwd, right)                 # 图像 +Y 向下（右手系 right×down=fwd）
        if upright and down[2] > 0.0:               # 图像下 = 世界上 → 翻转
            right = -right
            down = np.cross(fwd, right)
        R_cw = np.column_stack([right, down, fwd])
        return cls(img_w, img_h, fov_deg, vfov_deg, pos, R_cw)

    def pixel_to_ray(self, u, v):
        """像素 → 世界系单位射线方向。"""
        u, v = finite_vector([u, v], 2)
        if np.any(self.distortion):
            from .tip_dependencies import opencv
            cv2 = opencv()
            xy = cv2.undistortPoints(np.array([[[u, v]]]), self.K, self.distortion)[0, 0]
            d_w = self.R_cw @ np.array([*xy, 1.])
            return d_w / np.linalg.norm(d_w)
        d_cam = np.array([(u - self.cx) / self.fx,
                          (v - self.cy) / self.fy,
                          1.0], float)
        d_w = self.R_cw @ d_cam
        return d_w / np.linalg.norm(d_w)

    def project(self, P_w):
        """世界坐标 → (u, v, 是否落在画面内)。光轴背后返回 visible=False。"""
        p_cam = self.R_cw.T @ (finite_vector(P_w) - self.pos)
        if p_cam[2] <= 1e-9:
            return None, None, False
        u = self.cx + self.fx * p_cam[0] / p_cam[2]
        v = self.cy + self.fy * p_cam[1] / p_cam[2]
        if np.any(self.distortion):
            from .tip_dependencies import opencv
            xy, _ = opencv().projectPoints(p_cam.reshape(1, 3), np.zeros(3), np.zeros(3),
                                           self.K, self.distortion)
            u, v = xy[0, 0]
        vis = (0.0 <= u < self.W) and (0.0 <= v < self.H)
        return float(u), float(v), bool(vis)


# ────────────────────────────────────────────────────────────────────────
#  3) 方案 B：逐帧几何反解(射线 ∩ 球面)
# ────────────────────────────────────────────────────────────────────────
def tip_3d_from_pixel(cam, geom, u, v, *, intersection='near'):
    """
    ★方案 B★ 一帧叶尖像素 → 叶尖三维世界坐标。

    解 |(C − hub) + t·d|² = R²：
        t² + 2t(o·d) + (|o|² − R²) = 0,  o = C − hub
    相机在球内(机舱装法，|o| < R)时只有一个正根，取远交点；
    相机在球外时取近交点(先撞到的那个面)。

    返回 dict：{'P': (3,) 世界坐标, 't': 射线长度(=相机到叶尖距离), 及 decompose 的派生量}
    无实交点(标定错误或误检)→ None。
    """
    if intersection not in ('near', 'far'):
        raise ValueError('intersection must be near or far')
    try:
        u, v = finite_vector([u, v], 2)
    except (ValueError, TypeError):
        return None
    if not (0 <= u < cam.W and 0 <= v < cam.H):
        return None
    d = cam.pixel_to_ray(u, v)
    o = cam.pos - geom.hub
    b = float(np.dot(o, d))
    c = float(np.dot(o, o)) - geom.R ** 2
    disc = b * b - c
    if disc < 0.0:
        return None                      # 射线与球面不相交
    sq = math.sqrt(disc)
    roots = sorted(set(x for x in (-b-sq, -b+sq) if x > 0))
    if not roots:
        return None
    t = roots[0] if intersection == 'near' else roots[-1]
    P = cam.pos + t * d
    out = {'P': P, 't': float(t), 'source': 'pixel_sphere_estimate',
           'constraint': 'fixed hub-tip radius', 'ambiguous': len(roots) > 1,
           'validity': 'estimated'}
    out.update(geom.decompose(P))
    return out


# ────────────────────────────────────────────────────────────────────────
#  4) 方案 A：可见弧段 + 转速积分
# ────────────────────────────────────────────────────────────────────────
def visible_arc(cam, geom, step_deg=0.25):
    """
    ★方案 A 第一步★ 算出叶尖圆周的哪一段落在相机视野里。

    做法：沿标称圆(挠度=0)按 step_deg 采样方位角，逐点投影，记录落在画面内的区间。
    返回 {'segments': [(lo,hi), ...] 度, 'total_deg': 总弧长, 'coverage_pct': 占整圈比例}
    """
    if not 0 < finite_scalar(step_deg) <= 180:
        raise ValueError('step_deg must be in (0, 180]')
    vis = []
    n = int(math.ceil(360.0 / step_deg))
    step_deg = 360 / n
    for i in range(n):
        phi = i * step_deg
        _, _, ok = cam.project(geom.tip_position(phi))
        vis.append(ok)

    # 合并连续可见段（环形）
    segs = []
    if all(vis):
        segs = [(0.0, 360.0)]
    elif any(vis):
        start = None
        # 从一个不可见处开始扫，避免跨 0 断裂
        off = vis.index(False)
        for k in range(n):
            i = (off + k) % n
            if vis[i] and start is None:
                start = i * step_deg
            elif not vis[i] and start is not None:
                end = ((i - 1) % n) * step_deg
                segs.append((start, end if end >= start else end + 360.0))
                start = None
        if start is not None:
            end = ((off - 1) % n) * step_deg
            segs.append((start, end if end >= start else end + 360.0))

    total = sum(hi - lo for lo, hi in segs)
    return {'segments': [(round(a, 2), round(b, 2)) for a, b in segs],
            'total_deg': round(total, 2),
            'coverage_pct': round(total / 360.0 * 100.0, 2)}


class RpmAzimuthIntegrator:
    """
    ★方案 A★ 由转速积分推算方位角 → 圆周上的三维点位。

    φ(t) = φ₀ + ∫ ω dt,  ω = 2π·rpm/60  (rad/s)

    必须先 seed() 给一个绝对相位初值(方案 A 的固有前提)，可以来自：
      · 叶尖过某个已知方位角(如最低点)的那一帧；
      · 或用方案 B 解一帧当种子(推荐做同步验证时用)。

    注意：本方案把叶尖强制放在标称旋转面内，面外挠度恒为 0。
    """

    def __init__(self, geom, direction=+1):
        self.geom = geom
        self.dir = 1 if direction >= 0 else -1   # 旋转方向：+1 = 方位角递增
        self.phi = None       # 当前方位角(度)
        self.t = None         # 当前时刻(秒)

    def seed(self, azimuth_deg, t):
        phi, timestamp = finite_scalar(azimuth_deg), finite_scalar(t)
        self.phi, self.t = phi % 360.0, timestamp

    def step(self, t, rpm):
        """
        推进到时刻 t(秒)，rpm 为该段的转速。返回三维坐标与派生量。
        未 seed 过 → 返回 None。
        """
        if self.phi is None:
            return None
        t, rpm = finite_scalar(t), finite_scalar(rpm)
        dt = t - self.t
        if dt < 0:
            raise ValueError('Rewind requires reseeding the integrator')
        omega_deg = 360.0 * rpm / 60.0        # 度/秒
        self.phi = (self.phi + self.dir * omega_deg * dt) % 360.0
        self.t = float(t)
        P = self.geom.tip_position(self.phi)         # 面外角恒为 0
        out = {'P': P, 'azimuth_deg_integrated': self.phi}
        out.update(self.geom.decompose(P))
        return out


# ────────────────────────────────────────────────────────────────────────
#  5) 自测
# ────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import sys, io
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    except Exception:
        pass

    # ── 机组与相机 ──
    geom = TurbineGeometry(hub_height_m=110.0, overhang_m=5.0, tilt_deg=5.0,
                           tip_radius_m=68.0, tower_radius_m=2.0)
    bottom = geom.tip_position(180.0)                     # 最低点(过塔筒)
    cam = CameraModel.look_at(img_w=640, img_h=392,
                              fov_deg=49.20, vfov_deg=31.36,
                              pos_w=geom.hub + np.array([-2.0, 0.0, -1.5]),
                              target_w=bottom,
                              right_hint=geom.e_lat)
    print('轮毂中心   =', np.round(geom.hub, 3))
    print('最低点叶尖 =', np.round(bottom, 3),
          ' → 净空 %.3f m' % geom.decompose(bottom)['clearance_m'])
    print('相机位置   =', np.round(cam.pos, 3),
          ' fx=%.1f fy=%.1f px' % (cam.fx, cam.fy))

    # ── 方案 A 第一步：可见弧段 ──
    va = visible_arc(cam, geom)
    print('\n[方案A-1] 可见弧段 %s，共 %.2f°，占整圈 %.2f%%'
          % (va['segments'], va['total_deg'], va['coverage_pct']))

    # ── 方案 B 往返校验：真值 → 投影 → 反解，应精确还原 ──
    print('\n[方案B] 往返校验(含面外挠度)')
    print(' 真方位角  真面外角 |  投影像素      | 反解方位角 反解面外  位置误差(m)')
    for phi_t, psi_t in [(160.0, 0.0), (180.0, 2.0), (200.0, 4.0), (175.0, -1.5)]:
        P_true = geom.tip_position(phi_t, psi_t)
        u, v, ok = cam.project(P_true)
        if not ok:
            print('  %6.1f°  %5.1f°  |  (不在视野内)' % (phi_t, psi_t))
            continue
        r = tip_3d_from_pixel(cam, geom, u, v)
        err = float(np.linalg.norm(r['P'] - P_true))
        print('  %6.1f°  %5.1f°  | (%6.1f,%6.1f) |  %7.3f°  %6.2f°   %.2e'
              % (phi_t, psi_t, u, v, r['azimuth_deg'], r['out_of_plane_deg'], err))

    # ── 净空互校：三维坐标重算的净空 vs 真值 ──
    P_true = geom.tip_position(180.0, 3.0)               # 底部、面外偏 3°
    u, v, _ = cam.project(P_true)
    r = tip_3d_from_pixel(cam, geom, u, v)
    print('\n[互校] 底部叶尖面外偏3°：真净空 %.3f m，反解净空 %.3f m，面外位移 %.3f m'
          % (geom.decompose(P_true)['clearance_m'], r['clearance_m'], r['out_of_plane_m']))

    # ── A vs B 同步验证：同一段轨迹，两方案并行 ──
    print('\n[同步验证] 恒定 8.0 rpm 扫过底部；方案A用真转速积分，方案B逐帧反解')
    rpm_true = 8.0
    fps = 25.0
    integ_ok = RpmAzimuthIntegrator(geom)      # 用真转速
    integ_bad = RpmAzimuthIntegrator(geom)     # 转速有 3% 偏差 → 演示漂移
    phi0 = 155.0
    integ_ok.seed(phi0, 0.0)
    integ_bad.seed(phi0, 0.0)
    print('   t(s)  真方位角 | B反解    B误差 | A(准转速) A误差 | A(转速+3%) A误差')
    for k in range(0, 16, 3):
        t = k / fps
        phi_true = phi0 + 360.0 * rpm_true / 60.0 * t
        P_true = geom.tip_position(phi_true, 0.0)
        u, v, ok = cam.project(P_true)
        sB = '   (视野外)      '
        if ok:
            rb = tip_3d_from_pixel(cam, geom, u, v)
            sB = ' %7.3f° %7.4f°' % (rb['azimuth_deg'], rb['azimuth_deg'] - phi_true % 360.0)
        ra = integ_ok.step(t, rpm_true)
        rb2 = integ_bad.step(t, rpm_true * 1.03)
        print('  %5.2f  %7.2f° |%s | %8.3f° %7.4f° | %8.3f° %7.4f°'
              % (t, phi_true % 360.0, sB,
                 ra['azimuth_deg'], ra['azimuth_deg'] - phi_true % 360.0,
                 rb2['azimuth_deg'], rb2['azimuth_deg'] - phi_true % 360.0))

    print('\n结论：B 逐帧独立观测，误差不随时间累积；A 在转速有偏差时相位持续漂移。')

    # ── 敏感性：上面 1e-15 只说明几何自洽，真实精度由【标定参数误差】决定 ──
    print('\n[敏感性] 用真几何投影出像素，再用【错的】几何反解，看误差有多大')
    P_ref = geom.tip_position(180.0, 2.0)
    u0, v0, _ = cam.project(P_ref)
    ref = geom.decompose(P_ref)

    def probe(tag, g2=None, du=0.0, dv=0.0):
        g2 = g2 or geom
        r = tip_3d_from_pixel(cam, g2, u0 + du, v0 + dv)
        if r is None:
            print('  %-22s 无交点' % tag); return
        print('  %-22s 方位角 %+7.3f°  净空 %+7.3f m  面外 %+7.3f m  位置 %6.3f m'
              % (tag, r['azimuth_deg'] - ref['azimuth_deg'],
                 r['clearance_m'] - ref['clearance_m'],
                 r['out_of_plane_m'] - ref['out_of_plane_m'],
                 float(np.linalg.norm(r['P'] - P_ref))))

    G = lambda **kw: TurbineGeometry(**{**dict(hub_height_m=geom.H, overhang_m=geom.d,
                                               tilt_deg=math.degrees(geom.tau),
                                               tip_radius_m=geom.R,
                                               tower_radius_m=geom.r_tower), **kw})
    probe('像素噪声 ±2px(横)', du=2.0)
    probe('像素噪声 ±2px(纵)', dv=2.0)
    probe('主轴倾角错 1°',   G(tilt_deg=math.degrees(geom.tau) + 1.0))
    probe('overhang 错 0.5m', G(overhang_m=geom.d + 0.5))
    probe('轮毂高错 1m',      G(hub_height_m=geom.H + 1.0))
    probe('叶尖半径错 0.5m',  G(tip_radius_m=geom.R + 0.5))
    print('  注：横向像素→方位角，纵向像素→面外/净空，两者近似解耦(相机按 ê₁ 定右方向)。')
    print('  自测仅验证指定球面几何；FAST.Farm 截面必须直接读取三维数据。')
