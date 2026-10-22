"""
WFCRL 风场 3D 可视化（RViz 式），第 1 步：静态尾流场 + turbine glyph。

- 尾流场：FLORIS 水平切面（hub 高度），半透明彩色平面
- turbine：塔筒(cylinder) + 机舱(box) + 转子盘(disc)，按 yaw 朝向
- 交互：鼠标轨道/缩放/平移（PyVista），或 --screenshot 出 PNG

用法：
    # 交互窗口（在有显示器的本机跑）
    python viz_farm3d.py

    # 无窗口，仅存截图
    python viz_farm3d.py --screenshot out.png

    # 换风场
    python viz_farm3d.py --env HornsRev2_Floris
"""
import argparse

import numpy as np
import pyvista as pv

from wfcrl import environments as envs


def get_floris(env):
    """从 WFCRL env 逐层挖出 FLORIS interface。"""
    obj = env
    for _ in range(6):
        if hasattr(obj, "mdp"):
            return obj.mdp.interface.fi
        obj = getattr(obj, "env", obj)
    raise RuntimeError("找不到 mdp / FLORIS interface")


def extract_wake_plane(fi, hub_h, yaw_angles=None):
    """返回 (X, Y, Z, U) 均为 (nx, ny, 1)，可直接喂 StructuredGrid。

    yaw_angles: 传入当前偏航（FLORIS 形状 (n_wd,n_ws,n_turb)），
    保证切面尾流与画出来的机组朝向一致；None 则用 FLORIS 当前状态。
    """
    if yaw_angles is None:
        yaw_angles = fi.floris.farm.yaw_angles
    plane = fi.calculate_horizontal_plane(height=hub_h, yaw_angles=yaw_angles)
    df = plane.df
    n = int(round(np.sqrt(len(df))))  # 200
    # df 为 C-order，x1 变化最快 → reshape 得 [x2_idx, x1_idx]，转置成 [x1, x2]
    def grid(col):
        return df[col].values.reshape(n, n).T[:, :, None]
    return grid("x1"), grid("x2"), np.full((n, n, 1), hub_h), grid("u")


def add_wake_plane(pl, fi, hub_h, yaw_angles=None, clim=None,
                   return_actor=False):
    """FLORIS-proxy 水平切面。`return_actor=True` 时返回 (mesh, actor)。

    双视图要把**同一个 actor** 挂进第二个 renderer（VTK 允许，不复制网格），
    所以需要拿到它；默认仍只返回 mesh，老调用方不用改。
    """
    X, Y, Z, U = extract_wake_plane(fi, hub_h, yaw_angles)
    sgrid = pv.StructuredGrid(X, Y, Z)
    sgrid["wind_speed"] = U.flatten(order="F")
    u_free = float(np.ravel(fi.floris.flow_field.wind_speeds)[0])
    if clim is None:
        # 从 0 起标会让整场饱和成一片红——尾流亏损只占来流的一小段，
        # 色带必须铺在“有信息”的区间上。取来流的一半为下界：近尾流最深处
        # 会压到色带底端（可接受），换来整片尾流的层次清晰可辨。
        clim = [0.5 * u_free, u_free]
    actor = pl.add_mesh(
        sgrid, scalars="wind_speed", cmap="turbo", opacity=0.75,
        clim=clim,
        scalar_bar_args=dict(title="U [m/s]"), show_edges=False,
    )
    return (sgrid, actor) if return_actor else sgrid


# --- FAST.Farm 真实扰动风切面 ------------------------------------------------
# 上面那张是 FLORIS 稳态解析解（proxy）。下面这两个函数吃 wakevtk.DisXYFrame，
# 画的是 FAST.Farm 自己落盘的扰动风 —— 有输运时延、有蜿蜒、有湍流结构。
# 两条路径的**点序约定必须统一**放在这里：DisXY 的 u 是 (ny, nx)，而
# StructuredGrid 要 (nx, ny, 1) 且标量按 order="F" 展平，转置漏一次就整张图转 90°。

def vtk_plane_scalars(fr):
    """DisXYFrame.u (ny,nx) → 与 vtk_plane_mesh 点序对齐的一维标量。"""
    return np.asarray(fr.u, dtype=float).T.ravel(order="F")


def vtk_plane_mesh(fr, z):
    """DisXYFrame 的网格 → StructuredGrid（放在切面高度 z 上）。"""
    X, Y = np.meshgrid(np.asarray(fr.x, dtype=float),
                       np.asarray(fr.y, dtype=float), indexing="ij")
    Z = np.full(X.shape, float(z))
    return pv.StructuredGrid(X[:, :, None], Y[:, :, None], Z[:, :, None])


def add_vtk_wake_plane(pl, fr, z, u_free=None, clim=None):
    """把一张真实扰动风切面加进场景，返回 (sgrid, actor)。

    色带下界同样不从 0 起（见 add_wake_plane 的理由），但真实切面的近尾流比
    稳态解深得多（实测低到 3.4 m/s ≈ 0.43·u∞），所以下界放到 0.35·u∞，
    否则最深的那片尾流会整块贴在色带底端、看不出内部结构。
    """
    sgrid = vtk_plane_mesh(fr, z)
    sgrid["wind_speed"] = vtk_plane_scalars(fr)
    if clim is None:
        u0 = float(u_free) if u_free else float(np.nanpercentile(fr.u, 95))
        clim = [0.35 * u0, u0]
    actor = pl.add_mesh(sgrid, scalars="wind_speed", cmap="turbo", opacity=0.75,
                        clim=clim, show_edges=False,
                        scalar_bar_args=dict(title="U [m/s]"))
    return sgrid, actor


# NREL 5MW 实际值，出自 OpenFAST 模板（TowerHt=87.6、TipRad=63、HubRad=1.5，
# 见 wfrl/viz/geometry.py）。这里保留成常量是给静态出图的柱体近似用；交互界面
# 走 animate.build_turbine 的真实放样几何，不用这两个数。
TOWER_H = 88.0     # 轮毂高度 (m)：塔顶 87.6 + 轴倾抬升 0.44
BLADE_LEN = 61.5   # 单叶片长度 (m) = TipRad - HubRad


def add_turbine(pl, x, y, yaw_deg, hub_h=TOWER_H, blade_len=BLADE_LEN,
                azimuth_deg=0.0, scale=1.0):
    """一台真实几何风机：塔筒 + 机舱 + 轮毂 + 三叶片。

    yaw_deg    : 偏航角（0 = 转子正对来流 +x）
    azimuth_deg: 转子绕轴自转相位（动画里逐帧递增即可让叶片转起来）
    scale      : 整台等比例放大系数（塔筒高度、轮毂位置、叶片长度、各部件
                 粗细一起乘），比例始终与真机一致、叶片永不穿地；默认 1=物理
                 尺寸，整场 3600m 视图里机组偏小，传 scale>1 让整台变大看清。
    """
    # 整台等比例放大：塔高、轮毂高度、叶片都随 scale 长大，比例不变
    hub_h = hub_h * scale
    blade_len = blade_len * scale
    yaw = np.deg2rad(yaw_deg)
    # 转子轴（水平面内，随 yaw 转）；转子平面由 竖直 + 面内水平 张成
    axis = np.array([np.cos(yaw), np.sin(yaw), 0.0])   # 机舱/转子指向
    u_vert = np.array([0.0, 0.0, 1.0])
    u_horiz = np.array([np.sin(yaw), -np.cos(yaw), 0.0])

    # 转子悬伸：hub 从塔顶沿轴向上风向前移，使转子旋转平面平行于塔筒竖直面
    # 但不与塔筒相交——叶片扫过时才不会打到塔筒（真实上风向机型如此）
    tower_top = np.array([x, y, hub_h])
    overhang = 6.0 * scale                     # ≈ 机舱半长，转子落在机舱前端面
    hub = tower_top - overhang * axis

    tower = pv.Cylinder(center=(x, y, hub_h / 2), direction=(0, 0, 1),
                        radius=2.2 * scale, height=hub_h, resolution=24,
                        capping=True)
    # 机舱（长方体，沿转子轴朝向，坐在塔顶，前端伸到 hub）
    nac = pv.Cube(center=tuple(tower_top - 0.5 * overhang * axis),
                  x_length=12.0 * scale, y_length=4.0 * scale,
                  z_length=4.0 * scale)
    nac.rotate_z(yaw_deg, point=tuple(tower_top), inplace=True)
    # 轮毂
    hub_ball = pv.Sphere(radius=3.0 * scale, center=tuple(hub))

    actors = [
        pl.add_mesh(tower, color="whitesmoke"),
        pl.add_mesh(nac, color="dimgray"),
        pl.add_mesh(hub_ball, color="gray"),
    ]
    # 三片叶片，互成 120°，位于转子平面内
    for i in range(3):
        th = np.deg2rad(azimuth_deg + i * 120.0)
        d = np.cos(th) * u_vert + np.sin(th) * u_horiz  # 叶片指向（单位向量）
        mid = hub + 0.5 * blade_len * d
        blade = pv.Cylinder(center=tuple(mid), direction=tuple(d),
                            radius=1.8 * scale, height=blade_len, resolution=12)
        actors.append(pl.add_mesh(blade, color="ghostwhite"))
    return actors


def build_scene(env, yaws=None, off_screen=False, zscale=1.0, turb_scale=5.0):
    fi = get_floris(env)
    hub_h = float(np.ravel(fi.floris.farm.hub_heights)[0])
    lx, ly = np.asarray(fi.layout_x), np.asarray(fi.layout_y)
    if yaws is None:
        yaws = np.zeros(len(lx))

    pl = pv.Plotter(off_screen=off_screen)
    pl.set_background("white")
    add_wake_plane(pl, fi, hub_h)
    for x, y, yw in zip(lx, ly, yaws):
        add_turbine(pl, x, y, yw, hub_h, azimuth_deg=20.0, scale=turb_scale)

    # 垂直放大：域宽数千米、塔筒仅百米，不放大机组会被压扁看不见
    pl.set_scale(zscale=zscale)
    # 地面网格 + 坐标轴（RViz 感）
    pl.show_grid(color="gray")
    pl.add_axes(line_width=3)
    pl.camera_position = "yz"
    pl.camera.azimuth = -60
    pl.camera.elevation = 25
    pl.add_text(f"WFCRL wake field — FLORIS  (z x{zscale:g})",
                font_size=12, color="black")
    return pl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default="Ablaincourt_Floris")
    ap.add_argument("--screenshot", default=None,
                    help="给路径则无窗口存 PNG；否则开交互窗口")
    ap.add_argument("--turb-scale", type=float, default=5.0,
                    help="机组可视化放大系数（1=物理尺寸；整场视图默认放大看清）")
    ap.add_argument("--zscale", type=float, default=1.0,
                    help="垂直方向放大系数（1=真实比例；过大塔筒会变针状、转子被压成椭圆）")
    args = ap.parse_args()

    raw = envs.make(args.env, controls=["yaw"], max_num_steps=50)
    raw.reset()
    n_turb = len(np.asarray(get_floris(raw).layout_x))
    # 给个非零 yaw，视觉上能看出朝向差异
    yaws = np.linspace(-30, 30, n_turb)
    raw.step({"yaw": yaws.astype(np.float32)})

    off = args.screenshot is not None
    pl = build_scene(raw, yaws=yaws, off_screen=off,
                     zscale=args.zscale, turb_scale=args.turb_scale)
    if off:
        pl.screenshot(args.screenshot)
        print(f"[viz] saved screenshot -> {args.screenshot}")
    else:
        print("[viz] 交互窗口：鼠标左键轨道 / 滚轮缩放 / 中键平移，q 退出")
        pl.show()


if __name__ == "__main__":
    main()
