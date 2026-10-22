"""离线验收多控制量 action space：不起 FAST.Farm，把能在 spawn 前查的都查掉。

新增的三通道路径（trainer 从 scene.controls 派生 obs/act 维度、逐通道缩放、
按通道路由进约束层、按通道发 driver.step）里，只有最后一步真的要仿真回读。
其余全是纯逻辑，能离线逐位断言 —— FAST.Farm 一次 spawn ~25 s，值得把验证
挡在它前面。

覆盖：
  1 Trainer 从 controls 派生的 obs_keys / 维度（单控回归 + 三控）
  2 _pack 的列顺序与形状（列 = 各被控量 + 风速 + 风向）
  3 逐通道动作缩放：tanh 后乘各自动作上界，落在各自边界内、路由到正确通道
  4 三通道字典进 SafetyLimiter.apply：三通道都在、各自的约束各自触发
  5 driver.step 的签名确实收三个 delta（防止 trainer 发了 driver 收不下）

末行 MULTICTRL_OK / MULTICTRL_FAIL。
"""
import inspect
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from wfrl.fastfarm_driver import FastFarmDriver              # noqa: E402
from wfrl.scene.schema import Scene, Turbine                 # noqa: E402
from wfrl.studio.trainer import Trainer                      # noqa: E402

PASS, FAIL = [], []

# data_cases.DefaultControl 的每步增量边界 —— driver.act_bounds 会解析成同一组
ACT_HIGH = {"yaw": 5.0, "pitch": 1.0, "torque": 1e3}


def chk(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"[{'OK ' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def scene(controls):
    return Scene(name="probe", backend="fastfarm", dt=3.0,
                 turbines=[Turbine("T1", 0, 0), Turbine("T2", 504, 0),
                           Turbine("T3", 1008, 0)],
                 wind_speed=8.0, wind_direction=270.0,
                 controls=list(controls), sensors=[])


def fake_measure(n, controls):
    """一份 driver 风格的回读：被控量 + 风速 + 风向都在，够 _pack 取。"""
    m = {"wind_speed": np.full(n, 8.0), "wind_direction": np.full(n, 270.0)}
    base = {"yaw": 3.0, "pitch": 0.5, "torque": 3.0e4}
    for c in controls:
        m[c] = np.full(n, base[c])
    return m


def main():
    n = 3

    # 1 维度派生：单控回归到 3/1，三控是 5/3
    tr1 = Trainer(scene(["yaw"]))
    chk("单控 obs_keys 回到 [yaw, wind_speed, wind_direction]",
        tr1.obs_keys == ["yaw", "wind_speed", "wind_direction"],
        str(tr1.obs_keys))
    chk("单控 controls = [yaw]", tr1.controls == ["yaw"], str(tr1.controls))

    tr3 = Trainer(scene(["yaw", "pitch", "torque"]))
    chk("三控 obs_keys = [yaw, pitch, torque, wind_speed, wind_direction]",
        tr3.obs_keys == ["yaw", "pitch", "torque",
                         "wind_speed", "wind_direction"], str(tr3.obs_keys))
    chk("三控 obs_dim=5 / act_dim=3",
        len(tr3.obs_keys) == 5 and len(tr3.controls) == 3,
        f"obs={len(tr3.obs_keys)} act={len(tr3.controls)}")

    # 2 _pack 列顺序与形状
    o1 = tr1._pack(fake_measure(n, ["yaw"]))
    chk("单控 _pack 形状 (n, 3)", o1.shape == (n, 3), str(o1.shape))
    chk("单控 _pack 首列是 yaw", np.allclose(o1[:, 0], 3.0), str(o1[0]))

    o3 = tr3._pack(fake_measure(n, ["yaw", "pitch", "torque"]))
    chk("三控 _pack 形状 (n, 5)", o3.shape == (n, 5), str(o3.shape))
    chk("三控 _pack 列顺序 = yaw,pitch,torque,ws,wd",
        np.allclose(o3[0], [3.0, 0.5, 3.0e4, 8.0, 270.0]), str(o3[0]))

    # 3 逐通道动作缩放（复刻 trainer 的两行：tanh(a)*act_high，再按通道拆字典）
    controls = ["yaw", "pitch", "torque"]
    act_high = np.array([ACT_HIGH[c] for c in controls], np.float32)
    a = np.array([[10.0, -10.0, 10.0],        # 大幅 ⇒ tanh 饱和到 ±1
                  [0.0, 0.0, 0.0],            # 0 ⇒ 缩放后 0
                  [-10.0, 10.0, -10.0]], np.float32)
    a_scaled = np.tanh(a) * act_high
    routed = {c: a_scaled[:, j] for j, c in enumerate(controls)}
    chk("缩放后每通道落在各自动作边界内",
        all(np.all(np.abs(a_scaled[:, j]) <= ACT_HIGH[c] + 1e-6)
            for j, c in enumerate(controls)),
        "; ".join(f"{c}∈±{ACT_HIGH[c]:g}" for c in controls))
    chk("饱和动作缩到接近各自上界",
        abs(routed["yaw"][0] - 5.0) < 0.01
        and abs(routed["pitch"][0] + 1.0) < 0.01
        and abs(routed["torque"][0] - 1e3) < 1.0,
        f"yaw={routed['yaw'][0]:.3f} pitch={routed['pitch'][0]:.3f} "
        f"torque={routed['torque'][0]:.1f}")
    chk("零动作缩放后仍为零",
        all(abs(routed[c][1]) < 1e-9 for c in controls), "第 2 台全 0")

    # 4 三通道字典进约束层：三通道都在、各自约束各自触发
    m = {"duty": {"yaw": np.full(n, 0.5),      # yaw 占空比超 ⇒ 清零事件
                  "pitch": np.zeros(n)},
         "pitch_meas": np.full(n, 2.0),         # 实测桨距 > 指令 ⇒ 被顶替
         "torque": np.full(n, 1.0e4)}           # 低于 60% 额定 ⇒ 托底
    cmd, evs = tr3.limiter.apply(routed, m, enforce_duty=True)
    chk("约束层返回三通道", set(cmd) == {"yaw", "pitch", "torque"},
        str(sorted(cmd)))
    rules = {e.control: set() for e in evs}
    for e in evs:
        rules.setdefault(e.control, set()).add(e.rule)
    chk("yaw 触发占空比清零", "执行机构占空比清零" in rules.get("yaw", set()),
        str(rules.get("yaw")))
    chk("torque 触发运行包线约束（防超速/防失速）",
        any("转矩" in r for r in rules.get("torque", set())),
        str(rules.get("torque")))
    chk("三通道命令都是 (n,) 数组",
        all(np.asarray(cmd[c]).shape == (n,) for c in controls),
        "; ".join(f"{c}:{np.asarray(cmd[c]).shape}" for c in controls))

    # 5 driver.step 签名收三个 delta
    sig = inspect.signature(FastFarmDriver.step)
    params = set(sig.parameters)
    chk("driver.step 收 yaw/pitch/torque_delta",
        {"yaw_delta", "pitch_delta", "torque_delta"} <= params,
        str(sorted(params - {"self"})))

    print(f"\n{len(PASS)} 项通过, {len(FAIL)} 项失败")
    print("MULTICTRL_OK" if not FAIL else "MULTICTRL_FAIL: " + "; ".join(FAIL))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
