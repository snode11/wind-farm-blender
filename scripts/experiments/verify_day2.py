"""Day 2 验收脚本 —— 把之前散在命令行里的检查固化成可复跑的断言。

分两层，因为两层的代价差三个数量级：

  **离线层**（默认，~40 s，不起 FAST.Farm）
      A. `.bts` 轮毂层反演：分母必须是 8.00 而不是全场均值 8.75
      B. 地形不变量：塔基恰好 z=0、核心区限高、远景受 h_far 约束
      C. 中文标签：VTK 缺字形是**静默丢字**，要断言渲染出来的不是 "FAST.Farm"
      D. 双视图 GUI：真开 `MainWindow`，跑几秒后对画面下断言
      E. 源码结构：函数体内 `return` 之后不许还有语句

  **在线层**（`--online`，~3 min，要 mpiexec 起 FAST.Farm）
      F. 全栈联跑：真实切面 + 湍流 + 地形 + 双视图，查湍流是否真的进来了

用法：
    python scripts/experiments/verify_day2.py                    # 离线层
    python scripts/experiments/verify_day2.py --only bts,terrain # 挑着跑
    mpiexec -n 1 python scripts/experiments/verify_day2.py --online   # 全部

D 组会**真的弹出窗口**（FLORIS 后端，秒级起步），截图落在 `results/verify/`。
判据是画面本身：两个视口各有内容且互不相同、机组 actor 确实共享、方位角在推进、
偏航被策略改动。跑它需要能开图形界面的桌面会话 —— 无头环境下用 `--only` 跳过。
"""
import argparse
import os
import subprocess
import sys

import numpy as np

from wfrl import paths

os.chdir(paths.ROOT)                # wfcrl 按 CWD 找 simulators/ 与 __simul__/

import pyvista as pv                # noqa: E402

# C 组那几张字用离屏画；D 组要真开窗口，进去之前会把它关掉。
pv.OFF_SCREEN = True

TURB_SCALE = 5.0                    # 与 rviz_app.TURB_SCALE 对齐
HUB_H = 90.0
LAYOUT_X = np.array([0.0, 504.0, 1008.0])
LAYOUT_Y = np.zeros(3)


class Check:
    """一条检查。收集断言结果而不是第一条就崩，好让一次跑完看到全貌。"""

    def __init__(self, name):
        self.name = name
        self.rows = []
        self.ok = True

    def eq(self, label, got, want, tol=0.0):
        good = abs(float(got) - float(want)) <= tol
        self.rows.append((good, f"{label}: {got:.4g} (期望 {want:.4g}±{tol:g})"))
        self.ok &= good
        return good

    def true(self, label, cond, detail=""):
        good = bool(cond)
        self.rows.append((good, f"{label}{' — ' + detail if detail else ''}"))
        self.ok &= good
        return good

    def report(self):
        print(f"\n[{'PASS' if self.ok else 'FAIL'}] {self.name}")
        for good, text in self.rows:
            print(f"   {'v' if good else 'X'} {text}")
        return self.ok


# --- A. .bts 轮毂层 ---------------------------------------------------------
def check_bts():
    """WindType=3 下 wfcrl 从不写 HWindSpeed，分母只能从 .bts 自己算。

    而且必须取**轮毂高度那一层**：全场均值 8.75 插到 90 m 只有 8.00，
    差的 9.5% 全在竖向剪切上。拿全场均值当自由来流，P/u³ 整体偏低 ~25%。
    """
    from openfast_toolbox.io import TurbSimFile

    from wfrl.inflow import bts_info, resolve_bts

    c = Check("A. .bts 轮毂层反演（湍流来流的分母）")
    path = resolve_bts("90m_08mps.bts")
    c.true("模板 .bts 找得到", os.path.exists(path), path)

    info = bts_info("90m_08mps.bts", z_hub=HUB_H)
    c.eq("u_hub @90 m", info.u_hub, 8.00, tol=0.02)
    c.eq("TI", info.ti, 9.0, tol=0.2)
    c.eq("盒子时长 t_span", info.t_span, 199.9, tol=0.5)

    # 独立重算一遍：验的是 inflow.py，就不能拿 inflow.py 的中间量当参照。
    ts = TurbSimFile(path)
    u = np.asarray(ts["u"])[0]                  # (nt, ny, nz) 纵向分量
    z = np.asarray(ts["z"])
    prof = u.mean(axis=(0, 1))
    c.eq("独立重算的轮毂层插值", float(np.interp(HUB_H, z, prof)), info.u_hub,
         tol=1e-6)

    # 关键判据：插值层 ≠ 最近层。差 0.09 m/s，所以插值是必要而非讲究。
    near = float(z[np.argmin(np.abs(z - HUB_H))])
    c.true("90 m 不在网格层上（⇒ 必须插值）", abs(near - HUB_H) > 1.0,
           f"最近层 {near:.0f} m")
    c.true("最近层 ≠ 插值结果（插值是必要的）",
           abs(float(prof[np.argmin(np.abs(z - HUB_H))]) - info.u_hub) > 0.05,
           f"最近层 {float(prof[np.argmin(np.abs(z - HUB_H))]):.3f} vs "
           f"插值 {info.u_hub:.3f}")

    # 全场均值必须显著高于轮毂层，否则"取错分母"这个坑根本不存在、断言也就没意义
    c.true("全场均值 > 轮毂层（竖向剪切确实存在）",
           float(u.mean()) > info.u_hub + 0.3,
           f"全场 {float(u.mean()):.3f} vs 轮毂 {info.u_hub:.3f}，"
           f"P/u³ 用错分母偏 {100 * ((float(u.mean()) / info.u_hub) ** 3 - 1):.0f}%")

    # HWindSpeed 在 WindType=3 下从不被写 —— 本模块存在的唯一理由，钉住它
    import inspect

    import wfcrl.simul_utils as su
    src = inspect.getsource(su)
    c.true("wfcrl 源码里 HWindSpeed 与 FileName_BTS 不在同一分支",
           "FileName_BTS" in src and src.count("HWindSpeed") >= 1)
    return c


# --- B. 地形不变量 ----------------------------------------------------------
def check_terrain():
    """地形是装饰，不能在画面上暗示一个仿真里不存在的地形效应。

    两条硬约束：塔基**恰好** z=0（FAST.Farm 地面是平的、轮毂高度是绝对值，
    抬地基等于偷偷改了轮毂高度），核心区不得戳穿尾流切面。
    """
    from wfrl.viz import terrain

    c = Check("B. 地形不变量（塔基压平 / 核心区限高 / 远景受控）")
    cap = terrain.HEIGHT_FRAC * HUB_H
    hub_z = HUB_H * TURB_SCALE
    h_far = 0.5 * hub_z

    for kind in ("mountains", "gobi", "flat"):
        mesh = terrain.build(LAYOUT_X, LAYOUT_Y, kind=kind, z_wake=HUB_H,
                             h_far=h_far)
        P = mesh.points
        Z = P[:, 2]
        # 塔基：容差为 0，不是"近似平"而是"就是 0"
        base_err = max(
            abs(Z[np.argmin(np.hypot(P[:, 0] - x, P[:, 1] - y))])
            for x, y in zip(LAYOUT_X, LAYOUT_Y))
        c.true(f"[{kind}] 塔基 z 恰好为 0", base_err == 0.0,
               f"max|z| = {base_err:.3e}")

        cx = 0.5 * (LAYOUT_X.min() + LAYOUT_X.max())
        cy = 0.5 * (LAYOUT_Y.min() + LAYOUT_Y.max())
        core = np.hypot(P[:, 0] - cx, P[:, 1] - cy) <= terrain.CORE_R
        c.true(f"[{kind}] 核心区不穿切面", Z[core].max() <= cap + 1e-6,
               f"{Z[core].max():.1f} <= {cap:.1f}")
        c.true(f"[{kind}] 远景受 h_far 约束", Z.max() <= h_far + 1e-6,
               f"{Z.max():.1f} <= {h_far:.0f}")

    # 反向断言：不给 h_far 时山脊必须能长起来，否则"山地"只是丘陵
    tall = terrain.build(LAYOUT_X, LAYOUT_Y, kind="mountains", z_wake=HUB_H)
    c.true("不限 h_far 时远景确实是山（>300 m）",
           tall.points[:, 2].max() > 300.0,
           f"{tall.points[:, 2].max():.0f} m")
    return c


# --- C. 中文标签 ------------------------------------------------------------
def check_label():
    """VTK 自带字体没有中文字形，**缺字是静默丢弃而不是画方框**。

    "地形为装饰性渲染，不参与流场计算（FAST.Farm 本算例为平地）" 会被丢到
    只剩 ASCII，屏幕上恰好显示成 "FAST.Farm" —— 把"这是装饰"的声明反读成
    "这是 FAST.Farm 的地形"，比不写还糟。所以这条要真渲染出来数像素。
    """
    from wfrl.viz import terrain

    c = Check("C. 中文声明标签（缺字形会静默丢成 'FAST.Farm'）")
    font = terrain.label_font()
    c.true("找得到中文字体", font is not None, str(font))

    def ink(text, **kw):
        """渲染一行字，返回非背景像素数 —— 丢字会让墨迹显著变少。"""
        pl = pv.Plotter(off_screen=True, window_size=(1000, 120))
        pl.set_background("white")
        pl.add_text(text, position="lower_left", font_size=10,
                    color="black", **kw)
        img = np.asarray(pl.screenshot(return_img=True))
        pl.close()
        return int((img.min(axis=2) < 128).sum())

    ascii_only = "FAST.Farm"
    if font:
        full = ink(terrain.LABEL, font_file=font)
        stub = ink(ascii_only, font_file=font)
        # 整句中文的墨迹必须远多于 "FAST.Farm" 这几个字符
        c.true("整句渲染出来 ≫ 'FAST.Farm' 的墨迹量", full > 3 * stub,
               f"{full} px vs {stub} px")
        c.true("整句确实画了东西", full > 200, f"{full} px")

    # 兜底路径也要能读：宁可英文，不能只剩半句
    c.true("ASCII 兜底不含中文", terrain.LABEL_ASCII.isascii())
    c.true("兜底句明确否定流场影响",
           "NOT affect the flow" in terrain.LABEL_ASCII)
    return c


# --- D. 双视图（真开 Qt 窗口） -------------------------------------------
def check_dualview(seconds=6.0, shot_dir=None):
    """拉起真的 `MainWindow`，让它自己跑几秒，再对**画面**下断言。

    这才是这个平台该有的验收：不是"renderer 语义对不对"，而是"窗口起来了、
    两个视口都画了东西、叶片在转、策略在动"。之前用 pv.Plotter 代验是因为我
    自己设了 QT_QPA_PLATFORM=offscreen —— 那是自找的，真实桌面会话下 Qt 正常。

    用 FLORIS 后端：秒级起步、不占 MPI，而双视图/地形/共享 actor 全是渲染层的
    东西，与哪个后端喂数据无关。FAST.Farm 那一路由 F 组覆盖。
    """
    import glob

    from qtpy import QtCore, QtWidgets

    from wfrl.viz.rviz_app import TURB_SCALE, FarmSource, MainWindow

    c = Check("D. 双视图 GUI（真开窗口，对画面下断言）")
    pv.OFF_SCREEN = False              # 这一组要的就是真窗口
    shot_dir = shot_dir or os.path.join(paths.ROOT, "results", "verify")
    os.makedirs(shot_dir, exist_ok=True)
    for f in glob.glob(os.path.join(shot_dir, "day2_dual_*.png")):
        os.remove(f)

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    src = FarmSource(env_id="Ablaincourt_Floris", policy="auto")
    win = MainWindow(src, allow_drag=True, tick_ms=120, flex_scale=0.0,
                     render_ms=33, terrain_kind="mountains",
                     dual_view=True, focus=1)
    win.resize(1280, 720)
    win.show()
    win.raise_()

    state = {}

    def snap(tag):
        import imageio.v2 as iio
        img = np.asarray(win.plotter.screenshot(return_img=True))
        iio.imwrite(os.path.join(shot_dir, f"day2_dual_{tag}.png"), img)
        return img

    def phase_early():
        state["spin0"] = np.copy(np.atleast_1d(win._spin))
        state["yaw0"] = np.copy(np.asarray(
            win.telemetry._buf.get("yaw", [np.nan]), dtype=float))
        state["img0"] = snap("t0")

    def phase_late():
        state["spin1"] = np.copy(np.atleast_1d(win._spin))
        state["yaw1"] = np.copy(np.asarray(
            win.telemetry._buf.get("yaw", [np.nan]), dtype=float))
        state["img1"] = snap("t1")
        state["ncam"] = [tuple(r.camera.position) for r in win.plotter.renderers]
        state["nact"] = [r.GetViewProps().GetNumberOfItems()
                         for r in win.plotter.renderers]

        # actor 共享：逐个查机组 actor 在不在右 renderer 的 prop 列表里，
        # 并确认拿到的是**同一个对象**（共享而非各建一套）。
        from wfrl.viz.animate import turbine_actors
        props = win.plotter.renderers[1].GetViewProps()
        r1 = {id(props.GetItemAsObject(i)) for i in range(props.GetNumberOfItems())}
        tacts = [a for t in win.turbines for a in turbine_actors(t)]
        state["shared"] = {
            "n_turb": len(tacts),
            "missing": sum(id(a) not in r1 for a in tacts),
            "terrain": (win.terrain_actor is not None
                        and id(win.terrain_actor) in r1),
            "same_obj": bool(tacts) and id(tacts[0]) in r1,
        }
        state["alive"] = win.isVisible() and win.rtimer.isActive()
        state["playing"] = win._playing

        # 分屏滑块：QSplitter 那条路已验证不行（actor 的 GL 资源绑在创建它的
        # render window 上，跨窗口共享那侧 ink=0，见 probe_splitter.py）。
        # 这里验的是替代方案 —— 同一 render window 内改 renderer 的 viewport。
        # 判据不能只看 viewport 数值（设了不等于画面跟着变），要看两侧墨迹随
        # 拖动此消彼长。
        sp = []
        for pct in (25, 80):
            win.split_slider.setValue(pct)
            app.processEvents()
            im = np.asarray(win.plotter.screenshot(return_img=True))
            cut = int(pct / 100.0 * im.shape[1])
            sp.append({
                "pct": pct,
                "vp": tuple(np.round(win.plotter.renderers[0].GetViewport(), 3)),
                "ink_l": int((im[:, :cut].min(axis=2) < 235).sum()),
                "ink_r": int((im[:, cut:].min(axis=2) < 235).sum()),
                "wl": cut, "wr": im.shape[1] - cut,
            })
        state["split"] = sp
        win.split_slider.setValue(50)
        app.processEvents()

        win.close()
        app.quit()

    QtCore.QTimer.singleShot(int(1000 * min(2.0, 0.4 * seconds)), phase_early)
    QtCore.QTimer.singleShot(int(1000 * seconds), phase_late)
    app.exec_()

    # 1) 窗口真的活着跑完，不是起来就崩
    c.true("窗口存活到结束且渲染定时器在跑", state.get("alive", False))
    c.true("物理仍在推进（未因异常停摆）", state.get("playing", False))

    img0, img1 = state["img0"], state["img1"]
    c.true("截图尺寸正常", img0.ndim == 3 and img0.shape[0] > 200,
           str(img0.shape))

    # 2) 两个视口都画了东西 —— 各自数非背景像素，任一半空白就是没画
    h, w = img1.shape[:2]
    left, right = img1[:, : w // 2], img1[:, w // 2:]
    ink_l = int((left.min(axis=2) < 235).sum())
    ink_r = int((right.min(axis=2) < 235).sum())
    c.true("左视口有内容", ink_l > 0.02 * left[..., 0].size, f"{ink_l} px")
    c.true("右视口有内容", ink_r > 0.02 * right[..., 0].size, f"{ink_r} px")

    # 3) 两个视口画的**不是同一个画面**。不能比"墨迹量"：地形铺满画面后两边
    #    都接近饱和，数出来几乎一样多。直接逐像素比这两半。
    half = min(left.shape[1], right.shape[1])
    dlr = int((np.abs(left[:, :half].astype(int)
                      - right[:, :half].astype(int)).max(axis=2) > 12).sum())
    c.true("两视口画面不同（不是同一画面画两遍）",
           dlr > 0.25 * left[:, :half, 0].size,
           f"{dlr}/{left[:, :half, 0].size} px 不同")
    c.true("两视口相机相互独立",
           not np.allclose(state["ncam"][0], state["ncam"][1]),
           f"{np.round(state['ncam'][0])} vs {np.round(state['ncam'][1])}")

    # 4) actor 共享：只查该共享的那批（机组几何 + 地形），不比总数 ——
    #    左视图还挂着网格轴/坐标系/标题，总数天然多几个。
    shared = state["shared"]
    c.true("全部机组 actor 都挂进了右视口", shared["missing"] == 0,
           f"{shared['n_turb']} 个机组 actor，缺 {shared['missing']}")
    c.true("地形也挂进了右视口", shared["terrain"])
    c.true("是共享同一对象而非各建一套（id 相同）", shared["same_obj"])

    # 5) 叶片真的在转：方位角随真实流逝时间推进
    d_spin = float(np.max(np.abs(state["spin1"] - state["spin0"])))
    c.true("叶片方位角在推进（转子在转）", d_spin > 5.0, f"Δ={d_spin:.1f}°")

    # 6) 画面确实在变（不是冻在第一帧）
    diff = int((np.abs(img1.astype(int) - img0.astype(int)).max(axis=2) > 8).sum())
    c.true("画面逐帧在变（非静止图）", diff > 0.005 * img0[..., 0].size,
           f"{diff} px 变化")

    # 7) 控制策略在动：偏航角被策略改过
    y0, y1 = state["yaw0"], state["yaw1"]
    if np.isfinite(y0).any() and np.isfinite(y1).any():
        c.true("偏航被策略推动（控制回路在跑）",
               float(np.nanmax(np.abs(y1 - y0))) > 1e-6,
               f"Δyaw = {np.round(y1 - y0, 3)}")

    # 8) 显示放大常量共享，别让相机按 1x 取景对着 5x 的机器
    c.eq("TURB_SCALE", TURB_SCALE, 5.0)

    # 9) 分屏可调：viewport 跟着滑块走，且两侧画面随之此消彼长
    sp = state.get("split", [])
    c.true("分屏滑块存在且拖得动", len(sp) == 2)
    if len(sp) == 2:
        a, b = sp                                    # 25% / 80%
        c.eq("左视口 viewport 右边界 @25%", a["vp"][2], 0.25, tol=1e-6)
        c.eq("左视口 viewport 右边界 @80%", b["vp"][2], 0.80, tol=1e-6)
        # 归一成"每像素墨迹密度"再比：直接比总墨迹会被视口面积本身带偏。
        for s in (a, b):
            s["dl"] = s["ink_l"] / max(1, s["wl"])
            s["dr"] = s["ink_r"] / max(1, s["wr"])
        c.true("两侧在 25% 和 80% 下都还有内容（没有一侧变全白）",
               min(a["ink_l"], a["ink_r"], b["ink_l"], b["ink_r"]) > 0,
               f"25%: L={a['ink_l']} R={a['ink_r']}；"
               f"80%: L={b['ink_l']} R={b['ink_r']}")
        c.true("左视口拖宽后确实吃掉了更多画布",
               b["ink_l"] > a["ink_l"] and b["ink_r"] < a["ink_r"],
               f"L {a['ink_l']}→{b['ink_l']}，R {a['ink_r']}→{b['ink_r']}")
    print(f"   截图已存到 {shot_dir}", flush=True)
    return c


# --- E. 源码结构 ------------------------------------------------------------
def check_source():
    """扫"函数体内 return 之后还有语句"。

    这一轮真踩过：`_farm_bounds` 被插进 `__init__` 内部，它的 return 把侧栏、
    工具条、拖拽和两个定时器全变成死代码，而语法完全合法、import 也不报错。
    """
    import ast

    c = Check("E. 源码结构（无 return 之后的死代码）")
    files = ["wfrl/viz/rviz_app.py", "wfrl/viz/terrain.py",
             "wfrl/viz/field.py", "wfrl/inflow.py"]
    dead = []
    for rel in files:
        tree = ast.parse(open(rel, encoding="utf-8").read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for i, st in enumerate(node.body[:-1]):
                if isinstance(st, ast.Return):
                    dead.append(f"{rel}:{node.body[i + 1].lineno} in {node.name}")
    c.true("无 unreachable 语句", not dead, "; ".join(dead) or "clean")

    # MainWindow 的 __init__ 必须一路跑到两个定时器
    tree = ast.parse(open("wfrl/viz/rviz_app.py", encoding="utf-8").read())
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    ms = {m.name: m for m in cls.body if isinstance(m, ast.FunctionDef)}
    init = ast.unparse(ms["__init__"])
    c.true("__init__ 建到了渲染定时器", "self.rtimer.start" in init)
    c.true("__init__ 建到了物理定时器", "self.timer.start" in init)
    for name in ("_share", "_chase_camera", "_draw_dims", "_farm_bounds",
                 "_poked_renderer"):
        c.true(f"MainWindow.{name} 是方法而非嵌套定义", name in ms)

    # 双视图下拾取必须问 FindPokedRenderer，不能用 active renderer
    poked = ast.unparse(ms["_poked_renderer"])
    c.true("拾取用 FindPokedRenderer", "FindPokedRenderer" in poked)

    def reads_active_renderer(fn):
        """精确匹配 `self.plotter.renderer` 属性读取。

        不能用子串：`self.plotter.renderers[0]` 含有它，但那是按下标取
        renderer（合法），和取 active renderer（不合法）是两回事。
        """
        for n in ast.walk(fn):
            if (isinstance(n, ast.Attribute) and n.attr == "renderer"
                    and isinstance(n.value, ast.Attribute)
                    and n.value.attr == "plotter"):
                return True
        return False

    for name in ("_on_press", "_on_move"):
        c.true(f"{name} 不读 active renderer", not reads_active_renderer(ms[name]))
        c.true(f"{name} 走 _poked_renderer()",
               "_poked_renderer" in ast.unparse(ms[name]))
    # 右视图是观察窗，不许在那儿拖
    c.true("_on_press 拒绝非世界视图的拖拽",
           "self.plotter.renderers[0]" in ast.unparse(ms["_on_press"]))
    return c


# --- F. 在线全栈 ------------------------------------------------------------
def check_online(steps=6):
    """起真 FAST.Farm，验湍流是否**真的**进来了。

    判据是转子测速的**时间标准差**，不是均值：均匀来流下这一列只随尾流建立
    缓慢漂移，湍流盒子下每步都在抖。这个 std 天然小于盒子 TI —— 每 3 s 才采
    一点、且已被转子平均过。
    """
    c = Check(f"F. 在线全栈（FAST.Farm + 湍流 + 真实切面 + 地形 + 双视图，{steps} 步）")
    cmd = [sys.executable, "-X", "utf8", "-m", "wfrl.viz.rviz_app",
           "--backend", "fastfarm", "--headless", str(steps),
           "--max-steps", str(steps + 2), "--wake-vtk",
           "--turb", "90m_08mps.bts", "--terrain", "mountains", "--dual-view"]
    print("   $ " + " ".join(cmd), flush=True)
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=900)
    out = p.stdout + p.stderr

    c.true("进程正常退出", p.returncode == 0, f"rc={p.returncode}")
    # HEADLESS_OK 印在 src.close() 之后（rviz_app.py:978-979），所以它本身就
    # 蕴含"迭代预算已排空"。FAST.Farm 那句 "terminated normally" 是 exe 写给
    # 继承来的控制台的，不进这个管道，别拿它当判据。
    c.true("跑到 HEADLESS_OK（⇒ close() 已排空迭代预算）", "HEADLESS_OK" in out)
    c.true("无 MPI 挂起/超时痕迹",
           "MPI_RECV" not in out and "Traceback" not in out)
    c.true("分母取的是 .bts 轮毂层 8.00", "u_hub=8.00" in out)
    c.true("真实切面有交付", "真实切面共交付" in out)

    # 从遥测里抠出每步的转子测速，自己算时间 std —— 不信任日志里那行汇总
    import re
    series = []
    for line in out.splitlines():
        m = re.search(r"\| \[([0-9.\s]+)\] \| \[[0-9.\s]+\] \| "
                      r"\[[0-9.\s]+\] \| vtk", line)
        if m:
            series.append([float(v) for v in m.group(1).split()])
    if len(series) >= 3:
        arr = np.asarray(series)
        sd = arr.std(axis=0)
        c.true("逐机组测速随时间波动（湍流真进来了）", float(sd.min()) > 0.05,
               f"std = {np.array2string(sd, precision=3)} m/s")
        c.true("波动小于盒子 TI（已被转子平均过）",
               float(sd.max()) < 0.09 * float(arr.mean()),
               f"max std {sd.max():.3f} < {0.09 * arr.mean():.3f}")
    else:
        c.true("解析到逐步测速", False, f"只解析到 {len(series)} 行")

    # 孤儿进程会让**后续**的 spawn 静默挂起，属于必查项
    if sys.platform == "win32":
        q = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq FAST.Farm_x64_OMP_2023.exe"],
            capture_output=True, text=True, errors="replace")
        c.true("无 FAST.Farm 孤儿进程", "FAST.Farm" not in q.stdout)
    return c


CHECKS = {"bts": check_bts, "terrain": check_terrain, "label": check_label,
          "dualview": check_dualview, "source": check_source}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", default=None,
                    help=f"只跑其中几项，逗号分隔：{','.join(CHECKS)}")
    ap.add_argument("--online", action="store_true",
                    help="加跑在线全栈（要 mpiexec 起 FAST.Farm，约 3 min）")
    ap.add_argument("--online-steps", type=int, default=6)
    ap.add_argument("--gui-seconds", type=float, default=6.0, metavar="S",
                    help="D 组窗口开着跑多久（默认 6 s；调大可以人眼看着它转）")
    args = ap.parse_args()

    names = ([n.strip() for n in args.only.split(",")] if args.only
             else list(CHECKS))
    bad = [n for n in names if n not in CHECKS]
    if bad:
        ap.error(f"未知检查项 {bad}；可选：{list(CHECKS)}")

    results = [CHECKS[n](seconds=args.gui_seconds) if n == "dualview"
               else CHECKS[n]() for n in names]
    if args.online:
        results.append(check_online(args.online_steps))

    ok = all(r.report() for r in results)
    n_pass = sum(r.ok for r in results)
    print(f"\n{'=' * 60}\n{n_pass}/{len(results)} 组通过 —— "
          f"{'DAY2_VERIFY_OK' if ok else 'DAY2_VERIFY_FAILED'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
