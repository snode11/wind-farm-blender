"""Level vs Reference 奖励对比图 —— 讲"持续回报"解锁 wake steering 的故事。

三联图：
  (a) T1 偏航轨迹：reference 学到 35°，level 拉回 0°
  (b) 瞬时全场功率：reference 持续 +5%，level 回到基线
  (c) 三机组偏航分布：reference 只让 T1 偏航（最优策略），level 全员接近 0°

产物：slides/figs/fig_level_vs_ref.png
"""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams["font.family"] = "Microsoft YaHei"
plt.rcParams["axes.unicode_minus"] = False

LEVEL_NPZ = "results/runs/stagE400_rollout.npz"
REF_NPZ = "results/runs/stagE400ref_rollout.npz"
OUT = "slides/figs/fig_level_vs_ref.png"

level = np.load(LEVEL_NPZ)
ref = np.load(REF_NPZ)

yaw_level = level["yaw"]  # (400, 3)
pow_level = level["power"]  # (400, 3)
yaw_ref = ref["yaw"]
pow_ref = ref["power"]

steps = np.arange(len(yaw_level))

fig, ax = plt.subplots(1, 3, figsize=(15, 4.5))

# (a) T1 偏航轨迹对比
ax[0].plot(steps, yaw_level[:, 0], "-", lw=1.8, color="#1f77b4", label="level 奖励", alpha=0.85)
ax[0].plot(steps, yaw_ref[:, 0], "-", lw=1.8, color="#d62728", label="reference 奖励")
ax[0].axhline(20, ls="--", color="#2ca02c", lw=1.2, alpha=0.7)
ax[0].text(320, 21.5, "FLORIS 最优 ~20°", fontsize=8, color="#2ca02c")
ax[0].axhline(36, ls=":", color="#888", lw=1, alpha=0.6)
ax[0].text(320, 37.5, "占空比上限 ~36°", fontsize=7, color="#555")
ax[0].fill_between(steps, 0, yaw_ref[:, 0], color="#d62728", alpha=0.08)
ax[0].set_title("(a) 上游机组(T1)偏航轨迹\nreference 学到激进偏航，level 拉回 0°", fontsize=10)
ax[0].set_xlabel("时间步"); ax[0].set_ylabel("T1 偏航角 (°)")
ax[0].legend(loc="upper left", fontsize=9)
ax[0].grid(alpha=0.3)
ax[0].set_xlim(0, 400); ax[0].set_ylim(-2, 40)

# (b) 瞬时全场功率对比
farm_pow_level = pow_level.sum(axis=1)
farm_pow_ref = pow_ref.sum(axis=1)
baseline = 3.11  # 零偏航基线 (从 reference 训练日志)

ax[1].plot(steps, farm_pow_level, "-", lw=1.4, color="#1f77b4", label="level", alpha=0.8)
ax[1].plot(steps, farm_pow_ref, "-", lw=1.6, color="#d62728", label="reference")
ax[1].axhline(baseline, ls="--", color="#888", lw=1.2)
ax[1].text(10, baseline - 0.03, f"零偏航基线 {baseline:.2f} MW", fontsize=8, color="#555")
ax[1].fill_between(steps, baseline, farm_pow_ref, where=(farm_pow_ref > baseline),
                    color="#d62728", alpha=0.12, label="reference 增益区")
ax[1].set_title(f"(b) 瞬时全场功率\nreference 末50步均值 {farm_pow_ref[-50:].mean():.3f} MW (+{(farm_pow_ref[-50:].mean()-baseline)/baseline*100:.1f}%)\n"
                f"level 末50步 {farm_pow_level[-50:].mean():.3f} MW ({(farm_pow_level[-50:].mean()-baseline)/baseline*100:+.1f}%)",
                fontsize=10)
ax[1].set_xlabel("时间步"); ax[1].set_ylabel("全场功率 (MW)")
ax[1].legend(loc="lower right", fontsize=9)
ax[1].grid(alpha=0.3)
ax[1].set_xlim(0, 400); ax[1].set_ylim(2.9, 3.4)

# (c) 三机组末50步偏航分布（箱线图）
last50_level = yaw_level[-50:, :]
last50_ref = yaw_ref[-50:, :]

positions_level = [1, 2, 3]
positions_ref = [1.3, 2.3, 3.3]

bp1 = ax[2].boxplot([last50_level[:, i] for i in range(3)], positions=positions_level,
                     widths=0.25, patch_artist=True,
                     boxprops=dict(facecolor="#1f77b4", alpha=0.6),
                     medianprops=dict(color="#000", lw=1.5),
                     whiskerprops=dict(color="#1f77b4"),
                     capprops=dict(color="#1f77b4"))
bp2 = ax[2].boxplot([last50_ref[:, i] for i in range(3)], positions=positions_ref,
                     widths=0.25, patch_artist=True,
                     boxprops=dict(facecolor="#d62728", alpha=0.6),
                     medianprops=dict(color="#000", lw=1.5),
                     whiskerprops=dict(color="#d62728"),
                     capprops=dict(color="#d62728"))

ax[2].axhline(0, ls=":", color="#aaa", lw=1)
ax[2].set_xticks([1.15, 2.15, 3.15])
ax[2].set_xticklabels(["T1 (上游)", "T2 (中游)", "T3 (下游)"])
ax[2].set_title("(c) 末50步偏航分布（三机组）\nreference: 只让 T1 激进偏航（最优）\nlevel: 全员接近 0°", fontsize=10)
ax[2].set_ylabel("偏航角 (°)")
ax[2].legend([bp1["boxes"][0], bp2["boxes"][0]], ["level", "reference"],
             loc="upper left", fontsize=9)
ax[2].grid(alpha=0.3, axis="y")
ax[2].set_ylim(-6, 38)

# 标注 reference 的 T1 中位数
t1_ref_median = np.median(last50_ref[:, 0])
ax[2].text(1.5, t1_ref_median, f"{t1_ref_median:.1f}°", fontsize=8, color="#d62728",
           va="center", fontweight="bold")

fig.suptitle("Level vs Reference 奖励：持续回报解锁 wake steering",
             fontsize=13, y=1.00, fontweight="bold")
fig.tight_layout()
os.makedirs(os.path.dirname(OUT), exist_ok=True)
fig.savefig(OUT, dpi=140, bbox_inches="tight")
print(f"saved -> {os.path.abspath(OUT)}")
print(f"\nLevel (差分奖励):")
print(f"  T1 末50步均值: {last50_level[:, 0].mean():.2f}° (范围 [{last50_level[:, 0].min():.1f}, {last50_level[:, 0].max():.1f}])")
print(f"  全场功率: {farm_pow_level[-50:].mean():.3f} MW ({(farm_pow_level[-50:].mean()-baseline)/baseline*100:+.2f}%)")
print(f"\nReference (相对基线):")
print(f"  T1 末50步均值: {last50_ref[:, 0].mean():.2f}° (范围 [{last50_ref[:, 0].min():.1f}, {last50_ref[:, 0].max():.1f}])")
print(f"  全场功率: {farm_pow_ref[-50:].mean():.3f} MW (+{(farm_pow_ref[-50:].mean()-baseline)/baseline*100:.2f}%)")
print(f"  增益: {(farm_pow_ref[-50:].mean() - farm_pow_level[-50:].mean())*1000:.1f} kW")
