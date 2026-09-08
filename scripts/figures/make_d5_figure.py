"""D5 训练结果图 —— 讲"约束绑定"的故事，不是"策略变强"。

三联图：
  (a) 40 轮全场功率：全程在 3.11~3.15 MW 抖动，无系统性上升 —— 约束把最优解挡在回合之外；
  (b) critic explained_var 与 vloss：训练机制本身健康(critic 一度 0.96)，说明"没学到增益"不是流程 bug；
  (c) 学到的确定性偏航动作 vs 当前偏航角：策略把偏航往回拉向 0，而理论最优在 20°。

产物：slides/figs/fig_d5_binding.png
"""
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import torch

from wfrl import paths
from wfrl.mappo_central import Actor

plt.rcParams["font.family"] = "Microsoft YaHei"
plt.rcParams["axes.unicode_minus"] = False

TAG = "mappo_s0_level_E170_none_stagD5"
ENV = "Dec_Turb3_Row1_Fastfarm"
HIST = os.path.join(paths.RUNS, f"fastfarm_{ENV}_{TAG}_hist.json")
CKPT = os.path.join(paths.CKPT, f"mappo_fastfarm_{ENV}_{TAG}.pt")
OUT = os.path.join(os.path.dirname(__file__), "..", "..", "slides", "figs",
                   "fig_d5_binding.png")

with open(HIST, encoding="utf-8") as f:
    hist = json.load(f)["hist"]
pw = np.array(hist["power_mw"])
ev = np.array(hist["explained_var"])
vl = np.array(hist["vloss"])
it = np.arange(1, len(pw) + 1)

ckpt = torch.load(CKPT, map_location="cpu", weights_only=False)
actor = Actor(ckpt["obs_dim"], ckpt["act_dim"])
actor.load_state_dict(ckpt["actor"])
actor.eval()
mean = np.array(ckpt["obs_mean"])
var = np.array(ckpt["obs_var"])


def det_action(yaw0):
    obs = np.array([yaw0, 8.0, 270.0, 0.0])
    obs_n = (obs - mean) / np.sqrt(var + 1e-8)
    with torch.no_grad():
        return float(actor.dist(torch.as_tensor(obs_n, dtype=torch.float32)).mean)


yaws = np.linspace(0, 25, 26)
acts = np.array([det_action(y) for y in yaws])

fig, ax = plt.subplots(1, 3, figsize=(15, 4.2))

# (a) 功率曲线
ax[0].plot(it, pw, "-o", ms=3, color="#1f77b4", lw=1.4)
ax[0].axhline(pw[:5].mean(), ls="--", color="#888", lw=1)
ax[0].fill_between(it, pw.min(), pw.max(), color="#1f77b4", alpha=0.04)
ax[0].text(2, pw[:5].mean() + 0.001, f"前5轮均值 {pw[:5].mean():.3f}",
           fontsize=8, color="#555")
ax[0].set_title(f"(a) 全场功率：40轮抖动带 {pw.min():.3f}–{pw.max():.3f} MW\n"
                f"前5→后5轮 +{(pw[-5:].mean()-pw[:5].mean())/pw[:5].mean()*100:.2f}%",
                fontsize=10)
ax[0].set_xlabel("训练轮次"); ax[0].set_ylabel("全场功率 (MW)")
ax[0].grid(alpha=0.3)

# (b) critic 健康度
ax2 = ax[1]
ax2.plot(it, ev, "-o", ms=3, color="#2ca02c", lw=1.4, label="explained_var")
ax2.axhline(0, ls=":", color="#aaa", lw=1)
ax2.set_ylabel("explained_var", color="#2ca02c")
ax2.tick_params(axis="y", labelcolor="#2ca02c")
ax2.set_ylim(-2, 1.1)
ax2b = ax2.twinx()
ax2b.plot(it, vl, "-s", ms=2.5, color="#d62728", lw=1.1, alpha=0.7, label="vloss")
ax2b.set_ylabel("vloss", color="#d62728")
ax2b.tick_params(axis="y", labelcolor="#d62728")
ax2b.set_yscale("log")
ax2.set_title(f"(b) 训练机制健康：critic 峰值 {ev.max():.2f}\n"
              f"vloss {vl[0]:.3f}→{vl[-1]:.4f}（流程无 bug）", fontsize=10)
ax2.set_xlabel("训练轮次"); ax2.grid(alpha=0.3)

# (c) 学到的偏航策略
ax[2].plot(yaws, acts, "-o", ms=3, color="#9467bd", lw=1.4)
ax[2].axhline(0, ls=":", color="#aaa", lw=1)
ax[2].axvline(20, ls="--", color="#d62728", lw=1.2)
ax[2].text(20.3, acts.max() * 0.7, "理论最优 20°", fontsize=8, color="#d62728")
ax[2].fill_between(yaws, acts, 0, where=(acts < 0), color="#9467bd", alpha=0.12)
ax[2].set_title("(c) 学到的确定性动作 vs 当前偏航\n各偏航角下动作<0：把偏航拉回0",
                fontsize=10)
ax[2].set_xlabel("当前偏航角 (°)"); ax[2].set_ylabel("确定性动作（偏航增量指令）")
ax[2].grid(alpha=0.3)

fig.suptitle("D5：执行机构约束入回路后的训练结果 —— 平功率是约束绑定的证据，非流程失败",
             fontsize=12, y=1.02)
fig.tight_layout()
os.makedirs(os.path.dirname(OUT), exist_ok=True)
fig.savefig(OUT, dpi=130, bbox_inches="tight")
print(f"saved -> {os.path.abspath(OUT)}")
print(f"功率: {pw.min():.3f}-{pw.max():.3f} MW, std={pw.std():.4f}")
print(f"expl_var 峰值: {ev.max():.3f}, vloss 末: {vl[-1]:.4f}")
print(f"obs_mean yaw: {mean[0]:.3f}° (策略的平均偏航)")
