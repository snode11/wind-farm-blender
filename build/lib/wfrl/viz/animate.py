"""
第 2 步：策略驱动的 3D 尾流动画。

加载训练好的 MAPPO 模型，在多智能体 env 上逐步 rollout；每个 control step：
  - 用共享策略给出各机组 yaw
  - env 步进 → FLORIS 重算尾流
  - 更新 3D 场景的尾流切面标量 + 按新 yaw 重画机组

用法：
    # 交互窗口（默认；单次阻塞 show + 定时器逐步推进，Windows 上最稳）
    python animate_farm3d.py --steps 60

    # 输出 GIF（离屏，可在任意机器跑）
    python animate_farm3d.py --gif rollout.gif --steps 40
"""
import argparse
import os

import numpy as np
import pyvista as pv
from stable_baselines3.common.vec_env import VecNormalize

from wfrl import mappo_sb3 as T
from wfrl.viz.field import BLADE_LEN, add_wake_plane, extract_wake_plane
# 4x4 齐次变换助手挪到 geometry.py：扫塔间隙要用同一条装配链算距离，
# 两份实现一旦漂移，算出来的间隙就和画面上的不是一回事了。
from wfrl.viz.geometry import (rot_x as _rot_x, rot_y as _rot_y,
                               rot_z as _rot_z, trans as _trans)


_GEO_CACHE = {}


def _geometry():
    """真实机组几何，解析一次缓存住；解析失败返回 None 走柱体退化路径。"""
    if "geo" not in _GEO_CACHE:
        try:
            from wfrl.viz.geometry import load_turbine_geometry
            _GEO_CACHE["geo"] = load_turbine_geometry()
        except Exception as e:                                  # noqa: BLE001
            print(f"[anim] 真实几何解析失败，退回柱体近似: {e!r}")
            _GEO_CACHE["geo"] = None
    return _GEO_CACHE["geo"]


def _build_real(pl, x, y, hub_h, scale, geo):
    """按 OpenFAST 模板的真实几何建机组。

    坐标约定（canonical，yaw=0）：+x 顺风、+z 向上、转子在上风向（OverHang<0）。
    叶片网格用局部系（x=轴向/预弯, y=切向/弦, z=展向），每片一个 actor，
    这样 pitch（各绕自己展向轴转）才能表示出来。
    变换链：yaw ∘ 轴倾 ∘ 方位角 ∘ 锥角 ∘ 桨距 ∘ 局部网格。
    """
    hub_z = hub_h * scale                       # 让轮毂落在调用方给的高度上
    tilt_rise = (geo.hub_height - geo.tower_ht) * scale
    top_z = hub_z - tilt_rise                   # 塔顶（未倾斜前的轴心高度）

    # 塔筒：真实锥度，z 拉伸到 top_z（真实 87.6 与 FLORIS 的 90 差 2.4 m）
    tower = geo.tower_surface()
    tp = tower.points.copy()
    tp[:, :2] *= scale
    tp[:, 2] *= top_z / geo.tower_ht
    tp[:, 0] += x; tp[:, 1] += y
    tower.points = tp

    tower_top = np.array([x, y, top_z])
    overhang = geo.overhang * scale             # 负 = 上风向
    hub_flat = tower_top + np.array([overhang, 0.0, 0.0])
    # 轴倾绕塔顶的横轴转；ShftTilt<0 表示上风端上抬 ⇒ 取负号
    M_tilt = (_trans(tower_top) @ _rot_y(-geo.shft_tilt)
              @ _trans(-tower_top))
    M_hub = M_tilt @ _trans(hub_flat)           # 叶片局部原点 → 轮毂中心
    hub_c = (M_hub @ np.array([0.0, 0.0, 0.0, 1.0]))[:3]

    # 机舱 + 导流罩：尺寸按真实悬伸/轮毂半径推，不再是拍脑袋的 12×4×4
    nac_len = abs(geo.overhang) * 1.6 * scale
    nac_w = geo.hub_rad * 2.2 * scale
    nac = pv.Cube(center=(0.5 * nac_len + overhang * 0.15, 0.0, 0.0),
                  x_length=nac_len, y_length=nac_w, z_length=nac_w)
    spinner = pv.Sphere(radius=geo.hub_rad * 1.6 * scale)
    nac_mesh = (nac + spinner).transform(M_hub, inplace=False)

    blade = geo.blade_surface()
    blade.points = blade.points * scale
    xi = np.asarray(blade.point_data["xi"], dtype=float)

    tower_actor = pl.add_mesh(tower, color="whitesmoke", smooth_shading=True)
    nac_actor = pl.add_mesh(nac_mesh, color="dimgray")
    # 三片叶各自一份**独立**网格：柔性变形逐叶片不同（风剪切 + 塔影 + 偏航误差
    # 让三片的根部弯矩不同相），不能共用同一个 mesh 对象。
    blade_meshes = [blade.copy() for _ in range(3)]
    blade_actors = [pl.add_mesh(m, color="ghostwhite", smooth_shading=True)
                    for m in blade_meshes]
    return {"x": x, "y": y, "hub_c": hub_c, "real": True,
            "M_hub": M_hub, "precone": geo.precone,
            "tower": tower_actor, "nac": nac_actor,
            "blades": blade_actors, "rotor": blade_actors[0],
            # 柔性变形所需：设计态基准点 + 归一化展向 + 几何（带振型与标定）
            "geo": geo, "blade_meshes": blade_meshes,
            "blade_base": blade.points.copy(), "blade_xi": xi,
            "blade_scale": scale}


def _build_cyl(pl, x, y, hub_h, scale):
    """柱体近似（真实几何解析不出来时的退化路径），保持原有观感。"""
    hub_h = hub_h * scale
    blade_len = BLADE_LEN * scale
    overhang = 6.0 * scale
    tower_top = np.array([x, y, hub_h])
    axis = np.array([1.0, 0.0, 0.0])            # canonical 转子轴 (yaw=0)
    hub_c = tower_top - overhang * axis         # 悬伸后的轮毂中心
    u_vert = np.array([0.0, 0.0, 1.0])
    u_horiz = np.array([0.0, -1.0, 0.0])        # yaw=0 时转子面内水平方向

    tower = pv.Cylinder(center=(x, y, hub_h / 2), direction=(0, 0, 1),
                        radius=2.2 * scale, height=hub_h, resolution=24,
                        capping=True)
    nac = pv.Cube(center=tuple(tower_top - 0.5 * overhang * axis),
                  x_length=12.0 * scale, y_length=4.0 * scale,
                  z_length=4.0 * scale)
    hub_ball = pv.Sphere(radius=3.0 * scale, center=tuple(hub_c))
    blades = None
    for i in range(3):
        th = np.deg2rad(i * 120.0)
        d = np.cos(th) * u_vert + np.sin(th) * u_horiz
        mid = hub_c + 0.5 * blade_len * d
        b = pv.Cylinder(center=tuple(mid), direction=tuple(d),
                        radius=1.8 * scale, height=blade_len, resolution=12)
        blades = b if blades is None else blades + b

    tower_actor = pl.add_mesh(tower, color="whitesmoke")
    nac_actor = pl.add_mesh(nac + hub_ball, color="dimgray")
    rotor_actor = pl.add_mesh(blades, color="ghostwhite")
    return {"x": x, "y": y, "hub_c": hub_c, "real": False,
            "tower": tower_actor, "nac": nac_actor, "rotor": rotor_actor}


def build_turbine(pl, x, y, hub_h, scale=1.0, real=True):
    """建一台风机（canonical: yaw=0, spin=0），返回可逐帧变换的 actor 句柄。

    默认用 OpenFAST 模板里的真实几何（真实翼型/弦长/扭角/锥度/轴倾/悬伸）；
    解析不出来就退回原来的柱体近似。塔筒建好后静止不动，只改机舱与叶片的
    user_matrix —— 不重建、无闪烁。
    """
    geo = _geometry() if real else None
    if geo is not None:
        try:
            return _build_real(pl, x, y, hub_h, scale, geo)
        except Exception as e:                                  # noqa: BLE001
            print(f"[anim] 真实几何建模失败，退回柱体近似: {e!r}")
    return _build_cyl(pl, x, y, hub_h, scale)


def turbine_actors(turb):
    """这台机组的全部 actor（拖拽拾取 / 批量移动用）。"""
    acts = [turb["tower"], turb["nac"]]
    acts += turb.get("blades", [turb["rotor"]] if "rotor" in turb else [])
    return acts


def move_turbine_actors(turb, dx, dy):
    """把整台机组平移 (dx, dy)（拖拽时用，不重建网格）。

    塔筒没有 user_matrix，直接改 actor.position；机舱/叶片的姿态是每帧算出来的
    user_matrix，得把平移并进它们的基准矩阵里 —— 光改 position 会和 user_matrix
    里烧死的旧轮毂位置打架（VTK 是先 position 后 UserMatrix）。
    """
    turb["x"] += dx
    turb["y"] += dy
    turb["hub_c"] = turb["hub_c"] + np.array([dx, dy, 0.0])
    # 塔筒(无 user_matrix)与机舱(user_matrix 只是绕新塔轴的纯旋转)都可以走 position
    keys = ["tower", "nac"] if turb.get("real") else ["tower", "nac", "rotor"]
    for key in keys:
        a = turb[key]
        a.position = (a.position[0] + dx, a.position[1] + dy, a.position[2])
    # 叶片的 user_matrix 里烧着轮毂的绝对位置，平移必须并进 M_hub
    if turb.get("real"):
        turb["M_hub"] = _trans((dx, dy, 0.0)) @ turb["M_hub"]


def deform_blades(turb, m_flap=None, m_edge=None, scale=1.0, pitch_deg=0.0):
    """按逐叶片根部弯矩更新三片叶的网格顶点（柔性变形），并算扫塔间隙。

    m_flap / m_edge：长度 3 的数组，N·m，来自 MPI 实时回传的
    RootMyc(1..3) / RootMxc(1..3)（driver 的 m_flap/m_edge 字段）。
    nan 的那片按不变形处理 —— 启动瞬态和截断轮里会出现 nan。

    只改顶点、不重建 actor，姿态仍由 user_matrix 管：变形在**叶片局部坐标系**
    里做，和自转/桨距/偏航的刚体变换正交，两者可以各自独立更新。

    scale 是演示放大倍数，物理分析时保持 1.0。放大只作用在挠度上、不放大塔筒，
    所以先用 `safe_flex_scale` 压到不穿塔为止；间隙本身按 **1× 真值** 算，写进
    `turb["clearance"]`（长度 3，m）供遥测显示 —— 屏幕上没穿模不等于物理没扫塔，
    反过来也一样，两个量分开报。
    """
    res = deform_solve(turb, m_flap, m_edge, scale=scale, pitch_deg=pitch_deg)
    deform_apply(turb, res)


def deform_solve(turb, m_flap=None, m_edge=None, scale=1.0, pitch_deg=0.0):
    """算 —— 纯 numpy，不碰任何 VTK 对象，**可以在工作线程里跑**。

    与 `deform_apply` 分家的理由是实测的：整段（求解+贴点）在主线程里中位
    192 ms、峰值 326 ms，每个控制步一次，30 fps 的渲染回调根本容不下；而其中
    贴点只占几毫秒，重的是 `safe_flex_scale` 的不穿塔搜索与逐片挠度积分。
    """
    if not turb.get("real") or "blade_meshes" not in turb:
        return None
    geo, base, xi = turb["geo"], turb["blade_base"], turb["blade_xi"]
    g_scale = turb.get("blade_scale", 1.0)
    base_true = base / g_scale
    load = [{"m_flap": _pick(m_flap, i), "m_edge": _pick(m_edge, i)}
            for i in range(len(turb["blade_meshes"]))]
    live = [d["m_flap"] is not None or d["m_edge"] is not None for d in load]
    clr = [geo.tower_clearance(base_true, xi, pitch=pitch_deg, **d) if ok
           else np.nan for d, ok in zip(load, live)]
    # 三片共用一个放大倍数（取各片的上限里最小的那个）：逐片各放大不同倍数的话，
    # 屏幕上三片的弯曲差异就不再是弯矩差异，那正是这张图要看的东西。
    hint = turb.get("flex_scale_used")
    s = min([geo.safe_flex_scale(base_true, xi, scale, hint=hint,
                                 pitch=pitch_deg, **d)
             for d, ok in zip(load, live) if ok], default=float(scale))
    pts = [None if not ok
           else geo.blade_deform(base_true, xi, scale=s, **d) * g_scale
           for d, ok in zip(load, live)]
    return {"points": pts, "clearance": np.asarray(clr, dtype=float),
            "scale_used": s}


def deform_apply(turb, res):
    """贴 —— 只做顶点赋值。碰 VTK，必须在建 actor 的那个线程（主线程）上调。"""
    if res is None:
        return
    turb["flex_scale_used"] = res["scale_used"]
    for mesh, p in zip(turb["blade_meshes"], res["points"]):
        mesh.points = turb["blade_base"] if p is None else p
    turb["clearance"] = res["clearance"]


def _pick(arr, i):
    """取第 i 片的弯矩；给标量就三片共用；nan/None 一律返回 None。"""
    if arr is None:
        return None
    v = np.ravel(np.asarray(arr, dtype=float))
    if v.size == 0:
        return None
    x = float(v[i] if v.size > i else v[0])
    return None if not np.isfinite(x) else x


def update_turbine(turb, yaw_deg, spin_deg, pitch_deg=0.0,
                   m_flap=None, m_edge=None, flex_scale=1.0):
    """塔筒不动；机舱随 yaw；叶片再叠方位角自转与桨距，并按弯矩柔性变形。

    spin_deg 是转子方位角（度，绕轴倾后的主轴）；pitch_deg 是统一变桨角，
    每片各绕自己的展向轴转 —— 所以真实几何路径下三片是独立 actor。
    m_flap/m_edge 给了就顺带更新柔性变形（见 deform_blades）。
    """
    x, y, hub_c = turb["x"], turb["y"], turb["hub_c"]
    M_yaw = _trans((x, y, 0.0)) @ _rot_z(yaw_deg) @ _trans((-x, -y, 0.0))
    turb["nac"].user_matrix = M_yaw

    if not turb.get("real"):
        M_spin = _trans(hub_c) @ _rot_x(spin_deg) @ _trans(-hub_c)
        turb["rotor"].user_matrix = M_yaw @ M_spin
        return

    # 顺桨方向：+pitch 让叶片前缘转向上风向（feather）。原来 +_rot_z(pitch) 把
    # 前缘转向了下风向 —— 90° 全顺桨时看着是反的。绕展向轴取负号即纠正。
    # 变形/扫塔净空用同一个角度，一起取负保持自洽。
    pitch_use = -float(pitch_deg)
    if m_flap is not None or m_edge is not None:
        deform_blades(turb, m_flap, m_edge, scale=flex_scale,
                      pitch_deg=pitch_use)

    base = M_yaw @ turb["M_hub"]
    M_pitch = _rot_y(turb["precone"]) @ _rot_z(pitch_use)
    for i, act in enumerate(turb["blades"]):
        act.user_matrix = base @ _rot_x(spin_deg + 120.0 * i) @ M_pitch


def find_floris(root):
    """在 VecNormalize/VecEnv/PettingZoo 包装链里 BFS 找到 FLORIS interface。"""
    names = ["venv", "par_env", "aec_env", "env", "unwrapped", "aec"]
    seen, stack = set(), [root]
    while stack:
        obj = stack.pop()
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        if hasattr(obj, "mdp"):
            return obj.mdp.interface.fi
        for n in names:
            if hasattr(obj, n):
                stack.append(getattr(obj, n))
    raise RuntimeError("包装链里找不到 FLORIS interface")


def load_model_and_env():
    """复用 train_mappo 的 env 构造 + 归一化统计 + 训练好的策略。"""
    from stable_baselines3 import PPO

    venv = T.make_venv(norm_reward=False, training=False)
    if os.path.exists(T.VECNORM_PATH):
        venv = VecNormalize.load(T.VECNORM_PATH, venv.venv)
        venv.training = False
        venv.norm_reward = False
    if not os.path.exists(T.MODEL_PATH + ".zip"):
        raise FileNotFoundError(
            f"没找到模型 {T.MODEL_PATH}.zip，先跑 train_mappo.py 训练。")
    model = PPO.load(T.MODEL_PATH, env=venv)
    return model, venv


def current_yaws(fi):
    return np.ravel(fi.floris.farm.yaw_angles).astype(float)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", type=int, default=40)
    ap.add_argument("--gif", default=None, help="输出 GIF 路径")
    ap.add_argument("--live", action="store_true",
                    help="（默认行为）交互窗口；不加 --gif 即开窗口")
    ap.add_argument("--zscale", type=float, default=1.0)
    ap.add_argument("--turb-scale", type=float, default=5.0,
                    help="机组可视化放大系数（1=物理尺寸；整场视图默认放大看清）")
    ap.add_argument("--spin-frames", type=int, default=8,
                    help="每个控制步内插多少帧只转叶片（越大转得越平滑）")
    ap.add_argument("--spin-deg", type=float, default=15.0,
                    help="每帧叶片自转角度（度）")
    args = ap.parse_args()

    model, venv = load_model_and_env()
    fi = find_floris(venv)
    hub_h = float(np.ravel(fi.floris.farm.hub_heights)[0])
    lx, ly = np.asarray(fi.layout_x), np.asarray(fi.layout_y)

    off = args.gif is not None  # 仅写 GIF 时离屏；其余一律开窗口
    pl = pv.Plotter(off_screen=off)
    pl.set_background("white")

    # 初始帧：风机各建一次，之后只改变换矩阵（塔筒静止、机舱/转子转动）
    obs = venv.reset()
    sgrid = add_wake_plane(pl, fi, hub_h)
    turbines = [build_turbine(pl, x, y, hub_h, scale=args.turb_scale)
                for x, y in zip(lx, ly)]

    pl.set_scale(zscale=args.zscale)
    pl.show_grid(color="gray")
    pl.add_axes(line_width=3)
    pl.camera_position = "yz"
    pl.camera.azimuth = -60
    pl.camera.elevation = 25
    txt = pl.add_text("step 0", font_size=12, color="black")

    state = {"obs": obs, "spin": 0.0}  # 跨帧持有观测与叶片相位

    def redraw_turbines(spin):
        """按当前 yaw + 叶片相位更新各机组变换（不重建，无闪烁）。"""
        for turb, yw in zip(turbines, current_yaws(fi)):
            update_turbine(turb, yw, spin)

    redraw_turbines(0.0)  # 摆到初始 yaw

    def set_text(step):
        """原地更新文字（CornerAnnotation idx=2=upper_left），不 remove/add。"""
        yaw_str = ", ".join(f"{v:+.0f}" for v in current_yaws(fi))
        txt.SetText(2, f"step {step}  yaw=[{yaw_str}]")

    def control_step():
        """走一个 control step：策略给 yaw -> env 步进 -> 尾流重算。"""
        action, _ = model.predict(state["obs"], deterministic=True)
        state["obs"], _, dones, _ = venv.step(action)
        _, _, _, U = extract_wake_plane(fi, hub_h)
        sgrid["wind_speed"] = U.flatten(order="F")
        if np.asarray(dones).all():
            state["obs"] = venv.reset()

    if args.gif:
        pl.open_gif(args.gif)
        for step in range(1, args.steps + 1):
            control_step()
            for _ in range(args.spin_frames):     # 每个控制步内多帧转叶片
                state["spin"] += args.spin_deg
                redraw_turbines(state["spin"])
                set_text(step)
                pl.write_frame()
        pl.close()
        print(f"[anim] saved GIF -> {args.gif} ({args.steps} steps)")
    else:
        # 交互窗口：非阻塞 show + 手动刷新循环（Windows 上比 timer 可靠）
        print("[anim] 打开交互窗口，动画自动播放；鼠标轨道/缩放，按 q 退出")
        pl.show(interactive_update=True, auto_close=False)
        try:
            for step in range(1, args.steps + 1):
                control_step()
                for _ in range(args.spin_frames):
                    state["spin"] += args.spin_deg
                    redraw_turbines(state["spin"])
                    set_text(step)
                    pl.update()                    # 刷新窗口 + 处理交互事件
        except Exception as e:                     # 别让异常被静默吞掉
            print(f"[anim] 循环异常: {e!r}")
        print("[anim] 动画播放完毕，窗口保持可交互，按 q 关闭")
        pl.show()                                  # 阻塞，留窗口给用户查看/旋转


if __name__ == "__main__":
    main()
