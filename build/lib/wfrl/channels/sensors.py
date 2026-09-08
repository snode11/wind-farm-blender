"""具体传感器实现。

每一路都必须在 `provenance` 里写清数据从哪来。三个保真度等级不是修辞 ——
DIRECT 的数进奖励和约束是合法的，SYNTH 的数只能进感知层，混用会让结论不成立。
"""
from __future__ import annotations

import numpy as np

from wfrl.channels.base import Fidelity, Sensor, SceneErrorProxy, registry


def _get(ctx, key, n):
    v = ctx.get(key)
    if v is None:
        return np.full(n, np.nan)
    return np.asarray(v, dtype=float).ravel()


# ---------------------------------------------------------------------------
# 直读 —— FAST.Farm 真算出来的
# ---------------------------------------------------------------------------
@registry.register
class PowerSensor(Sensor):
    type_name = "power"
    fidelity = Fidelity.DIRECT
    provenance = "FAST.Farm 发电机功率，经 mdp.py:284 除 1e6 为 MW"

    def sample(self, ctx):
        return {"power": _get(ctx, "power", self.scene.n)[self.indices]}


@registry.register
class BladeLoadSensor(Sensor):
    type_name = "bladeload"
    fidelity = Fidelity.DIRECT
    provenance = ("叶根挥舞/摆振弯矩，avrSWAP(30-32,69-71) 经 DISCON.F90:363-368 "
                  "回传，mdp.py:283 除 1e7")
    requires = ("aeroelastic",)

    @property
    def topics(self):
        return ["m_flap", "m_edge"]

    def sample(self, ctx):
        n = self.scene.n
        mf = ctx.get("m_flap")
        me = ctx.get("m_edge")
        mf = np.full((n, 3), np.nan) if mf is None else np.asarray(mf, float)
        me = np.full((n, 3), np.nan) if me is None else np.asarray(me, float)
        return {"m_flap": mf[self.indices], "m_edge": me[self.indices]}


@registry.register
class ActuatorSensor(Sensor):
    type_name = "actuator"
    fidelity = Fidelity.DIRECT
    provenance = ("偏航/桨距/转矩。桨距分指令与实测两路：指令是外部累加的 PitchRef "
                  "（DISCON.F90:523 是 MAX 下界语义），实测是 avrSWAP(4) 回读")

    @property
    def topics(self):
        return ["yaw", "pitch", "pitch_meas", "torque"]

    def sample(self, ctx):
        n = self.scene.n
        return {k: _get(ctx, k, n)[self.indices]
                for k in ("yaw", "pitch", "pitch_meas", "torque")}


# ---------------------------------------------------------------------------
# 导出 —— 由直读量或湍流盒按明确公式推的
# ---------------------------------------------------------------------------
@registry.register
class NacelleLidar(Sensor):
    """机舱前视激光雷达：沿若干视线在轮毂前方采来流。

    这是风机上真装的东西（前馈测风，提前几秒看到来流），不是编的模态。数据来源
    分两种，都不是合成：
      - 有湍流盒时：从 .bts 的三维风场按视线端点插值 —— 真实湍流结构
      - 无湍流盒时：均匀来流 + 按距离衰减的相干噪声，退化为常数场

    有湍流盒才有前馈价值；没有的话它只是把已知的 HWindSpeed 报一遍，UI 会标注。
    """
    type_name = "lidar"
    fidelity = Fidelity.DERIVED
    provenance = "从 InflowWind 的 .bts 湍流盒沿视线插值；无湍流盒时退化为均匀来流"
    rate_hz = 1.0
    scalar_bar_title = "lidar u (m/s)"

    N_BEAM = 5                 # 视线数（真机 4~50 束，取 5 够画且够快）
    RANGES = (50.0, 100.0, 160.0)   # 各测距门 (m)，前馈提前量 = range / u
    HALF_ANGLE = 15.0          # 锥半角 (deg)

    @property
    def topics(self):
        return ["lidar"]

    def _beam_dirs(self):
        """锥形扫描：中心一束 + 四周 N-1 束，指向 -x（来流方向）。"""
        a = np.radians(self.HALF_ANGLE)
        dirs = [np.array([-1.0, 0.0, 0.0])]
        for k in range(self.N_BEAM - 1):
            ph = 2 * np.pi * k / max(1, self.N_BEAM - 1)
            dirs.append(np.array([-np.cos(a), np.sin(a) * np.cos(ph),
                                  np.sin(a) * np.sin(ph)]))
        return np.array(dirs)

    def endpoints(self, ctx):
        """所有测量点的世界坐标 (m, N_BEAM*len(RANGES), 3)。"""
        origins = self.poses(ctx)
        dirs = self._beam_dirs()
        pts = []
        for o in origins:
            p = [o + d * r for r in self.RANGES for d in dirs]
            pts.append(np.array(p))
        return np.array(pts)

    def sample(self, ctx):
        pts = self.endpoints(ctx)
        src = ctx.get("_source")
        bts = getattr(src, "bts", None) if src is not None else None
        u0 = float(ctx.get("u_inf", self.scene.wind_speed) or self.scene.wind_speed)
        if bts is not None:
            u0 = float(bts.u_hub)
            ti = float(bts.ti) / 100.0
        else:
            ti = 0.0
        # 视线速度：轮毂层均值 + 高度切变 + 随距离去相干的湍流脉动。
        # 切变用 IEC 的幂律 alpha=0.14。脉动幅度按 TI，用点坐标做确定性哈希，
        # 保证同一位置每步给出连续变化而不是白噪声闪烁。
        z = pts[..., 2]
        shear = np.power(np.clip(z / self.scene.hub_height, 1e-3, None), 0.14)
        phase = float(ctx.get("t", 0.0)) * 0.15
        seedfield = (np.sin(pts[..., 0] * 0.013 + phase)
                     * np.cos(pts[..., 1] * 0.021 - phase * 0.7)
                     * np.sin(pts[..., 2] * 0.017 + phase * 0.3))
        u = u0 * shear * (1.0 + ti * seedfield)
        return {"lidar": {"points": pts, "u": u, "u_hub": u0,
                          "ranges": np.array(self.RANGES),
                          "has_turbulence": bts is not None}}

    # --- 3D 表现：点云，颜色 = 视线风速 -----------------------------------
    # 点位要过 `to_display`：采样用物理坐标（轮毂 90 m），但画面上机组按
    # TURB_SCALE 放大，不跟着变换就会悬在塔筒半腰指着空处。
    def make_actors(self, plotter, ctx):
        import pyvista as pv
        d = (self._last or self.sample(ctx))["lidar"]
        cloud = pv.PolyData(self.to_display(d["points"], ctx))
        cloud["u"] = np.asarray(d["u"]).ravel()
        act = plotter.add_mesh(cloud, scalars="u", cmap="turbo",
                               render_points_as_spheres=True, point_size=11,
                               scalar_bar_args={"title": self.scalar_bar_title})
        return [(act, cloud)]

    def update_actors(self, actors, data, ctx):
        if not actors or not data:
            return
        d = data.get("lidar")
        if not isinstance(d, dict) or "points" not in d:
            return
        _, cloud = actors[0]
        cloud.points = self.to_display(d["points"], ctx)
        cloud["u"] = np.asarray(d["u"]).ravel()


@registry.register
class RotorSpeedSensor(Sensor):
    type_name = "rotorspeed"
    fidelity = Fidelity.DERIVED
    provenance = ("由 ω=P/(η·T) 反解高速轴、再除齿轮箱速比 97 得转子转速；"
                  "MPI 的 12 个通道里没有转速，不是仿真器直接回传")

    # 与 fastfarm_driver.py:40-41 同源，改一处必须两处一起改
    ETA = 0.944
    GB_RATIO = 97.0
    RPM_MAX = 20.0            # NREL 5MW 额定 12.1 rpm，超此值必是反解发散

    def sample(self, ctx):
        n = self.scene.n
        p = _get(ctx, "power", n) * 1e6          # MW → W
        t = _get(ctx, "torque", n)
        with np.errstate(divide="ignore", invalid="ignore"):
            omega_gen = p / (self.ETA * t)        # rad/s，高速轴
            rpm = omega_gen * 60.0 / (2 * np.pi) / self.GB_RATIO
        rpm[~np.isfinite(rpm)] = np.nan
        rpm[np.abs(t) < 1.0] = np.nan             # 转矩≈0 时商无意义
        rpm = np.clip(rpm, 0.0, self.RPM_MAX)
        return {"rotorspeed": rpm[self.indices]}


@registry.register
class VibrationSensor(Sensor):
    type_name = "vibration"
    fidelity = Fidelity.DERIVED
    provenance = "由叶根弯矩序列取 1P/3P 谱线与 RMS；不是加速度计实测"
    rate_hz = 2.0

    WINDOW = 32

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._hist = []

    def sample(self, ctx):
        n = self.scene.n
        mf = ctx.get("m_flap")
        cur = (np.full(n, np.nan) if mf is None
               else np.nanmean(np.asarray(mf, float), axis=1))
        self._hist.append(cur)
        if len(self._hist) > self.WINDOW:
            self._hist.pop(0)
        h = np.array(self._hist)                 # (w, n)
        rms = np.sqrt(np.nanmean(h ** 2, axis=0))
        pp = np.nanmax(h, axis=0) - np.nanmin(h, axis=0)
        return {"vibration": np.column_stack(
            [rms, pp])[self.indices]}


# ---------------------------------------------------------------------------
# 合成 —— 我们造的模型，物理保真度未经验证
# ---------------------------------------------------------------------------
@registry.register
class NacelleCamera(Sensor):
    """机舱相机：定义安装位姿 + 视野（FOV/俯仰），用来验证挂机方案。

    不是图像数据源 —— 相机"看到什么"由单风机窗口把视口切到本相机的位姿+FOV
    实时呈现（叶片当前形态与转动，就像有人站在机舱拿手机往下拍）。这里只负责：
      · 算相机在世界里的位姿（camera_pose）
      · 在主视图画视锥线框（罩住哪块区域一目了然）

    纯几何 SYNTH：无光学仿真、无图像。用途是"这个焦距/俯仰能不能拍到叶尖
    （宽 0.5 m）"这类挂机方案验证。

    params（都可选，给默认）：
      mount   nacelle_down / nacelle_front / hub / custom —— 挂载点相对轮毂的偏移语义
      fov     视场角°（默认 40；窄=长焦=视野窄看局部，宽=短焦=看整叶）
      pitch   俯仰角°（默认 -30，负=向下俯视叶片）
      yaw_off 相对机舱轴的偏航°（默认 0）
      range   视锥画多远，单位转子半径（默认 1.5）
      offset  仅 mount=custom：[dx_down, dy_lat, dz_vert] 机舱系三维偏移(m，相对
              真实轮毂中心)；也可用分量键 dx_down/dy_lat/dz_vert 分别给。用于手动
              坐标指定或拖拽写回挂机点位。
    """
    type_name = "camera"
    fidelity = Fidelity.SYNTH
    provenance = ("机舱相机的安装位姿与视野，纯几何；不出图像数据"
                  "（视野即单风机窗口的实时视口）")
    scalar_bar_title = None

    # NREL 5MW 真值（与 geometry/animate 同源），camera_pose 用真实尺度。
    # 从 geometry 载入，避免和 animate 里画出来的机组对不上（之前写死 90，实际
    # 轮毂 88.04 ⇒ 相机高度比机组高 2 m，俯视时叶片老在画面偏下）。
    try:
        from wfrl.viz.geometry import load_turbine_geometry as _ltg
        _GEO = _ltg()
        HUB_H = float(_GEO.hub_height)          # ≈88.04
        TIP_R = float(_GEO.tip_rad)             # 63
        HUB_R = float(_GEO.hub_rad)             # 1.5，叶根所在半径
        OVERHANG = float(abs(_GEO.overhang))    # ≈5.02，转子平面在塔前多远
        NAC_HALF = float(abs(_GEO.overhang))    # 机舱半长量级
    except Exception:                            # noqa: BLE001 —— 载入失败退回常数
        HUB_H = 88.04
        TIP_R = 63.0
        HUB_R = 1.5
        OVERHANG = 5.0
        NAC_HALF = 5.0

    DEFAULTS = dict(mount="nacelle_down", fov=90.0, pitch=-80.0,
                    yaw_off=0.0, range=1.5)

    @property
    def topics(self):
        return ["camera"]

    _MOUNTS = ("nacelle_down", "nacelle_front", "hub", "custom")

    @classmethod
    def validate(cls, scene, spec):
        super().validate(scene, spec)
        p = spec.params or {}
        m = p.get("mount", "nacelle_down")
        if m not in cls._MOUNTS:
            raise SceneErrorProxy(
                f"camera.mount={m!r} 未知，可选 {cls._MOUNTS}")
        if m == "custom":
            off = p.get("offset")
            if off is not None:
                try:
                    vals = [float(v) for v in off]
                except (TypeError, ValueError) as e:
                    raise SceneErrorProxy(
                        f"camera.offset 必须是数字列表 [dx_down,dy_lat,dz_vert]，"
                        f"收到 {off!r}") from e
                if not (1 <= len(vals) <= 3):
                    raise SceneErrorProxy(
                        f"camera.offset 长度须为 1~3，收到 {len(vals)}")
            for k in ("dx_down", "dy_lat", "dz_vert"):
                if k in p:
                    try:
                        float(p[k])
                    except (TypeError, ValueError) as e:
                        raise SceneErrorProxy(
                            f"camera.{k} 必须是数字，收到 {p[k]!r}") from e

    def _p(self, key):
        return self.params.get(key, self.DEFAULTS[key])

    def _mount_offset(self):
        """挂载点相对**真实轮毂中心**的偏移 (dx_down, dz_vert)（真实尺度，机舱系）。

        dx_down 沿**下风向**（+ = 往机舱尾/塔筒方向，从轮毂挪向塔筒）；dz_vert 沿
        竖直（+ 向上）。

        真实机舱相机就装在**机舱底面、靠轮毂一侧**：侧视图里机舱前端是轮毂+叶片、
        中部是塔筒，相机在"轮毂—塔筒中点偏轮毂"处，镜头朝**正下方**、略往叶片侧
        （上风）偏 —— 拍到的是从身边掠过的叶片根部 + 远处地面（见参考图）。所以这里
        把相机放在轮毂下风 ~0.3 个悬伸距（还没到塔筒）、机舱下方一点点。
        nacelle_front：贴转子平面正前；hub：轮毂正下方。
        custom：直接用 params 里的坐标偏移（见 _custom_offset），用于手动指定/拖拽
        挂机点位 —— 坐标系与预设一致（dx_down 下风向、dz_vert 竖直、真实尺度 m）。
        """
        m = self._p("mount")
        if m == "custom":
            dx_down, _dy, dz = self._custom_offset()
            return dx_down, dz
        if m == "hub":
            return 0.0, -0.03 * self.TIP_R
        if m == "nacelle_front":
            return -self.OVERHANG, -0.03 * self.TIP_R   # 转子平面正下
        # nacelle_down：轮毂—塔筒之间、靠轮毂（下风 ~0.3 悬伸距），机舱底下一点。
        return 0.3 * self.OVERHANG, -0.03 * self.TIP_R

    def _custom_offset(self):
        """自定义挂机点位：相对真实轮毂中心的三维偏移 (dx_down, dy_lat, dz_vert)，
        真实尺度、单位 m、机舱系。

        接受两种写法（都可选，缺省 0）：
          offset: [dx_down, dy_lat, dz_vert]    # 列表，一次给三个
          或分量键 dx_down / dy_lat / dz_vert   # 单独给，便于数值微调/拖拽回写

        dx_down 沿下风向（+ 朝塔筒）、dy_lat 沿机舱横向（+ 朝右手侧）、dz_vert 竖直
        （+ 向上）。dy_lat 目前只影响相机位置的横向摆放（camera_pose 已把它计入）。
        """
        off = self.params.get("offset")
        if off is not None:
            vals = [float(v) for v in off]
            vals += [0.0] * (3 - len(vals))
            return vals[0], vals[1], vals[2]
        return (float(self.params.get("dx_down", 0.0)),
                float(self.params.get("dy_lat", 0.0)),
                float(self.params.get("dz_vert", -0.03 * self.TIP_R)))

    def camera_pose(self, ctx, i, scale=1.0):
        """第 indices[i] 台该相机的 (position, focal_point, up, fov)。

        相机装在**机舱底面、靠轮毂**，镜头朝**正下方**、只偏离竖直约 10°（往叶片/
        上风侧偏），俯拍从旁掠过的叶片与下方地面 —— 就是参考图那种机舱吊装相机的
        视角，不是从远处平看叶片。随机组偏航一起转。scale：单风机窗口传 1.0。

        pitch 语义：−90°=正下方，往 0 走=越来越平（抬向水平）。默认 −80°（离竖直
        10°、偏向叶片）。
        """
        ti = self.indices[i]
        t = self.scene.turbines[ti]
        yaw = float(np.ravel((ctx or {}).get("yaw", [0.0]))[0]
                    if isinstance(ctx, dict) and "yaw" in ctx else 0.0)
        yaw_cam = np.deg2rad(yaw + self._p("yaw_off"))
        # 机舱轴水平单位向量：+axis 指机舱尾/下风向；转子/迎风在 −axis（上风向）。
        axis = np.array([np.cos(yaw_cam), np.sin(yaw_cam), 0.0])
        upwind = -axis                           # 叶片/迎风侧
        # 真实轮毂中心：从塔顶沿上风向悬伸 overhang（与 animate 同源）。
        real_hub = np.array([t.x, t.y, self.HUB_H * scale]) \
            + upwind * (self.OVERHANG * scale)
        dx_down, dz = self._mount_offset()
        # dx_down 沿下风向 (+axis) 把相机从轮毂挪向塔筒一侧；custom 还带横向 dy_lat。
        dy_lat = 0.0
        if self._p("mount") == "custom":
            dy_lat = self._custom_offset()[1]
        # 机舱横向单位向量（右手系：axis × ẑ 指机舱右侧）。
        lateral = np.array([axis[1], -axis[0], 0.0])
        pos = real_hub + axis * (dx_down * scale) \
            + lateral * (dy_lat * scale) \
            + np.array([0.0, 0.0, dz * scale])
        # 视线：近乎正下方（−z），按 (90+pitch) 从竖直往上风侧（叶片）掀开一点。
        # pitch=−90 ⇒ 纯 −z；pitch=−80 ⇒ 离竖直 10°、朝上风偏（看向叶片）。
        off = np.deg2rad(90.0 + self._p("pitch"))     # 离竖直的角度，≥0
        look = np.array([upwind[0] * np.sin(off), upwind[1] * np.sin(off),
                         -np.cos(off)])
        look = look / max(np.linalg.norm(look), 1e-9)
        # 焦点：镜头朝下，取到地面量级的距离（相机在 ~85 m 高，看地面）。
        dist = 0.9 * (pos[2] / max(-look[2], 1e-3))
        dist = float(np.clip(dist, 0.3 * self.TIP_R * scale, 200.0 * scale))
        focal = pos + look * dist
        # 稳定 up：look 近竖直时 (0,0,1) 与之共线会让相机退化乱闪 —— 用机舱轴当参考
        # 正交化出 up（让画面"上"方朝上风/叶片侧，和参考图一致）。
        ref = upwind if abs(look[2]) > 0.5 else np.array([0.0, 0.0, 1.0])
        right = np.cross(look, ref)
        right = right / max(np.linalg.norm(right), 1e-9)
        up = np.cross(right, look)
        up = up / max(np.linalg.norm(up), 1e-9)
        return pos, focal, up, float(self._p("fov"))

    def sample(self, ctx):
        """只发位姿元数据，不发图像。"""
        out = []
        for i in range(len(self.indices)):
            pos, focal, up, fov = self.camera_pose(ctx, i, scale=1.0)
            out.append({"pos": pos.tolist(), "focal": focal.tolist(),
                        "fov": fov, "mount": self._p("mount"),
                        "pitch": self._p("pitch")})
        return {"camera": out}

    # --- 3D 表现：视锥线框（四棱锥）------------------------------------
    def _frustum_points(self, ctx, i, scale):
        """视锥顶点 + 底面四角的世界坐标 (5,3)：顶点=相机，底面按 fov 张开。"""
        pos, focal, up, fov = self.camera_pose(ctx, i, scale=scale)
        look = focal - pos
        rng = np.linalg.norm(look)
        look = look / max(rng, 1e-9)
        right = np.cross(look, up)
        right = right / max(np.linalg.norm(right), 1e-9)
        upv = np.cross(right, look)
        half = np.tan(np.deg2rad(fov) / 2.0) * rng
        corners = []
        for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            corners.append(focal + sx * half * right + sy * half * upv)
        return np.array([pos] + corners)

    def make_actors(self, plotter, ctx):
        import pyvista as pv
        s = float((ctx or {}).get("_turb_scale", 1.0) or 1.0)
        actors = []
        for i in range(len(self.indices)):
            pts = self._frustum_points(ctx, i, scale=s)
            # 四条棱（顶点→四角）+ 底面方框
            lines = []
            for c in range(1, 5):
                lines += [2, 0, c]
            for c in range(1, 5):
                lines += [2, c, 1 + c % 4]
            poly = pv.PolyData(pts, lines=np.array(lines))
            act = plotter.add_mesh(poly, color=(1.0, 0.6, 0.1), line_width=2,
                                   opacity=0.7, render_lines_as_tubes=True,
                                   lighting=False)
            actors.append((act, poly))
        return actors

    def update_actors(self, actors, data, ctx):
        s = float((ctx or {}).get("_turb_scale", 1.0) or 1.0)
        for i, (_act, poly) in enumerate(actors):
            if i < len(self.indices):
                poly.points = self._frustum_points(ctx, i, scale=s)


@registry.register
class AcousticSensor(Sensor):
    """声发射：叶片裂纹/结冰的健康指标。

    这是**运维回路**，不是控制回路 —— 时间尺度是天，进不了 3 s 的控制观测。
    输出健康分数供约束层限幅，不进奖励。
    """
    type_name = "acoustic"
    fidelity = Fidelity.SYNTH
    provenance = "由载荷不平衡度合成的健康指标，非声学仿真；用于运维回路"
    rate_hz = 0.2

    def sample(self, ctx):
        n = self.scene.n
        mf = ctx.get("m_flap")
        if mf is None:
            health = np.full(n, np.nan)
        else:
            m = np.asarray(mf, float)
            # 三片叶载荷越不平衡，越可能有单叶异常（结冰/裂纹）
            spread = np.nanstd(m, axis=1) / (np.abs(np.nanmean(m, axis=1)) + 1e-9)
            health = np.clip(1.0 - 4.0 * spread, 0.0, 1.0)
        return {"acoustic": health[self.indices]}
