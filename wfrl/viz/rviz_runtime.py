"""RViz 策略加载、无窗口驱动循环与来流统计。"""
import os

import numpy as np

from wfrl import paths
from wfrl.viz.geometry import CALIB
from wfrl.viz.rviz_channels import ChannelHub
from wfrl.viz.rviz_sources import FarmSource, FastFarmSource


# ---------------------------------------------------------------------------
def make_policy_fn(kind):
    """FLORIS 后端的策略包装（保持原行为）。"""
    if kind != "auto":
        print(f"[rviz] FLORIS 策略模式 '{kind}' 暂为 TODO（需同步双 FLORIS），回退 auto。")
    return "auto"


def make_fastfarm_policy_fn(ckpt_path):
    """加载 train_fastfarm.py 存的 MAPPO actor，返回 source->偏航增量(n,)。

    actor 的动作本身就是"偏航增量"，与 FastFarmDriver.step 的入参语义一致，直接返回。
    """
    import torch
    from wfrl.mappo_central import Actor

    # 允许只给文件名：ckpt 都在 results/checkpoints/ 下，且本模块启动时已 chdir 到
    # 仓库根，裸相对路径会相对根解析而非用户的 cwd —— 兜底查一次 paths.CKPT。
    if not os.path.exists(ckpt_path):
        cand = os.path.join(paths.CKPT, os.path.basename(ckpt_path))
        if os.path.exists(cand):
            ckpt_path = cand
        else:
            raise FileNotFoundError(
                f"找不到 ckpt: {ckpt_path}\n"
                f"也不在 {paths.CKPT} 下。可用的有:\n  "
                + "\n  ".join(sorted(f for f in os.listdir(paths.CKPT)
                                     if f.endswith(".pt"))))

    ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    obs_keys = ckpt.get("obs_keys", ["yaw", "wind_speed", "wind_direction"])
    actor = Actor(ckpt["obs_dim"], ckpt["act_dim"])
    actor.load_state_dict(ckpt["actor"])
    actor.eval()
    mean, var = ckpt["obs_mean"], ckpt["obs_var"]

    def fn(source):
        m = source._m
        obs = np.stack([np.asarray(m[k], dtype=np.float32) for k in obs_keys],
                       axis=1)                          # (n, obs_dim)
        obs_n = ((obs - mean) / np.sqrt(var + 1e-8)).astype(np.float32)
        with torch.no_grad():
            mu = actor.mu(torch.as_tensor(obs_n))       # 确定性动作=增量
        return mu.cpu().numpy().ravel()

    print(f"[rviz] 已加载 FAST.Farm MAPPO 策略: {ckpt_path}", flush=True)
    return fn


def _run_headless(args):
    """不开 Qt 窗口，直接跑数据源并打印遥测。

    FAST.Farm 那一路要 mpiexec 起，而 GUI 在 CI/远端不一定有显示；自检用这条路
    能验证「pitch/torque 指令进得去、转速反解出得来」而不依赖窗口。
    """
    env_id = args.env or ("Dec_Turb3_Row1_Fastfarm" if args.backend == "fastfarm"
                          else "Ablaincourt_Floris")
    # 经 mpiexec 的管道是块缓冲，启动那 ~25s 不主动 flush 就一个字都不出，
    # 看着和「起不来」一模一样 —— 每条状态都显式 flush。
    if args.backend == "fastfarm":
        wake = "真实 DisXY 切面" if args.wake_vtk else "FLORIS proxy"
        infl = f"湍流 {args.turb}" if args.turb else (
            f"均匀 {args.wind_speed} m/s" if args.wind_speed else "均匀(随机抽样)")
        print(f"[rviz] (1/2) 启动 FAST.Farm（尾流={wake}，来流={infl}，约 10-25 秒）… "
              f"env={env_id} controls={args.controls}", flush=True)
        src = FastFarmSource(env_id=env_id, policy="auto",
                             max_steps=max(args.max_steps, args.headless + 2),
                             controls=tuple(args.controls.split(",")),
                             wake_vtk=args.wake_vtk, wake_vtk_z=args.wake_z,
                             wind_time_series=args.turb,
                             wind_speed=args.wind_speed,
                             wind_direction=args.wind_dir,
                             upstream_request=args.upstream_request,
                             pitch_demo=args.pitch_demo)
        print("[rviz] (2/2) 数据源就绪，开始步进；Ctrl-C 可随时终止。", flush=True)
    else:
        src = FarmSource(env_id=env_id, policy="auto",
                         wind_speed=args.wind_speed,
                         wind_direction=getattr(args, "wind_dir", None))
    hub = ChannelHub()
    box = {}
    for t in ("yaw", "pitch", "pitch_meas", "torque", "rotor_speed",
              "power", "m_flap"):
        hub.subscribe(t, lambda d, k=t: box.__setitem__(k, np.asarray(d, float)))
    # 尾流单独订阅：真实路径发的是 DisXYFrame（不是 ndarray），且**可能整步没有新帧**
    # —— 这正是要在无窗口下核对的东西，所以记帧号与最深亏损。
    wk = {}
    hub.subscribe("wake", lambda fr: wk.update(
        {} if isinstance(fr, np.ndarray)
        else {"t": fr.t, "min": float(np.nanmin(fr.u)),
              "shape": fr.u.shape, "n": wk.get("n", 0) + 1}))
    # 净空要真实叶片几何才能算；解析不出来（比如没装模板）就只是少一列
    try:
        from wfrl.viz.geometry import load_turbine_geometry
        _geo = load_turbine_geometry()
        _bl = _geo.blade_surface()
        _bp, _xi = _bl.points.copy(), np.asarray(_bl.point_data["xi"])
    except Exception as e:                                      # noqa: BLE001
        print(f"[rviz] 几何解析失败，净空列关闭: {e!r}", flush=True)
        _geo = None
    print("step |   yaw(deg)   | 桨距指令(deg) | 桨距实测(deg) | torque(kNm)"
          " |  rpm  |  P(MW)"
          " | 转子测速(m/s) | T1 三片叶尖挠度(m) | T1 扫塔净空(m)", flush=True)
    # 循环体里任何异常都必须走到 src.close()：FAST.Farm 在 t=0 拿到固定迭代预算，
    # 不排空就会卡在 MPI_RECV 变孤儿，而孤儿会让**下一次** spawn 无声挂死（见
    # memory: wfcrl-fastfarm-bringup）。实测一次 UnboundLocalError 就留下一个。
    try:
        _headless_loop(args, src, hub, box, wk, _geo, _bp, _xi)
    finally:
        if hasattr(src, "close"):
            src.close()
    print("HEADLESS_OK", flush=True)
    return 0


def _report_inflow(src, ws):
    """收尾报一次来流：自由来流、盒子 TI、以及转子处测速的**时间**波动。

    转子测速的时间标准差是判断湍流有没有真的进来的判据：均匀来流（WindType=1）下
    它只随尾流建立缓慢漂移，湍流盒子（WindType=3）下每步都在抖。注意这个 std 不是
    盒子的 TI —— 采样只有每 3 s 一点、且已被转子平均过，必然比 TI 小。

    FLORIS 源没有 `u_inf`/`last_wind_speed`，整段都会是 nan：那不是"来流为 nan"，
    是这条后端不提供这两个量。直接说明并跳过，别打印 nan 也别让 nanstd 对空切片
    发 RuntimeWarning（一屏警告会盖住真正的输出）。
    """
    bts = getattr(getattr(src, "drv", None), "bts", None)
    u0 = getattr(src, "u_inf", float("nan"))
    if not np.isfinite(u0) and not np.isfinite(np.asarray(ws)).any():
        print("[rviz] 该后端不报自由来流/转子测速（FLORIS 源无此通道），跳过来流统计",
              flush=True)
        return
    line = f"[rviz] 自由来流 u_inf={u0:.2f} m/s"
    if bts is not None:
        line += (f"（.bts 轮毂层，TI={bts.ti:.1f}%，盒子 {bts.t_span:.0f}s，"
                 f"取 z={bts.z_used:.0f}m 层算 TI）")
    else:
        line += "（InflowWind HWindSpeed，均匀来流）"
    print(line, flush=True)
    if ws.ndim == 2 and len(ws) >= 2:
        sd = np.nanstd(ws, axis=0)
        print(f"[rviz] 转子处测速时间波动 std={np.array2string(sd, precision=3)} "
              f"m/s（均值 {np.array2string(np.nanmean(ws, axis=0), precision=2)}）",
              flush=True)


def _headless_loop(args, src, hub, box, wk, _geo, _bp, _xi):
    """遥测主循环（拆出来是为了让调用方用 try/finally 兜住 close）。"""
    ws = []
    for k in range(args.headless):
        src.step(hub)
        f = lambda a, p=2: np.array2string(box[a], precision=p,
                                           suppress_small=True)
        # 桨距两列必须分开看：`pitch` 是外部累加的指令（PitchRef 是**下界**），
        # `pitch_meas` 是 avrSWAP(4) 回读。只印指令的话 5 m/s 和 14 m/s 会打印
        # 出完全相同的一列 —— 因为 mdp 把被控量排除出 measures，指令根本不会
        # 被测量覆盖，那一列与风速无关。
        pm = " | " + f("pitch_meas") if "pitch_meas" in box else " | (无实测)"
        # 挠度 = 标定的仿射式作用在 T1 的三片叶根挥舞弯矩上（geometry.CALIB），
        # 和渲染器用的是同一个系数，所以这一列就是屏幕上叶片实际弯的量。
        defl = ""
        if "m_flap" in box and box["m_flap"].size:
            a, b = CALIB["flap"]
            mf3 = np.ravel(box["m_flap"])[:3]
            tip = a * mf3 + b
            defl = " | " + np.array2string(tip, precision=2)
            # 净空按 1x 真值算（渲染放大不参与），负值 = 已扫塔
            if _geo is not None:
                clr = [_geo.tower_clearance(_bp, _xi, m_flap=float(m))
                       if np.isfinite(m) else np.nan for m in mf3]
                defl += " | " + np.array2string(np.asarray(clr), precision=2)
        wake = (f" | vtk t={wk['t']} min_u={wk['min']:.2f}"
                if "t" in wk else (" | vtk 等待首帧" if args.wake_vtk else ""))
        # 转子处测速：湍流来流下它应逐步波动，均匀来流下基本是条直线 ——
        # 这是"湍流真的进来了"最直接的判据，所以逐步记下来。
        get_ws = getattr(src, "last_wind_speed", None)   # FLORIS 源没有这个方法
        ws.append(np.asarray(get_ws(), dtype=float) if get_ws
                  else np.array([np.nan]))
        print(f" {k+1:3d} | {f('yaw',1)} | {f('pitch')}{pm} | "
              f"{np.array2string(box['torque']/1e3, precision=1)} | "
              f"{f('rotor_speed')} | {f('power')} | "
              f"{np.array2string(ws[-1], precision=2, suppress_small=True)}"
              f"{defl}{wake}", flush=True)
        if getattr(src, "done", False):
            break
    _report_inflow(src, np.asarray(ws))
    if args.wake_vtk:
        if "t" in wk:
            u0 = float(getattr(src, "u_inf", np.nan) or np.nan)
            rel = (f"，末帧最深亏损 {100*(1-wk['min']/u0):.0f}%"
                   f"（相对自由来流 {u0:.2f} m/s）") if np.isfinite(u0) else ""
            print(f"[rviz] 真实切面共交付 {wk['n']} 帧，网格 {wk['shape']}{rel}",
                  flush=True)
        else:
            print("[rviz] 真实切面一帧都没到 —— 检查案例 vtk_ff/ 是否在写盘",
                  flush=True)

