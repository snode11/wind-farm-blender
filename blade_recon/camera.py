"""
camera.py —— 针孔相机与三摄共光心扇形阵列。

约定(OpenCV)：相机系 x 右、y 下、z 前；像素原点在左上角像素中心。
    p = K · (R · X_world + t)，u = p_x/p_z，v = p_y/p_z
cameras.json 格式(三路各一项)：
    {"name","W","H","K":[[3x3]],"T_cv_from_world":[[4x4]]}
从 Blender 导出时用 blender_export_cameras.py，世界系需与 model.py 的机组系一致
(或在 recon.py 的 --T-model-from-world 里给出变换)。
"""
import math

import numpy as np


class Camera:
    def __init__(self, name, W, H, K, T_cv_from_world):
        self.name, self.W, self.H = name, int(W), int(H)
        self.K = np.asarray(K, float)
        T = np.asarray(T_cv_from_world, float)
        self.R, self.t = T[:3, :3], T[:3, 3]

    @property
    def center(self):
        return -self.R.T @ self.t

    def to_cam(self, X):
        return X @ self.R.T + self.t

    def project(self, X):
        """X (...,3) → uv (...,2), z (...)。z<=0 的点 uv 为 nan。"""
        Xc = self.to_cam(X)
        z = Xc[..., 2]
        with np.errstate(divide='ignore', invalid='ignore'):
            u = self.K[0, 0] * Xc[..., 0] / z + self.K[0, 2]
            v = self.K[1, 1] * Xc[..., 1] / z + self.K[1, 2]
        uv = np.stack([u, v], -1)
        uv[z <= 0.05] = np.nan
        return uv, z

    def scaled(self, s):
        """整体缩放分辨率。宽高取偶数(视频编码器要求)，K 按实际缩放比改。"""
        W2, H2 = 2 * round(self.W * s / 2), 2 * round(self.H * s / 2)
        sx, sy = W2 / self.W, H2 / self.H
        K = self.K.copy()
        K[0, 0] *= sx; K[1, 1] *= sy
        K[0, 2] = (K[0, 2] + 0.5) * sx - 0.5
        K[1, 2] = (K[1, 2] + 0.5) * sy - 0.5
        T = np.eye(4); T[:3, :3] = self.R; T[:3, 3] = self.t
        return Camera(self.name, W2, H2, K, T)

    def to_dict(self):
        T = np.eye(4); T[:3, :3] = self.R; T[:3, 3] = self.t
        return {'name': self.name, 'W': self.W, 'H': self.H,
                'K': self.K.tolist(), 'T_cv_from_world': T.tolist()}

    @staticmethod
    def from_dict(d):
        return Camera(d['name'], d['W'], d['H'], d['K'], d['T_cv_from_world'])


def rot_axis(axis, ang):
    axis = np.asarray(axis, float) / np.linalg.norm(axis)
    K = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    return np.eye(3) + math.sin(ang) * K + (1 - math.cos(ang)) * K @ K


def build_rig(rotor, back_m=6.0, down_m=3.0, pitch_down_deg=0.0, yaw_step_deg=50.0,
              hfov_deg=50.0, vfov_deg=40.0, W=3264, H=2548):
    """三路共光心扇形阵列：光心在轮毂后方 back_m、下方 down_m，整体朝前(上风)看，
    中路朝正前，两侧各偏 ±yaw_step_deg。光轴延长线交于共同光心。
    50°×40° 方形像素 → W/H = tan25°/tan20° = 1.281，8MP 取 3264×2548。"""
    n, up = rotor.n, rotor.e_up
    C = rotor.hub - back_m * n - down_m * up
    fx = W / (2 * math.tan(math.radians(hfov_deg) / 2))
    fy = H / (2 * math.tan(math.radians(vfov_deg) / 2))
    K = np.array([[fx, 0, (W - 1) / 2], [0, fy, (H - 1) / 2], [0, 0, 1.0]])
    cams = []
    for name, yaw in (('C1', -yaw_step_deg), ('C2', 0.0), ('C3', yaw_step_deg)):
        fwd = n.copy()
        right0 = np.cross(fwd, up)                      # 从后往前看时的右手方向
        fwd = rot_axis(right0, -math.radians(pitch_down_deg)) @ fwd
        upc = np.cross(right0, fwd)
        fwd = rot_axis(upc, -math.radians(yaw)) @ fwd    # 正 yaw = 朝右
        right = np.cross(fwd, upc); right /= np.linalg.norm(right)
        down = np.cross(fwd, right)
        R = np.stack([right, down, fwd], 0)             # 世界 → 相机
        T = np.eye(4); T[:3, :3] = R; T[:3, 3] = -R @ C
        cams.append(Camera(name, W, H, K, T))
    return cams
