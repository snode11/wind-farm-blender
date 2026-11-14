"""Rollout 一个已训练的 MAPPO checkpoint（确定性策略），读出它真正学到的偏航角。

训练历史里只记了全场功率，没记 yaw —— 判断"策略是否学到往 20° 去、只是没走到"
必须实跑一回合。这里加载 checkpoint、用**确定性动作**(dist.mean，去掉探索噪声)、
以 checkpoint 存的 obs_mean/var 归一化观测（与训练同口径），跑满一个 episode，
打印偏航角轨迹与分机组功率。

策略接口:
  - obs = controls + [wind_speed, wind_direction] (+ duty headroom 若 obs_duty)
  - duty headroom = clip((obs_duty_limit - duty_yaw)/obs_duty_limit, 0, 1)
  - checkpoint 缺少 obs_duty_limit 时固定使用 0.1
  - 动作按 checkpoint 的版本化合同变换并校验运行时边界
  - 无版本模型须有可确认的 Studio 或 train_fastfarm 元数据
  - 确定性: 用 dist.mean，不 sample

需 mpiexec。约 10~12 min（起一次 FAST.Farm + 跑 400 步）。

用法（默认跑 level checkpoint）:
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
from wfrl.policy_actions import (                            # noqa: E402
    LEGACY_OBS_DUTY_LIMIT, duty_headroom, load_action_contract, load_obs_duty_limit,
)
from wfrl.scene.schema import load_scene                      # noqa: E402
from wfrl.scene.runtime import SceneRuntime                   # noqa: E402

CKPT = ("results/checkpoints/"
        "mappo_fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_level_E400_none_stagE400.pt")
OUT = "results/runs/stagE400_rollout.npz"
SCENE = "scenes/turb3_stagger.yaml"
EP_STEPS = 400
WARMUP = 8


def measurement(m, key, n):
    """Return values and the intersection of declared validity and finiteness."""
    values = np.asarray(m.get(key, np.full(n, np.nan)), float).ravel()
    if values.size != n:
        raise ValueError(f"{key}: expected {n} turbine values, got {values.size}")
    valid = np.isfinite(values)
    if f"{key}_valid" in m:
        declared = np.asarray(m[f"{key}_valid"])
        if declared.dtype.kind != "b" or declared.size != n:
            raise ValueError(f"{key}_valid: expected {n} boolean turbine flags")
        valid &= declared.ravel()
    return values, valid


def pack_obs(m, n, obs_keys, obs_duty, obs_duty_limit=LEGACY_OBS_DUTY_LIMIT):
    cols = []
    for key in obs_keys:
        values, valid = measurement(m, key, n)
        if not valid.all():
            turbines = (np.flatnonzero(~valid) + 1).tolist()
            raise ValueError(f"拒绝策略观测: {key} 无效，机组 {turbines}；不做插补")
        cols.append(values)
    if obs_duty:
        duty, valid = measurement(m.get("duty") or {}, "yaw", n)
        if not valid.all():
            raise ValueError("拒绝策略观测: duty.yaw 缺失或无效；不做插补")
        cols.append(duty_headroom(duty, obs_duty_limit))
    obs = np.stack(cols, axis=1).astype(np.float32)
    if not np.isfinite(obs).all():
        raise ValueError("拒绝策略观测: float32 转换后存在非有限值")
    return obs                                               # (n, obs_dim)


def describe(values, valid=None, precision=2):
    values = np.asarray(values, float)
    mask = np.isfinite(values)
    if valid is not None:
        mask &= np.asarray(valid, bool)
    samples = values[mask]
    if not samples.size:
        return "无有效数据（有效样本数=0）"
    return (f"mean={samples.mean():.{precision}f}, "
            f"min={samples.min():.{precision}f}, max={samples.max():.{precision}f} "
            f"（有效样本数={samples.size}）")


def report_rollout(yaw, power, yaw_valid, power_valid):
    print("\n==================== ROLLOUT 结论 ====================")
    for i in range(yaw.shape[1]):
        print(f"T{i + 1} 末50步偏航角(°): "
              + describe(yaw[-50:, i], yaw_valid[-50:, i]))
        print(f"T{i + 1} 末50步功率(MW): "
              + describe(power[-50:, i], power_valid[-50:, i], 3))
    complete = power_valid.all(axis=1) & np.isfinite(power).all(axis=1)
    farm = np.full(power.shape[0], np.nan)
    farm[complete] = power[complete].sum(axis=1)
    print("末50步全场功率(MW): " + describe(farm[-50:], precision=4))
    print("上游(T1)最终偏航(°): " + describe(yaw[-1:, 0], yaw_valid[-1:, 0]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", default=CKPT)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--scene", default=SCENE)
    ap.add_argument("--ep-steps", type=int, default=EP_STEPS)
    args = ap.parse_args()

    ck = torch.load(args.ckpt, map_location="cpu", weights_only=False)
    sc = load_scene(args.scene)
    n = sc.n
    contract = load_action_contract(
        ck, controls=sc.controls, n_agents=n, scene=sc.to_dict())
    obs_keys = ck["obs_keys"]
    obs_duty = ck.get("obs_duty", False)
    obs_duty_limit = load_obs_duty_limit(ck)
    mean = np.asarray(ck["obs_mean"], np.float32)
    std = np.sqrt(np.asarray(ck["obs_var"], np.float32) + 1e-8)
    actor = Actor(ck["obs_dim"], ck["act_dim"])
    actor.load_state_dict(ck["actor"])
    actor.eval()
    print(f"载入 {args.ckpt}\n  obs_dim={ck['obs_dim']} act_dim={ck['act_dim']} "
          f"obs_duty={obs_duty} obs_duty_limit={obs_duty_limit:g} "
          f"recurrent={ck.get('recurrent', False)} "
          f"transform={contract.transform} controls={list(contract.controls)}", flush=True)
    print(f"  log_std={ck['actor']['log_std'].numpy()} "
          f"(std={np.exp(ck['actor']['log_std'].numpy())})", flush=True)

    ep_steps = args.ep_steps
    rt = SceneRuntime(sc, max_steps=ep_steps + WARMUP + 8, warmup_steps=0)
    yaw_hist, pow_hist, yaw_flags, power_flags = [], [], [], []
    try:
        rt.reset()
        contract.validate_runtime(sc.controls, rt.driver.act_bounds)
        print(f"  动作边界 {dict(zip(contract.controls, zip(contract.low, contract.high)))} "
              f"n={n}", flush=True)
        for _ in range(WARMUP):
            rt.step(**{f"{c}_delta": np.zeros(n) for c in contract.controls})
        m = rt.last_measure
        for t in range(ep_steps):
            obs = pack_obs(m, n, obs_keys, obs_duty, obs_duty_limit)
            obs_n = ((obs - mean) / std).astype(np.float32)
            if not np.isfinite(obs_n).all():
                raise ValueError("拒绝策略观测: 归一化后存在非有限值，请检查 checkpoint")
            with torch.no_grad():
                a = actor.dist(torch.as_tensor(obs_n)).mean
            cmd = contract.commands(a.cpu().numpy(), n)
            rt.step(yaw_delta=cmd.get("yaw"), pitch_delta=cmd.get("pitch"),
                    torque_delta=cmd.get("torque"))
            m = rt.last_measure
            yaw, yaw_valid = measurement(m, "yaw", n)
            pw, power_valid = measurement(m, "power", n)
            yaw_hist.append(yaw.copy())
            pow_hist.append(pw.copy())
            yaw_flags.append(yaw_valid.copy())
            power_flags.append(power_valid.copy())
            if t % 50 == 0 or t == ep_steps - 1:
                yaw_text = [round(v, 1) if ok else "无有效数据" for v, ok in zip(yaw, yaw_valid)]
                power_text = [round(v, 3) if ok else "无有效数据" for v, ok in zip(pw, power_valid)]
                total = describe([pw.sum()] if power_valid.all() else [], precision=3)
                print(f"  step {t:3d}  yaw={yaw_text} "
                      f"(有效样本数={yaw_valid.sum()}) P={power_text} "
                      f"(有效样本数={power_valid.sum()}) ΣP={total} MW",
                      flush=True)
    finally:
        rt.close(purge=True)

    yaw_hist = np.asarray(yaw_hist).reshape(-1, n)              # (T, n)
    pow_hist = np.asarray(pow_hist).reshape(-1, n)
    yaw_valid = np.asarray(yaw_flags, bool).reshape(-1, n)
    power_valid = np.asarray(power_flags, bool).reshape(-1, n)
    report_rollout(yaw_hist, pow_hist, yaw_valid, power_valid)
    np.savez(args.out, yaw=yaw_hist, power=pow_hist,
             yaw_valid=yaw_valid, power_valid=power_valid)
    print(f"轨迹存 -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
