"""Demo mode: Manual control sequence showing yaw/pitch changes and wake deflection

演示序列：
- 第 1 循环：静止(90°) → 启动(0°) → 偏航(0→30°) → 停机(90°)
- 第 2 循环：重复
"""
import sys
import os
import numpy as np
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from wfrl.scene.schema import load_scene
from wfrl.scene.runtime import SceneRuntime

def demo_sequence():
    """演示序列：2 个完整循环"""
    scene = load_scene("scenes/turb3_stagger.yaml")
    n = scene.n

    # 总步数：每循环 80 步，2 循环 = 160 步
    steps_per_cycle = 80
    total_steps = steps_per_cycle * 2 + 20  # 加 20 步 warmup

    rt = SceneRuntime(scene, max_steps=total_steps + 8, warmup_steps=8)
    rt.reset()

    print(f"\n{'='*60}")
    print(f"演示序列开始：2 循环 × 80 步")
    print(f"每循环：静止(pitch=90°) → 启动(pitch=0°) → 偏航(0→30°) → 停机(pitch=90°)")
    print(f"{'='*60}\n")

    step = 0
    for cycle in range(2):
        print(f"\n{'='*60}")
        print(f"第 {cycle + 1} 循环开始")
        print(f"{'='*60}\n")

        # 阶段 1：静止状态（pitch=90°，叶片顺桨）
        print(f"[阶段 1] 静止状态（pitch=90°，风机停转）...")
        for i in range(10):
            # 保持 pitch=90°（停机状态）
            frame = rt.step(pitch_delta=np.zeros(n))  # 不改变 pitch
            m = rt.last_measure
            step += 1
            if i % 5 == 0:
                pitch = np.asarray(m.get("pitch_meas", [90]*n), float)
                rpm = np.asarray(m.get("rotor_speed", [0]*n), float)
                print(f"  Step {step}: pitch={pitch[0]:.1f}°, rpm={rpm[0]:.1f}")

        # 阶段 2：启动（pitch 从 90° → 0°）
        print(f"\n[阶段 2] 启动中（pitch 90° → 0°）...")
        for i in range(20):
            # 逐步减小 pitch 到 0°（启动）
            pitch_target = 0.0
            pitch_current = np.asarray(rt.last_measure.get("pitch_meas", [90]*n), float)
            pitch_delta = np.clip(pitch_target - pitch_current, -5.0, 5.0)  # 每步最多 ±5°
            frame = rt.step(pitch_delta=pitch_delta)
            m = rt.last_measure
            step += 1
            if i % 5 == 0:
                pitch = np.asarray(m.get("pitch_meas", [0]*n), float)
                rpm = np.asarray(m.get("rotor_speed", [0]*n), float)
                power = np.asarray(m.get("power", [0]*n), float)
                print(f"  Step {step}: pitch={pitch[0]:.1f}°, rpm={rpm[0]:.1f}, power={power.sum():.3f} MW")

        # 阶段 3：运行 + 偏航（yaw 从 0° → 30°）
        print(f"\n[阶段 3] 运行中 + T1 偏航（0° → 30°）...")
        for i in range(30):
            # T1 逐步偏航，T2/T3 保持 0°
            yaw_target = np.array([30.0 * (i / 30), 0.0, 0.0])  # 线性增长到 30°
            yaw_current = np.asarray(rt.last_measure.get("yaw", [0]*n), float)
            yaw_delta = np.clip(yaw_target - yaw_current, -2.0, 2.0)  # 每步最多 ±2°
            frame = rt.step(yaw_delta=yaw_delta)
            m = rt.last_measure
            step += 1
            if i % 10 == 0 or i == 29:
                yaw = np.asarray(m.get("yaw", [0]*n), float)
                power = np.asarray(m.get("power", [0]*n), float)
                print(f"  Step {step}: yaw={yaw[0]:.1f}°, farm_power={power.sum():.3f} MW")

        # 阶段 4：停机（pitch 从 0° → 90°）
        print(f"\n[阶段 4] 停机中（pitch 0° → 90°）...")
        for i in range(20):
            # 逐步增大 pitch 到 90°（停机）
            pitch_target = 90.0
            pitch_current = np.asarray(rt.last_measure.get("pitch_meas", [0]*n), float)
            pitch_delta = np.clip(pitch_target - pitch_current, -5.0, 5.0)
            frame = rt.step(pitch_delta=pitch_delta)
            m = rt.last_measure
            step += 1
            if i % 5 == 0:
                pitch = np.asarray(m.get("pitch_meas", [0]*n), float)
                rpm = np.asarray(m.get("rotor_speed", [0]*n), float)
                print(f"  Step {step}: pitch={pitch[0]:.1f}°, rpm={rpm[0]:.1f}")

    print(f"\n{'='*60}")
    print(f"演示完成！总共 {step} 步")
    print(f"{'='*60}\n")

    rt.close(purge=True)
    return 0

if __name__ == "__main__":
    sys.exit(demo_sequence())
