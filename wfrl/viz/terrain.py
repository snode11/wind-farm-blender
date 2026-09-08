"""场景地形（山地 / 戈壁）—— **纯装饰，不参与流场计算**。

这一层存在的理由只有一个：让画面像真实风场，而不是三根杆子插在白板上。
它对物理的影响是**零**：

  - FAST.Farm 本算例的地面是平的（低分辨率域 z 从 0 起、无地形输入文件），
    机组轮毂高度是**绝对**高度 90 m，不是"离地 90 m"；
  - 尾流切面 DisXY 由 FAST.Farm 在平地上解出，画在 z=90 的水平面上；
  - 所以地形若抬高机组底座、或戳穿尾流切面，画面就在暗示一个仿真里不存在的
    地形效应。两件事都在下面显式挡掉（`_pad_mask` / `HEIGHT_FRAC`）。

真要做地形效应，得给 FAST.Farm 喂地形化的 InflowWind/TurbSim 盒子并开
复杂地形模块，那是另一件事。界面上必须写明"装饰"，见 `LABEL`。

用法：
    from wfrl.viz import terrain
    mesh = terrain.build(lx, ly, kind="mountains", z_wake=90.0)
    terrain.add(plotter, mesh, kind="mountains")
"""
import numpy as np
import pyvista as pv

# 尾流切面覆盖范围**之内**，地形最高点不得超过切面高度的这个比例 —— 超了就会
# 从切面里冒出来，看着像"山把尾流截断了"，而仿真里根本没有山。0.45 留足视觉余量。
# 切面**之外**不限高：远景山脊才是"像真实风场"的来源，而那里没有切面可穿。
# 只在切面内限高，是这个模块里唯一有物理含义的取舍。
HEIGHT_FRAC = 0.45

# 视作"切面覆盖范围"的半径 (m)，从机位包围盒中心量起。DisXY 低分辨率域实测
# x 0→1400、y ±150 左右（3T 布局），取 1400 把整张切面裹进去还有余量。
# 真实域范围写在 VTK 文件头里、建场景时未必已知，所以这里给个保守常量。
CORE_R = 1400.0

# 机位周围压平成台地的半径 (m)：塔基必须落在 z=0，否则塔筒会悬空或埋进土里。
# 取 1.5 倍转子半径（63 m）—— 恰好覆盖叶片扫掠圆的地面投影。
PAD_R = 95.0

LABEL = "地形为装饰性渲染，不参与流场计算（FAST.Farm 本算例为平地）"

# VTK 自带字体**没有中文字形**，且缺字时是静默丢弃而不是画方框 —— 上面这句
# 会只剩下 ASCII 部分渲染成 "FAST.Farm"，把"装饰"的声明反读成"这是 FAST.Farm 的
# 地形"，比不写还糟。所以必须显式给一个带中文的字体文件。
_CJK_FONTS = (r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\msyh.ttc")
# 一个中文字体都找不到时退回纯 ASCII，宁可英文也不能只剩半句。
LABEL_ASCII = ("Terrain is decorative only - it does NOT affect the flow "
               "(flat ground in this FAST.Farm case)")


def label_font():
    """返回第一个存在的中文字体路径；都没有则 None。

    公开的原因：VTK 缺中文字形会**静默丢字**，凡是往场景里写中文的地方都要它，
    不只是地形声明（视图标题同理）。
    """
    import os
    for f in _CJK_FONTS:
        if os.path.exists(f):
            return f
    return None

# 每种地形的 (起伏幅度 m, 最粗糙波长 m, 倍频数, 色带)。
# 山地：大幅度、长波长、多倍频 ⇒ 连绵山脊。
# 戈壁：小幅度、短波长 ⇒ 一片微起伏的砾石滩，视觉上几乎是平的但不死板。
#
# 色带不能用 `gist_earth`/`terrain` 这类"地理"色带：它们低端是水蓝，而本模块
# 把机位周围压成了 h=0 的台地 ⇒ 整片核心区会被涂成湖，画面在暗示海上风场。
# 改用单调的土色渐变（自定义 list，低端也是土），高度为 0 也仍然是地面。
PRESETS = {
    # relief 是**远景**幅度，核心区另有 HEIGHT_FRAC 限高把它压到切面之下。
    # 260 m 在 7.4 km 跨度上只有 3.5% 坡度，渲染出来是块平板；山地风场的山脊
    # 本来就是几百米量级，取 700 才看得出是山。核心区不受影响。
    "mountains": dict(relief=700.0, wavelength=2600.0, octaves=4,
                      cmap=["#6b7d5a", "#8a9166", "#a89c78", "#c4b79a",
                            "#e8e2d5"]),
    "gobi": dict(relief=22.0, wavelength=900.0, octaves=3,
                 cmap=["#a8926e", "#bda07a", "#cdb28c", "#dcc7a4"]),
    "flat": dict(relief=0.0, wavelength=1.0, octaves=1,
                 cmap=["#b8ae9c", "#b8ae9c"]),
}


def _lattice_noise(x, y, wavelength, seed):
    """一层格点噪声，双线性 + smoothstep 插值。

    自己写而不是拉 noise/scipy：仓库现在只依赖 numpy/pyvista，为了背景贴图
    加个依赖不值。smoothstep 是必须的 —— 纯双线性会留下明显的格点棱，
    山脊上看得出方格。
    """
    rng = np.random.default_rng(seed)
    gx = np.arange(x.min() - wavelength, x.max() + 2 * wavelength, wavelength)
    gy = np.arange(y.min() - wavelength, y.max() + 2 * wavelength, wavelength)
    g = rng.random((gx.size, gy.size))

    def frac(v, grid):
        i = np.clip(np.searchsorted(grid, v) - 1, 0, grid.size - 2)
        t = (v - grid[i]) / (grid[i + 1] - grid[i])
        return i, t * t * (3.0 - 2.0 * t)          # smoothstep

    ix, tx = frac(x, gx)
    iy, ty = frac(y, gy)
    IX, IY = np.meshgrid(ix, iy, indexing="ij")
    TX, TY = np.meshgrid(tx, ty, indexing="ij")
    c00, c10 = g[IX, IY], g[IX + 1, IY]
    c01, c11 = g[IX, IY + 1], g[IX + 1, IY + 1]
    return ((c00 * (1 - TX) + c10 * TX) * (1 - TY)
            + (c01 * (1 - TX) + c11 * TX) * TY)


def _pad_mask(X, Y, lx, ly):
    """机位台地遮罩：塔基处 0、离开 3·PAD_R 后 1，中间 smoothstep 过渡。

    不压平的话塔筒底端会落在山坡上 —— 悬空或半埋，两种都是错的，因为
    仿真里塔基就在 z=0。过渡带做宽（3×）是为了不出现一圈突兀的圆台阶。
    """
    m = np.ones_like(X)
    for x0, y0 in zip(lx, ly):
        r = np.hypot(X - x0, Y - y0)
        t = np.clip((r - PAD_R) / (2.0 * PAD_R), 0.0, 1.0)
        m = np.minimum(m, t * t * (3.0 - 2.0 * t))
    return m


def height_field(X, Y, lx, ly, kind="mountains", seed=7, z_wake=90.0,
                 h_far=None):
    """(X,Y) 网格上的地形高度 (m)，已压平机位、已限高。"""
    p = PRESETS[kind]
    relief = p["relief"] if h_far is None else min(p["relief"], float(h_far))
    if relief <= 0:
        return np.zeros_like(X)
    h = np.zeros_like(X)
    amp = 1.0
    for k in range(p["octaves"]):
        h += amp * _lattice_noise(X[:, 0], Y[0, :],
                                  p["wavelength"] / (2 ** k), seed + k)
        amp *= 0.5
    h -= h.min()
    h *= relief / max(h.max(), 1e-9)
    # 限高只作用在切面覆盖范围内（见 HEIGHT_FRAC / CORE_R）：核心区压到切面之下，
    # 远景保留全幅起伏，中间按 smoothstep 过渡，避免出现一圈突然长高的环。
    cap = HEIGHT_FRAC * float(z_wake)
    r = np.hypot(X - 0.5 * (lx.min() + lx.max()),
                 Y - 0.5 * (ly.min() + ly.max()))
    t = np.clip((r - CORE_R) / CORE_R, 0.0, 1.0)
    w = t * t * (3.0 - 2.0 * t)                    # 0=核心区, 1=远景
    h_core = np.minimum(h, cap)
    h = h_core * (1.0 - w) + h * w
    return h * _pad_mask(X, Y, lx, ly)


def build(lx, ly, kind="mountains", seed=7, z_wake=90.0, span_pad=3200.0,
          nx=200, h_far=None):
    """生成地形 StructuredGrid；高度写进 `elevation` 标量供上色。

    网格比机位包围盒外扩 span_pad，否则地形边缘就在画面中央。默认 3200 m
    必须 > CORE_R，否则全网格都落在限高的核心区里、远景山脊无处生长。
    nx=200 是取舍：再密看不出差别，再疏山脊会出多边形棱。

    h_far: 远景起伏的上限 (m)。调用方（画机组时放大了 TURB_SCALE 倍）要拿它
    把山脊压到机组之下 —— 否则 700 m 的山和 765 m 的"放大机组"一样高，画面
    主体从风场变成山。给 None 用 preset 原值。
    """
    lx, ly = np.asarray(lx, dtype=float), np.asarray(ly, dtype=float)
    x = np.linspace(lx.min() - span_pad, lx.max() + span_pad, nx)
    y = np.linspace(ly.min() - span_pad, ly.max() + span_pad, nx)
    X, Y = np.meshgrid(x, y, indexing="ij")
    Z = height_field(X, Y, lx, ly, kind=kind, seed=seed, z_wake=z_wake,
                     h_far=h_far)
    mesh = pv.StructuredGrid(X[:, :, None], Y[:, :, None], Z[:, :, None])
    mesh["elevation"] = Z.ravel(order="F")
    return mesh


def add(pl, mesh, kind="mountains", label=True):
    """把地形加进场景，返回 actor。

    `pickable=False`：拖拽机组时求鼠标落点靠的是那张 z=0 的拾取平面，
    地形要是可拾取，机位会被拖到山坡上去。
    不画 scalar bar —— 装饰性高度没有读数价值，多一根色带只会和 U 那根抢注意力。
    """
    actor = pl.add_mesh(mesh, scalars="elevation", cmap=PRESETS[kind]["cmap"],
                       show_scalar_bar=False, show_edges=False,
                       ambient=0.3, diffuse=0.7, pickable=False)
    if label:
        add_label(pl)
    return actor


def add_label(pl, font_size=9):
    """画"地形是装饰"那句声明。找不到中文字体就退回英文（见 _CJK_FONTS）。

    颜色不能用浅灰：这句话是压在地形上的，而地形本身就是中调土色/草色 ⇒ 灰字
    几乎读不出来。用近黑加阴影，保证它在任何一种地形色上都还是能读的。
    """
    font = label_font()
    kw = dict(position="lower_left", font_size=font_size, color="black",
              shadow=True)
    if font is None:
        return pl.add_text(LABEL_ASCII, **kw)
    return pl.add_text(LABEL, font_file=font, **kw)
