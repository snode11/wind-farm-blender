"""
真实 NREL 5MW 机组几何 —— 从 OpenFAST 输入文件读，而不是拍脑袋的柱体近似。

之前 viz 里塔筒/叶片是三根 `pv.Cylinder`，尺寸是硬编常量（TOWER_H=90、
BLADE_LEN=57、overhang=6），和算例实际跑的机组对不上。而这些参数本来就躺在
FAST.Farm 的模板输入文件里：

  AeroDyn blade (.dat)     19 个截面的 展长/预弯/后掠/扭角/弦长/翼型ID
  Airfoils/*_coords.txt    8 个翼型各 399 个 x/c-y/c 坐标点
  AD.dat                   12 站塔筒锥度 (TwrElev/TwrDiam)、翼型文件名表
  ElastoDyn (.dat)         TipRad/HubRad/PreCone/OverHang/ShftTilt/TowerHt

  Blade (.dat)             一阶挥舞/摆振模态振型多项式（BldFl1Sh/BldEdgSh）

本模块把它们解析出来放样成网格，并按实时根部弯矩做**柔性变形**：设计态放样
给出 base 网格，`blade_deform()` 把叶尖挠度按模态振型摊到整个展向。挠度由
MPI 实时回传的 RootMyc/RootMxc 经标定系数换得（见 CALIB 常量）。

用法：
    geo = load_turbine_geometry()          # 默认读 fastfarm 模板
    blade = geo.blade_surface()            # pv.StructuredGrid，单叶片（局部坐标系）
    xi = blade.point_data["xi"]            # 归一化展向，变形要用
    pts = geo.blade_deform(blade.points.copy(), xi, m_flap=5.9e6, m_edge=-4.2e6)
    tower = geo.tower_surface()            # pv.StructuredGrid
"""
import os
import re
from dataclasses import dataclass, field

import numpy as np
import pyvista as pv


# --- 模板定位 ---------------------------------------------------------------
def _template_dir():
    """FAST.Farm 模板目录。从 wfcrl 包里问，别硬编仓库路径。"""
    try:
        from wfcrl.simul_utils import TEMPLATE_DIR
        return TEMPLATE_DIR.format("fastfarm")
    except Exception:                                          # noqa: BLE001
        # wfcrl 装不上时（比如只想画图的环境）退回相对仓库根的位置
        from wfrl import paths
        return os.path.join(paths.ROOT, "wfcrl-env", "wfcrl", "simulators",
                            "fastfarm", "inputs", "template") + os.sep


# --- 底层解析 ---------------------------------------------------------------
# 这些 .dat 是 Fortran 固定格式的文本，但夹杂 latin-1 字节（Read 工具会当成
# 二进制），统一用 latin-1 读，解析只认数字，不依赖列宽。
def _lines(path):
    with open(path, "r", encoding="latin-1") as f:
        return f.read().splitlines()


_NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[EeDd][-+]?\d+)?")

# 根部弯矩 → 叶尖挠度的仿射标定：δ[m] = k·M[N·m] + b
# 出处：scripts/experiments/calib_blade_flex.py 在 8 m/s、3T 算例上实测 2487 个
# 样本（RootMyc1..3 vs OoPDefl1..3 / RootMxc vs IPDefl），落盘于
# results/runs/blade_flex/calib.json。
#   flap  δ = 7.4296e-07·M − 1.0254   R²=0.949
#   edge  δ = −1.3416e-07·M − 0.2268  R²=0.995
# 截距不是拟合瑕疵：8 m/s 下推力把叶片静态压弯约 1 m，重力再给摆振一个偏置。
# 一开始按"零弯矩必须零挠度"做过原点拟合，R² 只有 0.89/0.58，被实测否掉。
CALIB = {"flap": (7.4296e-07, -1.0254), "edge": (-1.3416e-07, -0.2268)}


def _nums(line):
    """抽出一行里的所有数字（兼容 Fortran 的 D 指数写法）。"""
    return [float(t.replace("D", "E").replace("d", "e"))
            for t in _NUM.findall(line)]


def _scalar(lines, key):
    """取 `<value>  KEY  - comment` 形式的标量。找不到返回 None。"""
    # 必须是独立 token，否则 "TipRad" 会被 "BlTipRad" 之类误命中。但 key 尾字符
    # 是非单词字符时（PreCone(1) 的右括号）不能加 \b —— 后面跟空格构不成边界。
    tail = r"\b" if key[-1].isalnum() or key[-1] == "_" else ""
    pat = rf"\b{re.escape(key)}{tail}"
    for ln in lines:
        if re.search(pat, ln):
            got = _nums(ln.split(key)[0])
            if got:
                return got[0]
    return None


def _table(lines, header_key, ncols, count=None):
    """定位含 header_key 的表头行，往下收集每行至少 ncols 个数字的连续块。

    OpenFAST 的表格是 `表头行 / 单位行 / 数据行...`，单位行形如 "(m) (-)"，
    括号里没有数字所以会被自动跳过；遇到下一个 "======" 段或数字不够就停。
    """
    out = []
    started = False
    for ln in lines:
        if not started:
            if header_key in ln:
                started = True
            continue
        if ln.strip().startswith("=") or ln.strip().startswith("---"):
            if out:
                break
            continue
        got = _nums(ln)
        if len(got) >= ncols:
            out.append(got[:ncols])
            if count is not None and len(out) >= count:
                break
        elif out:
            break
    return np.asarray(out, dtype=float)


def _coords_path(polar_path):
    """由极曲线文件（Cylinder1.dat）定位外形坐标文件（Cylinder1_coords.txt）。

    AD.dat 的 AFNames 列的是**极曲线**文件，里面只有 alpha/Cl/Cd/Cm，没有外形。
    外形在它的 NumCoords 行里以 `@"xxx_coords.txt"` 的形式外链。
    """
    for ln in _lines(polar_path):
        if "NumCoords" in ln:
            m = re.search(r'@\s*"([^"]+)"', ln)
            if m:
                return os.path.join(os.path.dirname(polar_path),
                                    os.path.basename(m.group(1)))
            break
    # 没有 @ 外链就按命名约定猜
    stem = os.path.splitext(polar_path)[0]
    return stem + "_coords.txt"


def _resample_ring(xy, n):
    """按归一化弧长把翼型外形重采样成 n 个点。

    8 个翼型原始点数虽然都是 399，但放样成 StructuredGrid 要求每一圈点数
    严格相同；重采样顺便把「相邻截面翼型不同时逐点混合」变得有意义
    —— 混合的是弧长同位点，而不是碰巧同序号的点。
    """
    d = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(xy, axis=0), axis=1))]
    if d[-1] <= 0:
        raise ValueError("翼型外形退化成一个点")
    s = d / d[-1]
    t = np.linspace(0.0, 1.0, n)
    return np.column_stack([np.interp(t, s, xy[:, 0]),
                            np.interp(t, s, xy[:, 1])])


def _read_airfoil_coords(polar_path, npt=121):
    """读翼型外形，返回 (ref_xy, shape_xy)，shape_xy 已重采样成 npt 点。

    坐标文件格式（8 个文件完全一致，407 行）：
        第 1 行  NumCoords = 400（含 1 个参考点）
        注释行以 ! 开头
        第 1 个数值对 = 翼型参考点（变桨轴位置，Cylinder 是 0.5、翼型是 0.25）
        其后 399 个数值对 = 翼型外形，从后缘 x/c=1.0 起绕一圈回到后缘
    """
    path = _coords_path(polar_path)
    pairs = []
    for ln in _lines(path):
        s = ln.strip()
        if not s or s.startswith("!"):
            continue
        got = _nums(s)
        if len(got) == 1 and not pairs:        # NumCoords 行
            continue
        if len(got) >= 2:
            pairs.append(got[:2])
    if len(pairs) < 3:
        raise ValueError(f"翼型坐标解析失败: {path}（只读到 {len(pairs)} 个点）")
    arr = np.asarray(pairs, dtype=float)
    return arr[0], _resample_ring(arr[1:], npt)


# --- 4x4 齐次变换 -----------------------------------------------------------
# 放在这里而不是 animate.py：转子装配链（轴倾/悬伸/方位角/锥角/桨距）既要给
# 渲染器摆姿态，也要给扫塔间隙算距离。两处必须是**同一条链**，否则算出来的
# 间隙和画面上看到的不是一回事。animate.py 从这里导入。
def rot_x(deg):
    t = np.deg2rad(deg); c, s = np.cos(t), np.sin(t)
    M = np.eye(4); M[1, 1] = c; M[1, 2] = -s; M[2, 1] = s; M[2, 2] = c
    return M


def rot_y(deg):
    t = np.deg2rad(deg); c, s = np.cos(t), np.sin(t)
    M = np.eye(4); M[0, 0] = c; M[0, 2] = s; M[2, 0] = -s; M[2, 2] = c
    return M


def rot_z(deg):
    t = np.deg2rad(deg); c, s = np.cos(t), np.sin(t)
    M = np.eye(4); M[0, 0] = c; M[0, 1] = -s; M[1, 0] = s; M[1, 1] = c
    return M


def trans(v):
    M = np.eye(4); M[0, 3], M[1, 3], M[2, 3] = v
    return M


# --- 几何容器 ---------------------------------------------------------------
@dataclass
class TurbineGeometry:
    """一台机组的真实设计几何（单位 m / deg）。"""

    tip_rad: float
    hub_rad: float
    precone: float
    overhang: float
    shft_tilt: float
    tower_ht: float
    # 叶片 19 个截面
    bl_spn: np.ndarray
    bl_crv_ac: np.ndarray            # 预弯（out-of-plane）
    bl_swp_ac: np.ndarray            # 后掠（in-plane）
    bl_twist: np.ndarray             # deg
    bl_chord: np.ndarray
    bl_afid: np.ndarray              # 1-based 翼型索引
    airfoils: list = field(default_factory=list)   # [(ref_xy, shape_xy), ...]
    # 塔筒 12 站
    twr_elev: np.ndarray = None
    twr_diam: np.ndarray = None
    # 一阶模态振型多项式系数，x^2..x^6（ElastoDyn 约定：没有 x^0/x^1 项，
    # 因为根部固支要求 φ(0)=φ'(0)=0）
    mode_flap1: np.ndarray = None
    mode_edge1: np.ndarray = None
    twr2shft: float = 0.0

    # ------------------------------------------------------------------
    @property
    def hub_height(self):
        """轮毂中心高度。塔顶 + 主轴高差 + 轴倾造成的抬升（OverHang 为负=上风向）。"""
        return self.tower_ht + self.twr2shft + abs(self.overhang) * np.sin(
            np.deg2rad(abs(self.shft_tilt)))

    @property
    def blade_len(self):
        return float(self.tip_rad - self.hub_rad)

    # ------------------------------------------------------------------
    def _section(self, i):
        """第 i 个截面的翼型外形，已按参考点对齐、按弦长缩放、按扭角旋转。

        返回 (n, 2) 数组：列 0 = 弦向（切向），列 1 = 厚度向（轴向）。
        """
        ref, shape = self.airfoils[int(self.bl_afid[i]) - 1]
        c = self.bl_chord[i]
        beta = np.deg2rad(self.bl_twist[i])
        # 各截面绕自己的参考点（变桨轴）对齐，否则叶片会沿弦向错开
        u = (shape[:, 0] - ref[0]) * c
        v = (shape[:, 1] - ref[1]) * c
        # 绕展向轴转扭角：根部 13.3° → 叶尖 0.1°
        tan = u * np.cos(beta) + v * np.sin(beta)
        axi = -u * np.sin(beta) + v * np.cos(beta)
        return np.column_stack([tan, axi])

    def blade_surface(self, subdiv=4):
        """单叶片放样面（局部坐标：x=轴向/推力, y=切向, z=展向）。

        subdiv: 相邻截面间插入的细分数。19 站直接连会看出折面，插值到
        ~70 站后轮廓平滑。相邻截面翼型不同时按点序线性混合 —— 8 个翼型
        都是 399 点、同样从后缘起绕，逐点混合几何上讲得通。
        """
        n_st = len(self.bl_spn)
        rings, spans = [], []
        for i in range(n_st - 1):
            s0, s1 = self._section(i), self._section(i + 1)
            k = subdiv if i < n_st - 2 else subdiv + 1   # 最后一段带上端点
            for j in range(k):
                t = j / subdiv
                rings.append((1 - t) * s0 + t * s1)
                spans.append((1 - t) * self.bl_spn[i] + t * self.bl_spn[i + 1])
        # 预弯/后掠同样插值到细分后的展向位置
        crv = np.interp(spans, self.bl_spn, self.bl_crv_ac)
        swp = np.interp(spans, self.bl_spn, self.bl_swp_ac)

        npt = rings[0].shape[0]
        nsp = len(rings)
        pts = np.zeros((nsp, npt, 3))
        for i, ring in enumerate(rings):
            # 展向 = z；根部从 hub_rad 起算，叶尖到 tip_rad
            pts[i, :, 2] = self.hub_rad + spans[i]
            pts[i, :, 1] = swp[i] + ring[:, 0]      # 切向 = 后掠 + 弦向
            pts[i, :, 0] = crv[i] + ring[:, 1]      # 轴向 = 预弯 + 厚度
        grid = pv.StructuredGrid()
        grid.points = pts.reshape(-1, 3)
        grid.dimensions = [npt, nsp, 1]
        # 归一化展向，逐点存下来供 blade_deform 用。ξ 以**展长**归一（不含
        # hub_rad）：振型多项式的定义域是叶片本体，根部固支点在 BlSpn=0。
        span_len = float(self.bl_spn[-1]) or 1.0
        grid.point_data["xi"] = np.repeat(
            np.asarray(spans) / span_len, npt).astype(float)
        return grid

    # ------------------------------------------------------------------
    def mode_shape(self, coeffs, xi):
        """振型 φ(ξ)，ξ = 归一化展向 ∈[0,1]。ElastoDyn 归一化成 φ(1)=1。

        系数和为 1 已核（flap1: 0.0622+1.7254−3.2452+4.7131−2.2555 = 1.0000），
        所以模态坐标就等于叶尖挠度本身，不需要额外的归一化因子。
        """
        xi = np.clip(np.asarray(xi, dtype=float), 0.0, 1.0)
        return sum(c * xi ** (p + 2) for p, c in enumerate(coeffs))

    def blade_deform(self, base_pts, xi, m_flap=None, m_edge=None,
                     tip_flap=None, tip_edge=None, scale=1.0):
        """按根部弯矩把设计态叶片弯成实时形态，返回变形后的点集 (N,3)。

        给 m_flap/m_edge（N·m，MPI 实时回传的 RootMyc/RootMxc）时走 CALIB
        换成叶尖挠度；也可以直接给 tip_flap/tip_edge（m）绕过标定。
        scale 只用于演示放大，物理分析时保持 1.0。

        近似有两处，都是二阶小量：
          - 只用一阶振型。标定是拿总叶尖挠度回归的，二阶挥舞的贡献已经吸收进
            系数里，但沿展向的分布形状按一阶算，中段会有百分级偏差。
          - 不做弧长守恒。叶尖挠度 3.4 m / 展长 63 m ⇒ 展向缩短约 0.1 m，
            比翼型厚度还小，看不出来。
        """
        if tip_flap is None:
            kf, bf = CALIB["flap"]
            tip_flap = kf * float(m_flap) + bf if m_flap is not None else 0.0
        if tip_edge is None:
            ke, be = CALIB["edge"]
            tip_edge = ke * float(m_edge) + be if m_edge is not None else 0.0
        out = np.asarray(base_pts, dtype=float).copy()
        # 局部坐标：x=轴向(挥舞)、y=切向(摆振)、z=展向
        out[:, 0] += scale * tip_flap * self.mode_shape(self.mode_flap1, xi)
        out[:, 1] += scale * tip_edge * self.mode_shape(self.mode_edge1, xi)
        return out

    # --- 扫塔间隙 ------------------------------------------------------
    def rotor_assembly(self, azimuth=0.0, pitch=0.0):
        """叶片局部系 → 机组局部系（yaw=0、塔轴在 x=y=0）的 4x4。

        链条：轴倾 ∘ 悬伸 ∘ 方位角 ∘ 锥角 ∘ 桨距，和 animate.update_turbine
        里逐叶片摆的那条完全一致（那里多乘一个绕塔轴的 yaw，对塔的相对位置
        没有影响，所以间隙与 yaw 无关）。azimuth=180° 时叶片指向正下方，
        也就是掠过塔筒的位置。
        """
        top = np.array([0.0, 0.0, self.tower_ht + self.twr2shft])
        hub_flat = top + np.array([self.overhang, 0.0, 0.0])
        m_tilt = trans(top) @ rot_y(-self.shft_tilt) @ trans(-top)
        m_hub = m_tilt @ trans(hub_flat)
        return m_hub @ rot_x(azimuth) @ rot_y(self.precone) @ rot_z(pitch)

    def tower_radius(self, z):
        """塔筒外半径（m）。z 落在塔外（高过塔顶）返回 nan —— 那里没有塔。"""
        z = np.asarray(z, dtype=float)
        r = np.interp(z, self.twr_elev, self.twr_diam / 2.0)
        return np.where((z >= self.twr_elev[0]) & (z <= self.twr_elev[-1]),
                        r, np.nan)

    def tower_clearance(self, base_pts, xi, azimuth=None, pitch=0.0,
                        xi_min=0.25, **deform):
        """叶片掠塔时的最小间隙（m），负值 = 已经打到塔筒。

        `base_pts`/`xi` 是设计态叶片网格（`blade_surface()` 的输出，**未缩放**），
        `**deform` 直接转给 `blade_deform`（m_flap/m_edge/tip_flap/tip_edge/scale）。
        默认在 azimuth 150°~210° 上采 9 个角度 —— 叶片正对塔筒的那一段。

        `xi_min=0.25`：只统计 25% 展长以外的部分。根部那截圆柱（弦长 3.54 m）
        贴着塔顶，固定间隙 1.29 m，是**设计常量**、不随载荷变；混进来会恒定压住
        最小值，把真正随载荷退化的叶尖间隙淹掉（实测过：不设下限时 0~9 MN·m
        的间隙一律显示 1.506 m）。

        两个限制说清楚：塔筒当刚性直立处理（本算例 TwFADOF/TwSSDOF 都是 False，
        塔本来就不弹），且只用一阶振型摊挠度，所以间隙是**指示性**的，不能当
        认证用的净空校核。真正判扫塔要看 OpenFAST 自己的 TipClrnc 输出。
        """
        pts = self.blade_deform(base_pts, xi, **deform)
        keep = np.asarray(xi, dtype=float) >= xi_min
        if np.any(keep):
            pts = pts[keep]
        h = np.column_stack([pts, np.ones(len(pts))])
        az = np.linspace(150.0, 210.0, 9) if azimuth is None \
            else np.atleast_1d(azimuth)
        best = np.inf
        for a in az:
            q = (self.rotor_assembly(a, pitch) @ h.T).T[:, :3]
            d = np.hypot(q[:, 0], q[:, 1]) - self.tower_radius(q[:, 2])
            ok = np.isfinite(d)
            if np.any(ok):
                best = min(best, float(d[ok].min()))
        return best

    def safe_flex_scale(self, base_pts, xi, scale, margin=0.3, hint=None,
                        **deform):
        """把变形的**显示放大倍数**压到叶片不穿塔为止，返回可用倍数。

        为什么需要：`--flex-scale 5` 是为了在整场尺度下看得见变形，但它只放大
        挠度、不放大塔筒，5× 之后画面上叶片会直接穿过塔筒 —— 物理上（1×）间隙
        还有 4.4 m。穿模会让人误判成扫塔，所以宁可少放大一点。

        间隙对 scale 单调递减但**不是线性**的（小挠度时最小值在 ξ≈0.3 处、与
        叶尖挠度基本无关；挠度大到叶尖成为最近点后才近似 1:1 下降），所以两点
        线性外推会给错答案，这里直接二分。

        `hint` 给上一帧用过的倍数：弯矩逐步只变百分之几，上一帧的答案基本还成立，
        试它一次（1 次 clearance）就能省掉 7 次二分。过不了再老实二分。
        """
        if scale <= 1.0:
            return float(scale)
        if self.tower_clearance(base_pts, xi, scale=scale, **deform) >= margin:
            return float(scale)                     # 常见情况：一次就过
        if hint is not None and 0.0 < hint < scale and self.tower_clearance(
                base_pts, xi, scale=hint, **deform) >= margin:
            return float(hint)
        lo, hi = 0.0, float(scale)
        for _ in range(7):          # 5/2^7≈0.04 的分辨率，肉眼分不出；再多就是白烧帧时间
            mid = 0.5 * (lo + hi)
            if self.tower_clearance(base_pts, xi, scale=mid, **deform) >= margin:
                lo = mid
            else:
                hi = mid
        return lo

    def tower_surface(self, resolution=32):
        """塔筒放样面（真实锥度：底 6.0 m → 顶 3.87 m）。"""
        th = np.linspace(0, 2 * np.pi, resolution, endpoint=True)
        z = self.twr_elev
        r = self.twr_diam / 2.0
        pts = np.zeros((len(z), resolution, 3))
        pts[:, :, 0] = (r[:, None] * np.cos(th)[None, :])
        pts[:, :, 1] = (r[:, None] * np.sin(th)[None, :])
        pts[:, :, 2] = z[:, None]
        grid = pv.StructuredGrid()
        grid.points = pts.reshape(-1, 3)
        grid.dimensions = [resolution, len(z), 1]
        return grid


# --- 顶层加载 ---------------------------------------------------------------
def load_turbine_geometry(template_dir=None):
    """解析模板输入文件，返回 TurbineGeometry。解析不出关键量就抛异常。"""
    tdir = template_dir or _template_dir()
    farm = os.path.join(tdir, "FarmInputs")
    base = os.path.join(tdir, "5MW_Baseline")

    ed = _lines(os.path.join(
        farm, "NRELOffshrBsline5MW_Onshore_ElastoDyn_8mps.dat"))
    ad = _lines(os.path.join(base, "AD.dat"))
    bl = _lines(os.path.join(base, "NRELOffshrBsline5MW_AeroDyn_blade.dat"))

    # 叶片截面表：BlSpn BlCrvAC BlSwpAC BlCrvAng BlTwist BlChord BlAFID (+3 列)
    nbl = int(_scalar(bl, "NumBlNds"))
    tab = _table(bl, "BlSpn", 7, count=nbl)
    if len(tab) < nbl:
        raise ValueError(f"叶片截面表只解析出 {len(tab)}/{nbl} 行")

    # 翼型坐标，按 AD.dat 里 AFNames 的顺序（BlAFID 是这个表的 1-based 索引）
    af_names = [m.group(1) for ln in ad
                for m in [re.search(r'"([^"]*Airfoils/[^"]+)"', ln)] if m]
    airfoils = []
    for name in af_names:
        p = os.path.join(base, "Airfoils", os.path.basename(name))
        airfoils.append(_read_airfoil_coords(p))
    if not airfoils:
        raise ValueError("AD.dat 里没解析到翼型文件名")

    twr = _table(ad, "TwrElev", 2, count=int(_scalar(ad, "NumTwrNds")))

    # 一阶挥舞/摆振振型多项式（x^2..x^6）。ElastoDyn 自己解气弹用的就是这几行，
    # 拿它做渲染变形，形状与物理侧同源，不是另找一条经验曲线。
    bd = _lines(os.path.join(base, "NRELOffshrBsline5MW_Blade.dat"))
    def _mode(prefix):
        c = [_scalar(bd, f"{prefix}({p})") for p in range(2, 7)]
        if any(v is None for v in c):
            raise ValueError(f"{prefix} 振型系数没解析全: {c}")
        s = sum(c)
        if abs(s - 1.0) > 1e-3:                     # 归一化前提，塌了就别默默用
            raise ValueError(f"{prefix} 系数和={s:.4f}，非 φ(1)=1 归一化")
        return np.asarray(c, dtype=float)

    return TurbineGeometry(
        tip_rad=_scalar(ed, "TipRad"),
        hub_rad=_scalar(ed, "HubRad"),
        precone=_scalar(ed, "PreCone(1)"),
        overhang=_scalar(ed, "OverHang"),
        shft_tilt=_scalar(ed, "ShftTilt"),
        tower_ht=_scalar(ed, "TowerHt"),
        twr2shft=_scalar(ed, "Twr2Shft"),
        bl_spn=tab[:, 0], bl_crv_ac=tab[:, 1], bl_swp_ac=tab[:, 2],
        bl_twist=tab[:, 4], bl_chord=tab[:, 5], bl_afid=tab[:, 6],
        airfoils=airfoils,
        twr_elev=twr[:, 0], twr_diam=twr[:, 1],
        mode_flap1=_mode("BldFl1Sh"), mode_edge1=_mode("BldEdgSh"),
    )
