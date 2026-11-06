"""RViz 命令行参数、风况预设与应用启动。"""
import argparse
import sys

from qtpy import QtWidgets

from wfrl.viz.rviz_runtime import (
    _run_headless,
    make_fastfarm_policy_fn,
    make_policy_fn,
)
from wfrl.viz.rviz_sources import FarmSource, FastFarmSource
from wfrl.viz.rviz_window import MainWindow


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="floris", choices=["floris", "fastfarm"],
                    help="floris=稳态可拖拽；fastfarm=真实动态(需 mpiexec)")
    ap.add_argument("--env", default=None,
                    help="环境 id；默认按 backend 选 Ablaincourt_Floris / Dec_Turb3_Row1_Fastfarm")
    ap.add_argument("--model", default="auto",
                    help="auto 或 ckpt 路径(fastfarm) / ippo|central(floris)")
    ap.add_argument("--max-steps", type=int, default=200,
                    help="fastfarm 回合长度（FAST.Farm 迭代预算；桌面模式至少 4）")
    ap.add_argument("--controls", default="yaw",
                    help="逗号分隔的控制量：yaw[,pitch][,torque]（仅 fastfarm）。"
                         "torque 会完全替换基线变速控制器，默认边界 ±2e4 N·m "
                         "低于额定 ~4.3e4，谨慎开。")
    ap.add_argument("--pitch-demo", default="hold",
                    choices=("hold", "sweep", "off"),
                    help="外部桨距指令的形态（仅 --controls 含 pitch 时有意义）。"
                         "hold=阶跃保持，只在上游机组抬 3° 并保持一段，其余归零，"
                         "贴近真机（额定以下基线贴 0°、调节不频繁，运行区间 0~25°，"
                         "90° 是停机）；sweep=连续正弦，不真实但自检直观；"
                         "off=完全不给外部指令，画面上的桨距全部来自基线控制器。")
    ap.add_argument("--headless", type=int, default=0, metavar="N",
                    help="不开窗口，只跑 N 步并打印遥测（自检用）")
    ap.add_argument("--flex-scale", type=float, default=5.0, metavar="K",
                    help="叶片变形的**显示**放大倍数（不影响物理与遥测读数）。"
                         "1=真实挠度，0=关掉柔性回到刚体。仅 fastfarm 有弯矩通道。"
                         "会被自动压低到不穿塔筒为止（净空列给的仍是 1x 真值）。")
    ap.add_argument("--wake-vtk", action="store_true",
                    help="尾流平面用 FAST.Farm 真实 DisXY 切面（默认 FLORIS proxy）；"
                         "仅 fastfarm 后端，每控制步落一张盘 ~1 MB"
                         "（3T 布局实测，200 步回合 ≈ 200 MB）")
    ap.add_argument("--wake-z", type=float, default=90.0, metavar="M",
                    help="真实切面的高度 (m)，默认 90 = 轮毂高度")
    ap.add_argument("--turb", nargs="?", const="90m_08mps.bts", default=None,
                    metavar="BTS",
                    help="湍流来流（WindType=3，TurbSim .bts）。不给值用模板自带的 "
                         "90m_08mps.bts（u_hub 8.00 m/s, TI 9%%, 仅 200 s）。"
                         "来流由盒子决定，同时指定的 --wind-speed 会被底层拒绝并记录")
    ap.add_argument("--wind-speed", type=float, default=None, metavar="MPS",
                    help="钉死均匀来流 (m/s)；不给则每次 reset 重抽 Weibull，"
                         "而 P∝u³ ⇒ 前后对照会变成比两次抽签")
    ap.add_argument("--wind", default=None, metavar="NAME",
                    help="内置风况预设，一键切换小风/大风（覆盖 --wind-speed/--turb）。"
                         "可选见 --list-wind")
    ap.add_argument("--list-wind", action="store_true",
                    help="列出内置风况预设后退出")
    ap.add_argument("--render-ms", type=int, default=33, metavar="MS",
                    help="画面刷新周期（默认 33≈30fps）。物理步进已与渲染解耦，"
                         "转子按真实流逝时间以 1x 实时匀速转。")
    ap.add_argument("--terrain", choices=("mountains", "gobi", "flat"),
                    default=None,
                    help="背景地形（山地/戈壁/平地）。**纯装饰，不参与流场计算** —— "
                         "本算例 FAST.Farm 地面是平的、轮毂高度是绝对高度；"
                         "地形已压平塔基并在切面覆盖区限高，不会暗示地形效应")
    ap.add_argument("--dual-view", action="store_true",
                    help="Gazebo 式双视图：左「世界」自由轨道，右「随机镜头」贴着"
                         "一台机组看叶片。两个视图共享 actor，不额外建几何")
    ap.add_argument("--focus", type=int, default=0, metavar="I",
                    help="随机镜头锁定第几台（0 起，默认 T1）")
    args = ap.parse_args()

    if args.backend == "fastfarm" and not args.headless and args.max_steps < 4:
        ap.error("FAST.Farm 桌面模式的 --max-steps 至少为 4；更短的预算会在窗口首帧前耗尽")

    from wfrl import windcond
    if args.list_wind:
        print("内置风况预设（--wind NAME）：\n" + windcond.describe())
        print("\n各档看点：")
        for k in windcond.ORDER:
            print(f"  {k}: {windcond.PRESETS[k]['note']}")
        return 0
    args.upstream_request = {"rviz": {
        "wind": args.wind,
        "wind_speed": args.wind_speed,
        "wind_time_series": args.turb,
        "wind_speed_role": "overridden_by_preset" if args.wind else "solver_request",
        "wind_time_series_role": "overridden_by_preset" if args.wind else "solver_request",
    }}
    if args.wind:
        # FLORIS 模式保留预设风向；FAST.Farm 的应用与拒绝由底层接口负责。
        ws, wd, turb = windcond.resolve(args.wind, backend="floris")
        args.wind_speed, args.wind_dir, args.turb = ws, wd, turb
        p = windcond.PRESETS[args.wind]
        args.upstream_request["rviz"]["preset"] = {
            **p, "wind_speed_role": "solver_request", "wind_direction_role": "solver_request",
        }
        print(f"[风况] {args.wind} — {p['label']}\n        {p['note']}",
              flush=True)
    else:
        args.wind_dir = None

    if args.headless:
        return _run_headless(args)

    app = QtWidgets.QApplication(sys.argv)

    if args.backend == "fastfarm":
        env_id = args.env or "Dec_Turb3_Row1_Fastfarm"
        wake = "真实 DisXY 切面" if args.wake_vtk else "FLORIS proxy"
        print(f"[rviz] (1/4) 正在启动 FAST.Farm（尾流={wake}，约 10 秒）…",
              flush=True)
        source = FastFarmSource(env_id=env_id, policy="auto",
                                max_steps=args.max_steps,
                                controls=tuple(args.controls.split(",")),
                                wake_vtk=args.wake_vtk,
                                wake_vtk_z=args.wake_z,
                                wind_time_series=args.turb,
                                wind_speed=args.wind_speed,
                                wind_direction=args.wind_dir,
                                upstream_request=args.upstream_request,
                                pitch_demo=args.pitch_demo)
        print("[rviz] (2/4) 数据源就绪，正在建 3D 窗口…", flush=True)
        policy_fn = (None if args.model == "auto"
                     else make_fastfarm_policy_fn(args.model))
        # FAST.Farm 一个 3 s 控制步要 2~3 s 墙钟，扔到后台线程去跑（async_step），
        # 主线程只负责按 render_ms 插值转子相位；布局固定 ⇒ 禁用拖拽。
        # tick_ms 只是"多久看一眼能不能开下一步"，实际节奏由 FAST.Farm 决定。
        win = MainWindow(source, policy_fn=policy_fn,
                         allow_drag=False, tick_ms=100,
                         flex_scale=args.flex_scale,
                         render_ms=args.render_ms, async_step=True,
                         terrain_kind=args.terrain,
                         dual_view=args.dual_view, focus=args.focus)
        print("[rviz] (3/4) 窗口已创建，弹出中。若没看到，请看任务栏 / Alt+Tab。",
              flush=True)
    else:
        env_id = args.env or "Ablaincourt_Floris"
        source = FarmSource(env_id=env_id, policy="auto",
                            wind_speed=args.wind_speed,
                            wind_direction=args.wind_dir)
        policy_fn = None if args.model == "auto" else make_policy_fn(args.model)
        # FLORIS 后端没有弯矩通道 ⇒ 柔性用不上，叶片保持刚体
        # FLORIS 后端没有弯矩通道 ⇒ 柔性用不上，叶片保持刚体。稳态求解够快，
        # 同步步进即可（拖拽也要在主线程改 fi，异步会打架）。
        win = MainWindow(source, policy_fn=policy_fn, allow_drag=True,
                         tick_ms=120, flex_scale=0.0,
                         render_ms=args.render_ms,
                         terrain_kind=args.terrain,
                         dual_view=args.dual_view, focus=args.focus)

    win.show()
    win.raise_()
    win.activateWindow()
    if args.backend == "fastfarm":
        print("[rviz] (4/4) 已进入事件循环，窗口应已在前台。关闭窗口即安全退出。",
              flush=True)
    sys.exit(app.exec_())

