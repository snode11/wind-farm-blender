"""内置风况预设与按回合索引可复现的调度。

FAST.Farm 的风况校验和记录合同见 docs/training/FASTFarm风况.md。
预设方向使用气象学风向（270°为西风），由实际后端在回合开始时应用。
FAST.Farm 通过平地布局旋转实现方向；标量风速由驱动校验并记录。
"""

from numbers import Integral
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
        wind_speed=8.0, wind_direction=240.0, turb=None, floris_only=False,
        label="中风 8 m/s · 风向 240°",
        note="斜吹。尾流不再沿机组连线，下游机组部分脱离尾流。"
             "FAST.Farm 在回合边界旋转布局，运行中途不改变方向。"),
    "turb": dict(
        wind_speed=None, wind_direction=None, turb="90m_08mps.bts",
        floris_only=False,
        label="湍流盒子 8 m/s TI 9%",
        note="TurbSim WindType=3。来流由 .bts 决定，接口拒绝标量风速请求。"
             "文件时长与回合预算由接口核对，超时长只记录警告。"),
}

ORDER = ["calm", "rated", "strong", "gale", "veer", "turb"]


def resolve_turbulence(name=None, *, wind_speed=None, ti=None, seed=None, library_dir=None):
    """选择已有 BTS，返回可传给驱动或场景 turbulence 的绝对路径。

    TI 使用百分数（8 表示 8%）或 IEC 类别 A/B/C；不在 reset 时生成风场。
    """
    from wfrl.inflow import resolve_bts

    if name is not None:
        if any(value is not None for value in (wind_speed, ti, seed)):
            raise ValueError("湍流名称与 (wind_speed, ti, seed) 不能同时指定")
        selection = name
    else:
        selection = (wind_speed, ti, seed)
    return resolve_bts(selection, library_dir=library_dir)

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

    两个后端均保留风向；实际算例的覆盖范围由后端校验。
    """
    _check_backend(backend)
    if not isinstance(name, str) or name not in PRESETS:
        raise ValueError(f"未知风况 {name!r}。可选：\n{describe()}")
    p = PRESETS[name]
    wd = p["wind_direction"]
    return p["wind_speed"], wd, p["turb"]


def _check_backend(backend):
    if not isinstance(backend, str) or backend not in ("fastfarm", "floris"):
        raise ValueError(f"未知风况后端 {backend!r}，可选 fastfarm / floris")


def _nonnegative_integer(value, name):
    if isinstance(value, (bool, _np.bool_)) or not isinstance(value, Integral) or value < 0:
        raise ValueError(f"{name} 必须为非负整数")
    return int(value)


def _indexed_schedule(sample):
    def schedule(episode_index):
        return sample(_nonnegative_integer(episode_index, "episode_index"))
    return schedule


def _positive_wind_speed(value):
    speed = float(value)
    if not _np.isfinite(speed) or speed <= 0:
        raise ValueError("风速必须为有限正数，单位 m/s")
    return speed


# ---------------------------------------------------------------------------
# 逐回合的来流调度 —— 训练用
# ---------------------------------------------------------------------------
def make_wind_schedule(spec, seed=0, backend="fastfarm"):
    """`spec` 字符串 → 函数 `f(episode_index) -> (wind_speed, wind_direction)`。

      pin:8        每回合都钉 8 m/s
      preset:calm  用 PRESETS 里的一档,同样恒定
      cycle:6,8,10 按回合序号循环(确定性的变风况,便于复现)
      weibull      每回合抽 8·Weibull(8) —— 与 `mdp.py:239` 的 benchmark 分布同式,
                   用调度自己的 seed 与回合索引确定结果
      none         不指定风速，交给 mdp 抽样

    回合索引从 0 开始，不设回合总数或时长。preset 的风向由 resolve 决定；
    其他调度不指定风向。显式风速须为有限正数，不按抽样边界裁剪。
    此二元返回接口不传递 BTS 文件选择。
    """
    _check_backend(backend)
    if spec is not None and not isinstance(spec, str):
        raise ValueError("来流调度 spec 必须为字符串")
    spec = (spec or "pin:8").strip()
    head, _, arg = spec.partition(":")
    head = head.lower()

    if head == "none":
        if arg:
            raise ValueError("none 调度不接受参数")
        return _indexed_schedule(lambda ep: (None, None))

    if head == "pin":
        u = _positive_wind_speed(arg)
        return _indexed_schedule(lambda ep: (u, None))

    if head == "preset":
        ws, wd, _turb = resolve(arg, backend=backend)
        return _indexed_schedule(lambda ep: (ws, wd))

    if head == "cycle":
        if not arg or any(not x.strip() for x in arg.split(",")):
            raise ValueError("cycle: 后面要给非空风速列表，例如 cycle:6,8,10")
        us = [_positive_wind_speed(x) for x in arg.split(",")]
        return _indexed_schedule(lambda ep: (us[ep % len(us)], None))

    if head == "weibull":
        # 每个回合用 seed+ep 独立起 rng：中途中断续跑也能对齐同一序列
        base = _nonnegative_integer(int(arg) if arg else seed, "seed")

        def _f(ep):
            rng = _np.random.default_rng(base * 100003 + ep)
            u = float(_np.clip(8 * rng.weibull(8), *WS_BOUNDS))
            return (u, None)
        return _indexed_schedule(_f)

    raise ValueError(f"未知来流调度 {spec!r}。可选:pin:U / preset:NAME / "
                     f"cycle:U1,U2,... / weibull[:SEED] / none")
