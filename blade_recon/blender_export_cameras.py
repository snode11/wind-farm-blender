"""
blender_export_cameras.py —— 从你们的 Blender 场景导出 recon.py 需要的 cameras.json。

    blender 场景.blend --background --python blender_export_cameras.py -- \
        --cams C1 C2 C3 --hub Hub_T1 --upwind 1 0 0 --frame 1 --out cameras.json

做的事：
  1. 读三台相机在 --frame 时刻的世界矩阵与镜头参数 → K、T_cv_from_world(Blender 世界系)；
  2. 把世界系换成 model.py 的机组系(原点塔底、z 上、x 指向上风)：
     需要轮毂对象(或 --hub-xyz)和上风方向 --upwind(Blender 世界系水平向量)；
  3. 写入 turbine 参数(默认 NREL 5MW，按需改 --hub-height/--overhang/--tilt/--cone)。
相机相对机舱固定、机舱不偏航时，一帧的外参即可用于整段视频；机舱有偏航/塔顶晃动时，
需逐帧导出(本版 recon.py 按固定外参处理)。
"""
import argparse
import json
import math
import sys

import bpy
import numpy as np


def args_after_dashes():
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument('--cams', nargs='+', default=['C1', 'C2', 'C3'])
    ap.add_argument('--hub', default=None, help='轮毂对象名')
    ap.add_argument('--hub-xyz', type=float, nargs=3, default=None)
    ap.add_argument('--upwind', type=float, nargs=3, default=[1, 0, 0])
    ap.add_argument('--frame', type=int, default=1)
    ap.add_argument('--hub-height', type=float, default=90.0)
    ap.add_argument('--overhang', type=float, default=5.0)
    ap.add_argument('--tilt', type=float, default=5.0)
    ap.add_argument('--cone', type=float, default=2.5)
    ap.add_argument('--fps', type=float, default=None)
    ap.add_argument('--out', default='cameras.json')
    return ap.parse_args(argv)


def K_of(cam_obj, scene):
    cd = cam_obj.data
    W = int(scene.render.resolution_x * scene.render.resolution_percentage / 100)
    H = int(scene.render.resolution_y * scene.render.resolution_percentage / 100)
    if cd.sensor_fit == 'VERTICAL':
        fy = H / (2 * math.tan(cd.angle_y / 2)); fx = fy
    else:
        fx = W / (2 * math.tan(cd.angle_x / 2)); fy = fx
    # 像素中心约定：左上像素中心为 (0,0)；Blender 的 shift_x/y 以较长边为单位
    cx = (W - 1) / 2 - cd.shift_x * max(W, H)
    cy = (H - 1) / 2 + cd.shift_y * max(W, H)
    return W, H, np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1.0]])


def main():
    a = args_after_dashes()
    sc = bpy.context.scene
    sc.frame_set(a.frame)
    hub = np.array(bpy.data.objects[a.hub].matrix_world.translation) if a.hub else np.array(a.hub_xyz)
    xu = np.array(a.upwind, float); xu[2] = 0; xu /= np.linalg.norm(xu)
    z = np.array([0, 0, 1.0]); y = np.cross(z, xu)
    origin = hub - a.overhang * xu - a.hub_height * z          # 机组系原点(塔底)
    R_wm = np.stack([xu, y, z], 1)                             # 机组系 → 世界系 的旋转
    T_wm = np.eye(4); T_wm[:3, :3] = R_wm; T_wm[:3, 3] = origin

    cams = []
    for name in a.cams:
        ob = bpy.data.objects[name]
        M = np.array(ob.matrix_world)
        M[:3, :3] = M[:3, :3] / np.linalg.norm(M[:3, :3], axis=0)   # 去掉缩放
        T_w_cv = M.copy(); T_w_cv[:3, :3] = M[:3, :3] @ np.diag([1, -1, -1])   # Blender 相机 → CV
        T_cv_m = np.linalg.inv(T_w_cv) @ T_wm
        W, H, K = K_of(ob, sc)
        cams.append({'name': name, 'W': W, 'H': H, 'K': K.tolist(), 'T_cv_from_world': T_cv_m.tolist()})
    out = {'turbine': {'hub_height_m': a.hub_height, 'overhang_m': a.overhang,
                       'tilt_deg': a.tilt, 'cone_deg': a.cone},
           'fps': a.fps or sc.render.fps / sc.render.fps_base,
           'note': 'T_cv_from_world 已换到 model.py 机组系', 'cameras': cams}
    with open(a.out, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print('[blade_recon] wrote', a.out)


main()
