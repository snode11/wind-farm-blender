"""后台训练线程的验收（需 mpiexec —— 真跑 FAST.Farm）。

跑：
    & "C:\\Program Files\\Microsoft MPI\\Bin\\mpiexec.exe" -n 1 `
      "C:\\Users\\s1155\\.conda\\envs\\wfcrl\\python.exe" `
      scripts/experiments/probe_trainer.py

这是 D2 的一半：训练必须在后台线程跑，主线程能一直响应。所以判据不是"loss 降了"
（几十步的 PPO 什么都说明不了），而是**线程行为**与**回路接线**：

  1. start() 立刻返回，主线程在训练期间保持可响应（模拟 UI 的渲染定时器）
  2. 快照持续更新，且拿到的是纯值（无 VTK/torch 对象）—— 跨线程传 actor 会崩
  3. 约束层真的串在回路里：事件计数 > 0，且指令确实被改过
  4. pause/resume 停在控制步边界，暂停期间快照不再前进
  5. stop() 能干净收尾：driver 排空、无孤儿进程、算例目录已清
  6. 训练确有推进：PPO 更新执行过，explained_var/vloss 有数
  7. checkpoint 能被 rviz_app.make_fastfarm_policy_fn 的口径加载
"""
import os
import subprocess
import sys
import threading
import time
import traceback

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

import torch                                                   # noqa: E402

from wfrl.scene.schema import Scene, SensorSpec, Turbine        # noqa: E402
from wfrl.studio.trainer import Snapshot, Trainer               # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CKPT = os.path.join(ROOT, "results", "checkpoints", "_probe_trainer.pt")
FAILS = []


def fastfarm_processes():
    """Return FAST.Farm processes on both Windows and Unix hosts."""
    cmd = (["tasklist"] if os.name == "nt"
           else ["pgrep", "-f", "[F]AST.Farm"])
    try:
        return subprocess.run(cmd, capture_output=True, text=True).stdout
    except FileNotFoundError:
        return ""


def check(name, cond, detail=""):
    ok = bool(cond)
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""),
          flush=True)
    if not ok:
        FAILS.append(name)


def scene():
    """与 D1 同一份非预注册布局，保证走的是场景层而不是预注册表。"""
    return Scene(
        name="probe_trainer", backend="fastfarm", dt=3.0,
        turbines=[Turbine("W1", 0.0, 0.0), Turbine("W2", 630.0, 126.0),
                  Turbine("W3", 1260.0, -126.0)],
        wind_speed=8.0, wind_direction=270.0, controls=["yaw"],
        sensors=[SensorSpec(type="power"), SensorSpec(type="actuator"),
                 SensorSpec(type="rotorspeed")],
    ).validate()


def has_vtk_or_torch(obj, depth=0):
    """快照里混进 VTK/torch 对象就是线程边界破了。"""
    if depth > 3:
        return False
    mod = type(obj).__module__ or ""
    if mod.startswith("vtk") or mod.startswith("torch") \
            or mod.startswith("pyvista"):
        return True
    if isinstance(obj, dict):
        return any(has_vtk_or_torch(v, depth + 1) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return any(has_vtk_or_torch(v, depth + 1) for v in obj)
    return False


def wait_for(tr, pred, timeout, gaps=None, period=0.033):
    """按 UI 渲染定时器的节奏轮询，等到 pred 成立。

    等待和卡顿测量必须是同一个循环：分开写就会像第一版那样，用 13 s 的窗口去
    等一个 spawn 要 25 s 的东西，然后断言"快照没推进" —— 测的是自己的耐心。
    返回 (是否等到, 实际耗时)。
    """
    t0 = tlast = time.time()
    while time.time() - t0 < timeout:
        time.sleep(period)
        now = time.time()
        if gaps is not None:
            gaps.append(now - tlast)
        tlast = now
        if pred():
            return True, now - t0
        if not tr.running:
            return pred(), now - t0
    return pred(), time.time() - t0


def main():
    sc = scene()
    print(f"== 场景 {sc.name}: x={sc.xcoords} ==", flush=True)
    tr = Trainer(sc, iters=2, n_steps=8, warmup_steps=2, mb_steps=4,
                 n_epochs=3, ckpt_path=CKPT)

    print("\n== 1. start() 不阻塞，主线程保持响应 ==", flush=True)
    t0 = time.time()
    tr.start()
    dt_start = time.time() - t0
    check("start() 立刻返回（< 0.5 s）", dt_start < 0.5, f"{dt_start:.3f} s")

    # 模拟 UI 的 33 ms 渲染定时器。训练若占着主线程，间隔会被拉到秒级。
    # spawn 一个 FAST.Farm 要 ~25 s，等第一帧采样数据的窗口必须给够。
    gaps = []
    ok, el = wait_for(tr, lambda: (tr.latest() or Snapshot()).phase == "sampling",
                      240, gaps)
    check("等到第一帧采样数据", ok, f"{el:.1f} s（含 FAST.Farm spawn）")
    ok4, el4 = wait_for(tr, lambda: (tr.latest() or Snapshot()).step >= 4,
                        120, gaps)
    check("快照持续推进到第 4 步", ok4,
          f"step={(tr.latest() or Snapshot()).step}，再等 {el4:.1f} s")
    worst = max(gaps) if gaps else 0.0
    check("主线程最大卡顿 < 0.3 s（一个控制步是 2~3 s）", worst < 0.3,
          f"最差 {worst * 1000:.0f} ms，共轮询 {len(gaps)} 次 / {sum(gaps):.0f} s")

    print("\n== 2. 快照是纯值，没有跨线程的 VTK/torch 对象 ==", flush=True)
    s = tr.latest()
    check("拿到快照", isinstance(s, Snapshot))
    check("frame 无 VTK/torch", not has_vtk_or_torch(s.frame))
    check("measure 无 VTK/torch", not has_vtk_or_torch(s.measure))
    check("功率是有限数", np.isfinite(s.farm_power), f"{s.farm_power:.3f} MW")
    check("measure 带位姿（渲染要用）",
          all(k in s.measure for k in ("yaw", "rotor_speed")),
          f"{sorted(s.measure)[:6]}")

    print("\n== 3. 约束层串在回路里 ==", flush=True)
    # 等到占空比累积起来。yaw 可持续量 0.09 °/步，策略动作量级 ±5° ⇒ 必然触发。
    deadline = time.time() + 90
    while tr.running and not tr.limiter.counts and time.time() < deadline:
        time.sleep(0.2)
    check("产生了约束事件", bool(tr.limiter.counts), f"{tr.limiter.summary()}")
    check("事件带机组 id 与规则名",
          all(e.turbine in [t.id for t in sc.turbines] and e.rule
              for e in tr.limiter.recent(5)))
    blocked = [e for e in tr.limiter.events if e.blocked]
    check("有 block 级事件（enforce_duty=True 会真改指令）", bool(blocked),
          f"{len(blocked)} 条")
    if blocked:
        print(f"    {blocked[0]}", flush=True)
        check("block 事件里 applied != requested（指令确实被改）",
              any(abs(e.applied - e.requested) > 1e-9 for e in blocked))

    print("\n== 4. 暂停停在控制步边界 ==", flush=True)
    tr.pause(True)
    time.sleep(0.5)
    a = tr.latest().step
    time.sleep(5.0)                     # 超过一个控制步（2~3 s）
    b = tr.latest().step
    check("暂停期间步数不前进（最多多走已在途的那一步）", b - a <= 1,
          f"{a} → {b}")
    check("暂停期间线程仍活着（不是崩了）", tr.running)
    tr.pause(False)
    time.sleep(6.0)
    c = tr.latest().step
    check("恢复后继续推进", c > b, f"{b} → {c}")

    print("\n== 5. 跑完并干净收尾 ==", flush=True)
    case_dir = tr.runtime.driver.case_dir if tr.runtime else None
    t0 = time.time()
    tr.stop(timeout=300)
    print(f"  stop() 耗时 {time.time() - t0:.1f} s", flush=True)
    check("线程已退出", not tr.running)
    check("训练线程无异常", tr.error is None, (tr.error or "")[-300:])
    if case_dir:
        check("算例目录已清", not os.path.exists(case_dir), case_dir)
    out = fastfarm_processes()
    check("无 FAST.Farm 孤儿进程", "FAST.Farm" not in out)

    print("\n== 6. PPO 更新确实执行过 ==", flush=True)
    check("有训练记录", bool(tr.history), f"{len(tr.history)} 轮")
    if tr.history:
        h = tr.history[-1]
        print(f"    {h}", flush=True)
        check("统计量齐全",
              all(k in h for k in ("pg", "vloss", "ent", "explained_var")))
        check("全部有限", all(np.isfinite(h[k]) for k in
                              ("pg", "vloss", "ent", "explained_var")))
        check("mean_power 是整段均值而非单帧",
              np.isfinite(h["mean_power"]) and h["mean_power"] > 0.5,
              f"{h['mean_power']:.3f} MW")

    print("\n== 7. checkpoint 与 rviz_app 的加载口径兼容 ==", flush=True)
    check("checkpoint 已落盘", os.path.exists(CKPT), CKPT)
    if os.path.exists(CKPT):
        ck = torch.load(CKPT, map_location="cpu", weights_only=False)
        need = ("actor", "critic", "obs_mean", "obs_var", "obs_keys",
                "obs_dim", "act_dim", "state_dim")
        check("含 rviz_app.make_fastfarm_policy_fn 要的全部键",
              all(k in ck for k in need),
              f"缺 {[k for k in need if k not in ck]}")
        check("state_dim = n × obs_dim", ck["state_dim"] == sc.n * ck["obs_dim"],
              f"{ck['state_dim']} vs {sc.n}×{ck['obs_dim']}")
        # 真加载一次：能构造出 Actor 并前向，才算口径兼容
        from wfrl.mappo_central import Actor
        ac = Actor(ck["obs_dim"], ck["act_dim"])
        ac.load_state_dict(ck["actor"])
        with torch.no_grad():
            y = ac.mu(torch.zeros(sc.n, ck["obs_dim"]))
        check("mappo_central.Actor 能加载并前向", tuple(y.shape) == (sc.n, 1),
              f"{tuple(y.shape)}")
        check("场景一并存进 checkpoint（能复现出布局）",
              ck.get("scene", {}).get("layout", [{}])[0].get("x") == 0.0)
        os.remove(CKPT)

    print()
    if FAILS:
        print(f"TRAINER_FAILED — {len(FAILS)} 项: {FAILS}")
        return 1
    print("TRAINER_OK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:                                          # noqa: BLE001
        traceback.print_exc()
        print("TRAINER_FAILED — 未捕获异常")
        sys.exit(1)
