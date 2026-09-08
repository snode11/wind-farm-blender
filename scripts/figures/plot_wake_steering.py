"""Plot yaw trajectory and power gain from rollout data"""
import numpy as np
import matplotlib.pyplot as plt
import matplotlib
matplotlib.rcParams['font.sans-serif'] = ['SimHei']  # Windows 中文
matplotlib.rcParams['axes.unicode_minus'] = False

# Load rollout data
data = np.load("results/runs/stagE400ref_rollout.npz")
yaw = data["yaw"]      # (400, 3)
power = data["power"]  # (400, 3)

steps = np.arange(len(yaw))
farm_power = power.sum(axis=1)

fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(10, 8), sharex=True)

# Plot 1: T1 yaw angle
ax1.plot(steps, yaw[:, 0], 'b-', linewidth=2, label='T1 (upstream)')
ax1.axhline(0, color='gray', linestyle='--', alpha=0.5)
ax1.set_ylabel('Yaw Angle (deg)', fontsize=12)
ax1.set_title('Wake Steering Policy: T1 Yaw Trajectory', fontsize=14, fontweight='bold')
ax1.legend()
ax1.grid(alpha=0.3)

# Plot 2: Individual turbine power
ax2.plot(steps, power[:, 0], 'b-', label='T1 (upstream)', alpha=0.7)
ax2.plot(steps, power[:, 1], 'g-', label='T2 (middle)', alpha=0.7)
ax2.plot(steps, power[:, 2], 'r-', label='T3 (downstream)', alpha=0.7)
ax2.set_ylabel('Power (MW)', fontsize=12)
ax2.set_title('Individual Turbine Power', fontsize=14)
ax2.legend()
ax2.grid(alpha=0.3)

# Plot 3: Farm total power vs baseline
baseline = 3.116  # Zero-yaw baseline (MW)
ax3.plot(steps, farm_power, 'k-', linewidth=2, label='RL Policy (learned)')
ax3.axhline(baseline, color='r', linestyle='--', linewidth=2, label=f'Baseline (zero yaw): {baseline} MW')
ax3.fill_between(steps, baseline, farm_power, where=(farm_power > baseline),
                 color='green', alpha=0.2, label='Gain region')
ax3.set_xlabel('Time Step', fontsize=12)
ax3.set_ylabel('Farm Power (MW)', fontsize=12)
ax3.set_title(f'Farm Power: +5.3% Gain (3.281 vs {baseline} MW)', fontsize=14, fontweight='bold')
ax3.legend()
ax3.grid(alpha=0.3)

plt.tight_layout()
plt.savefig("results/figures/wake_steering_demo.png", dpi=150, bbox_inches='tight')
print("Figure saved: results/figures/wake_steering_demo.png")
print(f"\nKey results (last 50 steps):")
print(f"  T1 yaw: {yaw[-50:, 0].mean():.1f} deg")
print(f"  Farm power: {farm_power[-50:].mean():.3f} MW")
print(f"  Gain: {(farm_power[-50:].mean() - baseline) / baseline * 100:.1f}%")
plt.show()
