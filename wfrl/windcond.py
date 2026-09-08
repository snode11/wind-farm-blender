"""内置风况序列 —— 让演示能在"小风/大风"之间一键切换，而不是每次背参数。

为什么需要这个：功率 ∝ u³，而 `mdp.reset` 默认每次重抽 `8*rng.weibull(8)`。
不钉死来流的话，两次运行的差别主要来自抽签而不是控制策略（见 memory:
wfcrl-inflow-control）。所以每个预设都**显式给定** wind_speed，且带上这一档
应该看到什么现象，方便对着画面讲。

变桨的可观测性是分档的，这一点决定了演示时该说什么：
  NREL-5MW 额定风速 11.4 m/s。额定以下基线控制器把桨距贴在 0° 附近跑（此时
  最大化 Cp 靠的是变转速而不是变桨），额定以上才靠顺桨限功率。
  ServoDyn 模板是 `PCMode=5`/`VSContrl=5`（Bladed 风格 DLL），基线变桨确实在工作。
  而 WFCRL 的 pitch 通道是 **PitchRef=下界**（只能比基线更顺桨，见 memory:
  wfcrl-control-channels），所以：
    - 小风档：基线 ~0°，外部指令**完全可见**，画面上叶片明显扭转
    - 大风档：基线自己就在变桨，外部小角度指令会被 max() 吃掉、看不出来
  这不是 bug，是控制律语义。演示大风档时讲的是"基线在顺桨限功率"。

**风向只有 FLORIS 后端能改。** `interface.py:437-441`：FastFarmInterface 收到
wind_direction 会 warn 后忽略（"cannot set wind direction in the simulator"）。
风速两个后端都能钉：FAST.Farm 走 `write_inflow_info` 把 HWindSpeed 写进
InflowWind 再 spawn exe（`interface.py:443-446`）。所以 `veer` 档标了 floris_only。
"""

import numpy as _np

PRESETS = {
    "calm": dict(
        wind_speed=5.0, wind_direction=270.0, turb=None, floris_only=False,
        label="小风 5 m/s",
        note="额定以下深处。基线桨距贴 0°，外部变桨指令完全可见；"
             "转速低、尾流亏损相对明显。"),
    "rated": dict(
        wind_speed=8.0, wind_direction=270.0, turb=None, floris_only=False,
        label="中风 8 m/s",
        note="额定以下，与湍流盒子同风速，便于和 turb 档对照。变桨仍可见。"),
    "strong": dict(
        wind_speed=14.0, wind_direction=270.0, turb=None, floris_only=False,
        label="大风 14 m/s",
        note="额定(11.4)以上。基线控制器主动顺桨限功率，桨距明显不为 0；"
             "外部 PitchRef 是下界，小角度指令会被基线盖过 —— "
             "这一档看的是基线变桨行为本身。"),
    "gale": dict(
        wind_speed=20.0, wind_direction=270.0, turb=None, floris_only=False,
        label="强风 20 m/s",
        note="远超额定，顺桨角度大、载荷高。看桨距和叶根弯矩的量级。"),
    "veer": dict(
        wind_speed=8.0, wind_direction=240.0, turb=None, floris_only=True,
        label="中风 8 m/s · 风向 240°",
        note="斜吹。尾流不再沿机组连线，下游机组部分脱离尾流。"
             "**仅 FLORIS 后端**：FAST.Farm 改不了风向，会被 warn 后忽略。"),
    "turb": dict(
        wind_speed=None, wind_direction=None, turb="90m_08mps.bts",
        floris_only=False,
        label="湍流盒子 8 m/s TI 9%",
        note="TurbSim WindType=3。来流由 .bts 决定，钉风速的请求会被忽略。"
             "盒子只有 200 s ⇒ --max-steps 不要超过 60。"),
}

ORDER = ["calm", "rated", "strong", "gale", "veer", "turb"]

# `mdp.py:46` 的状态空间边界。抽样风速必须裁到这里,和 benchmark 的 reset 同口径。
WS_BOUNDS = (3.0, 28.0)


def describe():
    """给 --help 和启动日志用的一段说明。"""
    w = max(len(k) for k in ORDER)
    lines = []
    for k in ORDER:
        p = PRESETS[k]
        u = f"{p['wind_speed']:.0f} m/s" if p["wind_speed"] else "由 .bts 定"
        tag = " [仅 floris]" if p["floris_only"] else ""
        lines.append(f"  {k:<{w}}  {u:>10}   {p['label']}{tag}")
    return "\n".join(lines)


def resolve(name, backend="fastfarm"):
    """名字 → (wind_speed, wind_direction, turb)；未知名字抛错并列出可选。

    FAST.Farm 下把 wind_direction 置 None：留着它只会换来一条 wfcrl 的 warning，
    而调用方会以为自己钉住了风向。
    """
    if name not in PRESETS:
        raise SystemExit(f"未知风况 {name!r}。可选：\n{describe()}")
    p = PRESETS[name]
    wd = p["wind_direction"]
    if backend != "floris" and wd is not None:
        print(f"[风况] {name}: FAST.Farm 改不了风向，{wd:.0f}° 的请求已忽略"
              f"（风速仍然生效）", flush=True)
        wd = None
    return p["wind_speed"], wd, p["turb"]


# ---------------------------------------------------------------------------
# 逐回合的来流调度 —— 训练用
# ---------------------------------------------------------------------------
def make_wind_schedule(spec, seed=0, backend="fastfarm"):
    """`spec` 字符串 → 函数 `f(episode_index) -> (wind_speed, wind_direction)`。

    阶段 3 之前训练全程只 reset 一次,等于**一次抽签**决定整段的来流(见
    memory: wfcrl-inflow-control)。有了真回合结构以后,"每回合换什么风"就成了
    一个必须显式声明的实验设定,所以做成可复现的调度而不是让 mdp 随手抽。

      pin:8        每回合都钉 8 m/s(受控对照,阶段 3/4 用这个)
      preset:calm  用 PRESETS 里的一档,同样恒定
      cycle:6,8,10 按回合序号循环(确定性的变风况,便于复现)
      weibull      每回合抽 8·Weibull(8) —— 与 `mdp.py:239` 的 benchmark 分布同式,
                   但用**本调度自己的 seed**,可复现;阶段 5 变风况训练用
      none         不钉,交给 mdp 自己抽(不可复现,只为复现旧口径)

    风向一律返回 None:FAST.Farm 侧改不了(`interface.py:437-441`),留着只会换来
    一条 warning 而调用方以为钉住了。FLORIS 侧要钉风向请直接用 preset。
    """
    spec = (spec or "pin:8").strip()
    head, _, arg = spec.partition(":")
    head = head.lower()

    if head == "none":
        return lambda ep: (None, None)

    if head == "pin":
        u = float(arg)
        return lambda ep: (u, None)

    if head == "preset":
        ws, wd, _turb = resolve(arg, backend=backend)
        return lambda ep: (ws, wd)

    if head == "cycle":
        us = [float(x) for x in arg.split(",") if x.strip()]
        if not us:
            raise SystemExit("cycle: 后面要给至少一个风速,例如 cycle:6,8,10")
        return lambda ep: (us[ep % len(us)], None)

    if head == "weibull":
        # 每个回合用 seed+ep 独立起 rng：中途中断续跑也能对齐同一序列
        base = int(arg) if arg else int(seed)

        def _f(ep):
            rng = _np.random.default_rng(base * 100003 + ep)
            u = float(_np.clip(8 * rng.weibull(8), *WS_BOUNDS))
            return (u, None)
        return _f

    raise SystemExit(f"未知来流调度 {spec!r}。可选:pin:U / preset:NAME / "
                     f"cycle:U1,U2,... / weibull[:SEED] / none")
