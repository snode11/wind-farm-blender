"""训练用的奖励整形器 —— 用来替掉默认的 `StepPercentage`。

## 环境交给整形器的是什么

`multiagent_env.py:220-227`:

    normalized_powers = powers * 1e3 / (self.state()["freewind_measurements"][0] ** 3)
    reward = normalized_powers.mean() - self.load_coef * load_penalty
    reward = np.array([self.reward_shaper(reward)])

所以**原始 reward 本来就已经除过 u³**,量纲是 kW/(m/s)³,数值上就是训练脚本里
那条 `P/u^3` 曲线(实测 ≈3.3)。分母 `freewind_measurements[0]` 不是算例设定的
风速,而是 `interface.py:327` `upstream_point = np.argmax(speeds)` —— 全场逐机组
测速的**最大值**,即没被遮挡的那台。这一点很重要:它逐步跟随真实来流,不是常数。

但有两个已确认的缺陷:

1. **分母滞后一个控制步。** reward 在 `multiagent_env.py:226` 算,而
   `self._state = next_state` 在 228 行 —— 用的是**上一步**的来流。定常来流下
   无所谓;来流变化时,分子已经是 u_t³ 的功率、分母还是 u_{t-1}³,那一步的
   相对误差就是 (u_t/u_{t-1})³ − 1。
2. **`StepPercentage` 在这个已经归一化的量上再做一次逐步相对差分:**
   r̃_t = (r_t − r_{t−1}) / r_{t−1}。定常来流下 r_t 几乎不动,信号退化成噪声的
   相对差;来流一变,上面那个滞后失配被**整段放大**成一个与策略无关的大信号。
   README §6 的 "u 8→8.5 就产生 +19.9% 奖励" 就是这么来的(1.06³−1 = 19.1%)。

## 这里提供的替代

| 名字 | 形式 | 用途 |
|---|---|---|
| `level` | r | 直接用已归一化的量。跨来流可比,无差分放大 |
| `centered` | r − EMA(r) | 减掉慢变基线,零均值、量级稳定;EMA **跨回合保留** |
| `reference` | (r − r₀)/r₀ | r₀ 来自零偏航基线,数值即"相对基线的提升百分比" |
| `step` | (r_t−r_{t−1})/r_{t−1} | 原样保留 wfcrl 的 `StepPercentage`,只为对照 |

`centered` 的 EMA 之所以要跨回合保留:回合边界重新拉起 FAST.Farm、来流可能换档,
若每回合把基线清零,前 1/α 步的奖励全是"相对 0 的偏差",相当于给每个回合开头
塞一个与策略无关的巨大信号 —— 正是我们要修掉的那类污染。

诊断:每个整形器都记 `raw`/`shaped` 两串,`stats()` 给出均值/标准差,
`scripts/experiments/exp_reward_shaper.py` 用它量各整形器对来流变化的敏感度。
"""
import numpy as np

from wfcrl.rewards import RewardShaper, StepPercentage    # noqa: F401


class _Recording(RewardShaper):
    """公共部分:记原始值与整形后的值,供事后诊断。"""

    def __init__(self):
        self.raw = []
        self.shaped = []

    def _emit(self, raw, shaped):
        self.raw.append(float(raw))
        self.shaped.append(float(shaped))
        return float(shaped)

    def stats(self):
        r = np.asarray(self.raw, dtype=float)
        s = np.asarray(self.shaped, dtype=float)
        if r.size == 0:
            return {}
        return {"n": int(r.size),
                "raw_mean": float(r.mean()), "raw_std": float(r.std()),
                "shaped_mean": float(s.mean()), "shaped_std": float(s.std()),
                "shaped_absmax": float(np.abs(s).max())}


class Level(_Recording):
    """r 原样透传(可选整体缩放)。

    `wfcrl.rewards.DoNothingReward` 的显式版本,额外记录诊断。缩放只影响梯度
    步长的量级,不改变最优策略 —— 训练脚本另有 return 标准化,一般用 1.0。
    """

    def __init__(self, scale: float = 1.0):
        super().__init__()
        self.scale = float(scale)

    def __call__(self, reward):
        return self._emit(reward, self.scale * reward)


class Centered(_Recording):
    """r − EMA(r):减掉慢变基线,保留策略造成的偏差。

    alpha 是 EMA 的更新率,alpha=0.01 对应约 100 步的时间常数 —— 要比策略改变
    功率的时间尺度**慢**,否则基线会把策略的增益一起吃掉(基线追得越快,
    奖励越接近 0)。默认 0.01,在 64 步/轮的口径下一轮只更新掉 ~47%。

    `reset()` **不清空** EMA:回合边界清零会给每个回合开头塞一段与策略无关的
    大信号(见模块 docstring)。
    """

    def __init__(self, alpha: float = 0.01, scale: float = 1.0):
        super().__init__()
        self.alpha = float(alpha)
        self.scale = float(scale)
        self.ema = None

    def __call__(self, reward):
        if self.ema is None:
            self.ema = float(reward)              # 首步:基线即自身 ⇒ 奖励 0
        shaped = self.scale * (reward - self.ema)
        self.ema += self.alpha * (float(reward) - self.ema)
        return self._emit(reward, shaped)

    def reset(self, *_args, **_kw):
        pass                                       # 故意不动 self.ema


class FixedReference(_Recording):
    """(r − r₀)/r₀,r₀ 是**固定**的零偏航基线值。

    与 `wfcrl.rewards.ReferencePercentage` 同式,区别是这里的 r₀ 必须由匹配来流下
    的零偏航实测给出(`--reward-ref`),不是随手填的数。好处是奖励的物理含义直接
    是"相对基线提升百分比",训练曲线和最终评估指标同口径。
    """

    def __init__(self, reference: float):
        super().__init__()
        if not np.isfinite(reference) or abs(reference) < 1e-9:
            raise ValueError(f"reference 必须是有限非零值,收到 {reference!r}")
        self.reference = float(reference)

    def __call__(self, reward):
        return self._emit(reward, (reward - self.reference) / self.reference)


def make_shaper(name, reference=None, alpha=0.01, scale=1.0):
    """名字 → 整形器实例。未知名字抛错并列出可选。"""
    name = (name or "level").lower()
    if name == "level":
        return Level(scale=scale)
    if name == "centered":
        return Centered(alpha=alpha, scale=scale)
    if name == "reference":
        if reference is None:
            raise SystemExit(
                "--reward reference 需要同时给 --reward-ref R0(匹配来流下的"
                "零偏航基线 P/u^3,可用 --policy zero 先跑一遍拿到)")
        return FixedReference(reference)
    if name == "step":
        return StepPercentage()
    raise SystemExit(f"未知奖励整形器 {name!r}。可选:level / centered / "
                     f"reference / step")


CHOICES = ("level", "centered", "reference", "step")
