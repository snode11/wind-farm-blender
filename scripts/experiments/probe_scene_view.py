"""场景 → 3D 视图的离线验收（不起 FAST.Farm，用离屏渲染）。

跑：
    "C:\\Users\\s1155\\.conda\\envs\\wfcrl\\python.exe" scripts/experiments/probe_scene_view.py

这是 D1 验收的渲染那一半。要证的是同一句话：**改 YAML 里的机位，3D 跟着变**，
而且变的是画面上机组真实所在的位置，不是某张预注册表里的坐标。

所以判据不看代码路径，看**像素与几何**：
  1. 机组 actor 的世界坐标 == 场景里的坐标（且不等于 Turb3_Row1 的 0/504/1008）
  2. 同一份代码换一份场景 YAML，包围盒与 actor 数跟着变
  3. lidar 点云真的出现在被挂载那台机组的**前方**，且只有那一台有
  4. 关掉通道 ⇒ actor 不可见 **且** 传感器停止采样；重开即恢复
  5. 偏航/桨距改变会真的改机组的变换矩阵（画面不是死的）
  6. 离屏截图非空白，且关掉 lidar 后画面像素确实变少（点云真的没画）
"""
import os
import sys
import traceback

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

import pyvista as pv                                              # noqa: E402

from wfrl.channels import registry                                # noqa: E402
from wfrl.scene.schema import (Scene, SensorSpec, Turbine,        # noqa: E402
                               load_scene)
from wfrl.studio import SceneView                                 # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FAILS = []


def check(name, cond, detail=""):
    ok = bool(cond)
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""),
          flush=True)
    if not ok:
        FAILS.append(name)


def custom_scene():
    """与 probe_scene_runtime.py 同一份非预注册布局：5D 间距 + 横向错开。"""
    return Scene(
        name="probe_view", backend="fastfarm", dt=3.0,
        turbines=[Turbine("W1", 0.0, 0.0),
                  Turbine("W2", 630.0, 126.0),
                  Turbine("W3", 1260.0, -126.0)],
        wind_speed=8.0, wind_direction=270.0, controls=["yaw"],
        sensors=[SensorSpec(type="power"), SensorSpec(type="bladeload"),
                 SensorSpec(type="lidar", turbines="W1")],
    ).validate()


def fake_ctx(sc, yaw=None, mflap=None):
    """假的一步测量，形状与 fastfarm_driver._read 的返回一致。"""
    n = sc.n
    return {
        "yaw": np.zeros(n) if yaw is None else np.asarray(yaw, float),
        "pitch": np.zeros(n), "pitch_meas": np.zeros(n),
        "torque": np.full(n, 4.0e4), "power": np.array([2.1, 1.6, 1.5])[:n],
        "rotor_speed": np.full(n, 9.0),
        "m_flap": (np.full((n, 3), 6e3) if mflap is None
                   else np.asarray(mflap, float)),
        "m_edge": np.full((n, 3), 4e3),
        "u_inf": sc.wind_speed, "t": 0.0, "_source": None,
    }


def actor_center(act):
    """actor 在世界坐标里的实际中心（已含 user_matrix / position）。"""
    b = act.GetBounds()
    return np.array([(b[0] + b[1]) / 2, (b[2] + b[3]) / 2, (b[4] + b[5]) / 2])


def ink(img):
    """非白像素数 —— 画面上"画了多少东西"的粗糙但可靠的度量。"""
    a = np.asarray(img)[..., :3].astype(int)
    return int((a.sum(axis=-1) < 720).sum())


def main():
    sc = custom_scene()
    pl = pv.Plotter(off_screen=True, window_size=(900, 640))
    view = SceneView(pl, sc, wake=False)      # 尾流平面另测，这里只看机组与传感器

    print(f"== 1. 机组画在场景写的位置上 ==  x={sc.xcoords} y={sc.ycoords}",
          flush=True)
    for i, t in enumerate(sc.turbines):
        c = actor_center(view.turbines[i]["tower"])
        check(f"{t.id} 塔筒 xy 对得上", abs(c[0] - t.x) < 1.0 and abs(c[1] - t.y) < 1.0,
              f"画在 ({c[0]:.0f}, {c[1]:.0f})，场景写 ({t.x:.0f}, {t.y:.0f})")
    xs = [actor_center(v["tower"])[0] for v in view.turbines]
    check("不是预注册的 Turb3_Row1",
          not np.allclose(xs, [0.0, 504.0, 1008.0], atol=1.0),
          f"{np.round(xs, 1)}")
    ys = [actor_center(v["tower"])[1] for v in view.turbines]
    check("y 不是全零（预注册 3T 是一条直线）", float(np.ptp(ys)) > 100.0,
          f"ptp={np.ptp(ys):.0f} m")
    b = view.bounds()
    check("包围盒罩住所有机位",
          b[0] < min(sc.xcoords) and b[1] > max(sc.xcoords)
          and b[2] < min(sc.ycoords) and b[3] > max(sc.ycoords),
          f"{np.round(b, 0)}")

    print("\n== 2. 换一份场景，画面跟着变 ==", flush=True)
    sc6 = load_scene(os.path.join(ROOT, "scenes", "turb6_grid.yaml"))
    pl6 = pv.Plotter(off_screen=True, window_size=(400, 300))
    view6 = SceneView(pl6, sc6, wake=False)
    check("6 机场景建出 6 台", len(view6.turbines) == 6, f"{len(view6.turbines)}")
    xs6 = sorted(actor_center(v["tower"])[0] for v in view6.turbines)
    check("6 机 x 与 YAML 一致",
          np.allclose(xs6, sorted(sc6.xcoords), atol=1.0), f"{np.round(xs6, 0)}")
    check("两份场景的包围盒不同", view6.bounds() != b)
    pl6.close()

    print("\n== 3. lidar 点云挂在被指定的那台机组前方 ==", flush=True)
    ctx = fake_ctx(sc)
    rt = _FakeRuntime(sc, ctx)
    view.attach_sensors(rt, ctx)
    check("lidar 有 3D 表现", view.has_actors("lidar"))
    check("power 没有 3D 表现（不该凭空造几何）", not view.has_actors("power"))
    lid_act, cloud = view._sensor_actors["lidar"][0]
    pts = np.asarray(cloud.points)
    w1 = sc.turbines[0]
    check("点云在 W1 前方（-x 侧）", bool(np.all(pts[:, 0] < w1.x)),
          f"x={pts[:, 0].min():.0f}~{pts[:, 0].max():.0f}")
    check("点云横向锁在 W1 附近，没跑到 W2/W3",
          float(np.abs(pts[:, 1] - w1.y).max()) < 200.0 * view.turb_scale,
          f"|y-y_W1|max={np.abs(pts[:, 1] - w1.y).max():.0f} m")
    check("点数 = 视线数 × 测距门", len(pts) == 5 * 3, f"{len(pts)}")
    # 采样是物理坐标（轮毂 90 m），但机组按 TURB_SCALE 画，轮毂在 450 m。
    # 点云不跟着同一个变换，雷达就悬在塔筒半腰指着没有转子的地方。
    hub_draw = sc.hub_height * view.turb_scale
    check("点云高度跟着机组的显示尺度走",
          abs(np.median(pts[:, 2]) - hub_draw) < 0.35 * hub_draw,
          f"点云 z 中位数 {np.median(pts[:, 2]):.0f} m，画面轮毂 {hub_draw:.0f} m")
    tower_top = view.turbines[0]["tower"].GetBounds()[5]
    check("点云不在塔筒半腰（那是没做尺度变换的症状）",
          np.median(pts[:, 2]) > 0.6 * tower_top,
          f"塔顶 {tower_top:.0f} m")

    print("\n== 4. 通道开关：既不画也不采 ==", flush=True)
    lidar = rt.sensor_of("lidar")
    k0 = lidar._k
    view.set_channel("lidar", False, runtime=rt)
    check("actor 不可见", lid_act.GetVisibility() == 0)
    rt.step()
    check("传感器停止采样", lidar._k == k0, f"{k0} → {lidar._k}")
    view.set_channel("lidar", True, runtime=rt)
    rt.step()
    check("重开后可见", lid_act.GetVisibility() == 1)
    check("重开后恢复采样", lidar._k > k0, f"{k0} → {lidar._k}")

    print("\n== 5. 偏航/转速真的改变机组姿态 ==", flush=True)
    m0 = np.array(view.turbines[1]["nac"].user_matrix).copy()
    view.on_frame({"_meta": {"u_inf": 8.0}},
                  fake_ctx(sc, yaw=[0.0, 25.0, 0.0]))
    view.tick(0.1)
    m1 = np.array(view.turbines[1]["nac"].user_matrix)
    check("偏航改变机舱变换矩阵", not np.allclose(m0, m1),
          f"最大差 {np.abs(m1 - m0).max():.3f}")
    check("未偏航的机组没被动到",
          np.allclose(np.array(view.turbines[0]["nac"].user_matrix),
                      np.eye(4), atol=1e-9))
    b0 = np.array(view.turbines[0]["blades"][0].user_matrix).copy()
    view.tick(0.5)                       # 9 rpm ⇒ 0.5 s 转 27°
    check("转子按 rpm 自转",
          not np.allclose(b0, np.array(view.turbines[0]["blades"][0].user_matrix)))

    print("\n== 6. 离屏出图，通道开关反映在像素上 ==", flush=True)
    # 用侧视（沿 -y 看）而不是俯视：俯视下点云正好被叶片盘挡住，关不关都一样，
    # 那样这条判据就变成了永远通过的空断言。
    pl.camera_position = "xz"
    pl.reset_camera(bounds=view.bounds())
    pl.render()
    img_on = ink(pl.screenshot(return_img=True))
    view.set_channel("lidar", False, runtime=rt)   # 内部会 render，见 view.py
    img_off = ink(pl.screenshot(return_img=True))
    check("画面非空白", img_on > 1000, f"{img_on} 个非白像素")
    check("关掉 lidar 后画面像素变少", img_off < img_on,
          f"{img_on} → {img_off}（差 {img_on - img_off}）")
    bars = pl.scalar_bars
    check("色带也跟着收（否则截图上宣称那一路还在采）",
          "lidar u (m/s)" in bars.keys()
          and bars["lidar u (m/s)"].GetVisibility() == 0)
    out = os.path.join(ROOT, "results", "runs", "_scene_view.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    view.set_channel("lidar", True, runtime=rt)
    pl.screenshot(out)
    print(f"  截图: {out}", flush=True)
    pl.close()

    print()
    if FAILS:
        print(f"SCENE_VIEW_FAILED — {len(FAILS)} 项: {FAILS}")
        return 1
    print("SCENE_VIEW_OK")
    return 0


class _FakeRuntime:
    """只提供 SceneView 用到的那点接口，免得离线验收去起 MPI。"""

    def __init__(self, scene, ctx):
        self.scene = scene
        self.ctx = ctx
        self.sensors = registry.build(scene)
        self.enabled = {t: True for s in self.sensors for t in s.topics}
        self.last_ctx = ctx

    def sensor_of(self, topic):
        return next(s for s in self.sensors if topic in s.topics)

    def set_enabled(self, topic, on):
        self.enabled[topic] = bool(on)

    def step(self):
        for s in self.sensors:
            if any(self.enabled.get(t, False) for t in s.topics):
                s.step(self.ctx)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:                                          # noqa: BLE001
        traceback.print_exc()
        print("SCENE_VIEW_FAILED — 未捕获异常")
        sys.exit(1)
