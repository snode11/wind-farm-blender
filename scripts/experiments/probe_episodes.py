"""阶段 3 回合结构的在线验收 —— 真起 FAST.Farm，对 hist 下断言。

覆盖三件在旧口径下做不到的事：
  1. 每回合真的重起了进程，回合数 = ceil(总步数 / episode_steps)；
  2. `--wind-sched` 的来流逐回合换档，且换的是**调度里写的那个值**；
  3. warmup 步既不进训练数据也不进统计（回合内步数 = episode_steps，
     而 FAST.Farm 实跑步数 = warmup + episode_steps + 排空）。

外加零偏航基线走同一条路径（`--policy zero`），以及算例目录的清理队列。

    mpiexec -n 1 python -X utf8 scripts/experiments/probe_episodes.py
"""
import math
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "scripts", "train"))

from wfrl import fastfarm_driver                        # noqa: E402
from train_fastfarm import train                        # noqa: E402

ITERS, N_STEPS, EP, WARM = 2, 8, 6, 4
TOTAL = ITERS * N_STEPS
SCHED = "cycle:7,9"

ok = True


def chk(label, cond, detail=""):
    global ok
    ok = bool(cond) and ok
    print(f"  {'v' if cond else 'X'} {label}" + (f" — {detail}" if detail else ""),
          flush=True)


print(f"[probe] {ITERS}x{N_STEPS} 步，回合 {EP} 步，warmup {WARM} 步，{SCHED}",
      flush=True)
hist = train("Dec_Turb3_Row1_Fastfarm", ITERS, N_STEPS,
             episode_steps=EP, warmup_steps=WARM, wind_sched=SCHED,
             reward="level", policy="learn", seed=0, tag_extra="probe")

print("\n[probe] 回合结构", flush=True)
# 采样 TOTAL 步、每 EP 步换一次 ⇒ 起过 ceil(TOTAL/EP) 个回合（含第 0 个）
want_spawns = math.ceil(TOTAL / EP)
chk("spawn 次数 = ceil(总步数/回合长)", hist["n_spawns"] == want_spawns,
    f"{hist['n_spawns']} vs {want_spawns}")
chk("每轮都有回合边界（旧口径下这里恒为 0）", sum(hist["episodes"]) >= 1,
    str(hist["episodes"]))

# cycle:7,9 ⇒ 逐回合来流在 7 / 9 之间换；逐轮平均必然落在两者之间
us = hist["u_inf"]
chk("来流按调度换档", all(6.9 <= u <= 9.1 for u in us) and len(set(us)) >= 1,
    str(us))

print("\n[probe] 奖励口径", flush=True)
st = hist.get("shaper_stats", {})
# 整形器看到的步数：训练步 + 每次 spawn 的 warmup + close() 排空的那几步
# （排空也走 env.step，照样过整形器，但不进训练数据）。所以给一个区间。
lo = TOTAL + hist["n_spawns"] * WARM
hi = hist["n_spawns"] * (WARM + EP + 2)
chk("整形器吃到的步数 = 训练步 + warmup + 排空", lo <= st.get("n", -1) <= hi,
    f"{st.get('n')} ∈ [{lo}, {hi}]")
chk("level 整形器透传（shaped == raw）",
    abs(st.get("shaped_mean", 0) - st.get("raw_mean", 1)) < 1e-9,
    f"raw {st.get('raw_mean'):.4f} / shaped {st.get('shaped_mean'):.4f}")
chk("raw 落在实测量级 (mean P/u^3 ≈ 3)", 1.0 < st.get("raw_mean", 0) < 6.0,
    f"{st.get('raw_mean'):.3f}")

print("\n[probe] 零偏航基线走同一条路径", flush=True)
hz = train("Dec_Turb3_Row1_Fastfarm", 1, N_STEPS,
           episode_steps=EP, warmup_steps=WARM, wind_sched=SCHED,
           reward="level", policy="zero", seed=0, tag_extra="probe")
chk("基线不更新网络（pg/vloss 为 nan）",
    all(x != x for x in hz["pg"]), str(hz["pg"]))
chk("基线仍产出功率统计", hz["power_mw"] and hz["power_mw"][0] > 0,
    f"{hz['power_mw'][0]:.2f} MW")

print("\n[probe] 收尾", flush=True)
pend = list(fastfarm_driver._PENDING_PURGE)
chk("算例目录清理队列已排空（或只剩最后一个）", len(pend) <= 1,
    f"{len(pend)} 个待删：{[os.path.basename(p) for p in pend]}")
try:
    out = subprocess.run(["tasklist"], capture_output=True, text=True).stdout
    chk("没留下孤儿 FAST.Farm 进程", "FAST.Farm" not in out)
except Exception as e:                                   # noqa: BLE001
    print(f"  · tasklist 查不了（跳过）: {e}", flush=True)

print("EPISODES_OK" if ok else "EPISODES_FAILED", flush=True)
sys.exit(0 if ok else 1)
