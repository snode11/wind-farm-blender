"""Rollout 一个已训练的 MAPPO checkpoint（确定性策略），读出它真正学到的偏航角。

训练历史里只记了全场功率，没记 yaw —— 判断"策略是否学到往 20° 去、只是没走到"
必须实跑一回合。这里加载 checkpoint、用**确定性动作**(dist.mean，去掉探索噪声)、
以 checkpoint 存的 obs_mean/var 归一化观测（与训练同口径），跑满一个 episode，
打印偏航角轨迹与分机组功率。

口径对齐（train_fastfarm.py）:
  - obs = [yaw, wind_speed, wind_direction] (+ duty headroom 若 obs_duty)
  - duty headroom = clip((0.1 - duty_yaw)/0.1, 0, 1)
  - 动作 = 网络输出直接 clip 到 [act_low, act_high]（无 tanh）
  - 确定性: 用 dist.mean，不 sample

需 mpiexec。约 10~12 min（起一次 FAST.Farm + 跑 400 步）。

用法（默认跑 level 那个 checkpoint，与旧行为一致）:
  mpiexec -n 1 python scripts/experiments/rollout_ckpt.py [--ckpt PATH] [--out PATH]
          [--scene PATH] [--ep-steps N]
"""
import argparse
import os
import sys

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wfrl.mappo_central import Actor                          # noqa: E402
from wfrl.scene.schema import load_scene                      # noqa: E402
from wfrl.scene.runtime import SceneRuntime                   # noqa: E402

CKPT = ("results/checkpoints/"
        "mappo_fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_level_E400_none_stagE400.pt")
OUT = "results/runs/stagE400_rollout.npz"
SCENE = "scenes/turb3_stagger.yaml"
EP_STEPS = 400
WARMUP = 8


def pack_obs(m, n, obs_keys, obs_duty):
    cols = [np.asarray(m[k], np.float32).ravel() for k in obs_keys]
    if obs_duty:
        duty = np.asarray((m.get("duty") or {}).get("yaw", np.zeros(n)),
                          np.float32).ravel()
        cols.append(np.clip((0.1 - duty) / 0.1, 0.0, 1.0).astype(np.float32))
    return np.stack(cols, axis=1)                              # (n, obs_dim)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", default=CKPT)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--scene", default=SCENE)
    ap.add_argument("--ep-steps", type=int, default=EP_STEPS)
    args = ap.parse_args()

    ck = torch.load(args.ckpt, map_location="cpu", weights_only=False)
    obs_keys = ck["obs_keys"]
    obs_duty = ck["obs_duty"]
    mean = np.asarray(ck["obs_mean"], np.float32)
    std = np.sqrt(np.asarray(ck["obs_var"], np.float32) + 1e-8)
    actor = Actor(ck["obs_dim"], ck["act_dim"])
    actor.load_state_dict(ck["actor"])
    actor.eval()
    print(f"载入 {args.ckpt}\n  obs_dim={ck['obs_dim']} act_dim={ck['act_dim']} "
          f"obs_duty={obs_duty} recurrent={ck['recurrent']}", flush=True)
    print(f"  log_std={ck['actor']['log_std'].numpy()} "
          f"(std={np.exp(ck['actor']['log_std'].numpy())})", flush=True)

    sc = load_scene(args.scene)
    n = sc.n
    ep_steps = args.ep_steps
    rt = SceneRuntime(sc, max_steps=ep_steps + WARMUP + 8, warmup_steps=WARMUP)
    rt.reset()                                                # 内含 warmup
    drv = rt.driver
    lo, hi = drv.act_low, drv.act_high
    print(f"  动作边界 yaw ∈ [{lo}, {hi}]°/step  n={n}", flush=True)

    m = rt.last_measure
    yaw_hist, pow_hist = [], []
    for t in range(ep_steps):
        obs = pack_obs(m, n, obs_keys, obs_duty)
        obs_n = ((obs - mean) / std).astype(np.float32)
        with torch.no_grad():
            a = actor.dist(torch.as_tensor(obs_n)).mean      # 确定性
        a_np = np.clip(a.cpu().numpy().ravel(), lo, hi)
        frame = rt.step(yaw_delta=a_np)
        m = rt.last_measure
        yaw = np.asarray(m["yaw"], float).ravel()
        pw = np.asarray(m["power"], float).ravel()
        yaw_hist.append(yaw.copy())
        pow_hist.append(pw.copy())
        if t % 50 == 0 or t == ep_steps - 1:
            print(f"  step {t:3d}  yaw={np.round(yaw, 1).tolist()}  "
                  f"P={np.round(pw, 3).tolist()}  ΣP={pw.sum():.3f}MW",
                  flush=True)

    rt.close(purge=True)

    yaw_hist = np.array(yaw_hist)                              # (T, n)
    pow_hist = np.array(pow_hist)
    tail = slice(-50, None)                                    # 末 50 步稳态
    print("\n==================== ROLLOUT 结论 ====================")
    print(f"末50步平均偏航角(°): {np.round(yaw_hist[tail].mean(0), 2).tolist()}")
    print(f"末50步偏航角范围   : "
          f"min={np.round(yaw_hist[tail].min(0), 1).tolist()} "
          f"max={np.round(yaw_hist[tail].max(0), 1).tolist()}")
    print(f"末50步分机组功率(MW): {np.round(pow_hist[tail].mean(0), 3).tolist()}")
    print(f"末50步全场功率(MW) : {pow_hist[tail].sum(1).mean():.4f}")
    print(f"上游(T1)最终偏航   : {yaw_hist[-1, 0]:.2f}°  "
          f"(理论最优 ~20°；可达上限 ~36° @400步)")
    np.savez(args.out, yaw=yaw_hist, power=pow_hist)
    print(f"轨迹存 -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
