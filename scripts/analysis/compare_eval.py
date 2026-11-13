"""
IPPO(参数共享，局部 critic）vs 真 MAPPO（集中式 critic）严谨对照。

单回合 return 受风况随机采样影响方差极大（曾见 248 vs 534），2 回合无统计意义。
本脚本各跑 N 回合，报 mean ± std，并做 Welch t 近似判断差异是否显著。

Usage:
    python compare_eval.py --episodes 20
"""
import argparse

import numpy as np
import torch

from wfrl import mappo_sb3 as T
from wfrl import mappo_central as MC


def eval_ippo(n_episodes):
    """参数共享 IPPO：train_mappo 的 SB3 模型。返回每回合总 return 数组。"""
    if type(n_episodes) is not int or n_episodes < 1:
        raise ValueError('Episode count must be a positive integer')
    model, venv = T.load_evaluation_pair()
    try:
        rets = []
        for _ in range(n_episodes):
            obs, ep = venv.reset(), 0.0
            done = np.zeros(venv.num_envs, dtype=bool)
            while not done.all():
                a, _ = model.predict(obs, deterministic=True)
                obs, rew, done, _ = venv.step(a)
                if not np.isfinite(rew).all():
                    raise ValueError('Episode rewards must be finite')
                ep += float(rew[0])
                if not np.isfinite(ep):
                    raise ValueError('Episode return must be finite')
            rets.append(ep)
        return np.array(rets)
    finally:
        venv.close()


def eval_central(n_episodes):
    """真 MAPPO：mappo_central 的集中式 critic 模型。返回每回合总 return 数组。"""
    if type(n_episodes) is not int or n_episodes < 1:
        raise ValueError('Episode count must be a positive integer')
    smp = MC.Sampler()
    try:
        actor = MC.Actor(smp.obs_dim, smp.act_dim)
        ckpt = torch.load(MC.MODEL_PATH, map_location=MC.DEVICE, weights_only=False)
        actor.load_state_dict(ckpt["actor"])
        rms = MC.RunningMeanStd((smp.obs_dim,))
        rms.mean, rms.var = ckpt["obs_mean"], ckpt["obs_var"]
        rets = []
        for _ in range(n_episodes):
            obs, ep, done = smp.reset(), 0.0, False
            while not done:
                obs_n = rms.norm(obs).astype(np.float32)
                with torch.no_grad():
                    mu = actor.mu(torch.as_tensor(obs_n))
                obs, r, done = smp.step(mu.cpu().numpy())
                ep += r
                if not np.isfinite(r) or not np.isfinite(ep):
                    raise ValueError('Episode reward and return must be finite')
            rets.append(ep)
        return np.array(rets)
    finally:
        smp.par.close()


def summarize(name, r):
    r = np.asarray(r, dtype=float)
    if r.ndim != 1 or not len(r) or not np.isfinite(r).all():
        raise ValueError(f'{name}: expected nonempty finite episode returns')
    with np.errstate(over='ignore', invalid='ignore'):
        mean, std = r.mean(), r.std()
    if not np.isfinite([mean, std]).all():
        raise ValueError(f'{name}: episode statistics must be finite')
    print(f"  {name:16s}  n={len(r):2d}  mean={r.mean():8.2f}  "
          f"std={r.std():7.2f}  min={r.min():7.2f}  max={r.max():7.2f}")
    return r.mean(), r.std(), len(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", type=int, default=20)
    args = ap.parse_args()
    if args.episodes < 2:
        ap.error('--episodes must be at least 2 for a comparison')

    print(f"[compare] evaluating each over {args.episodes} episodes …\n")
    print("IPPO（参数共享，局部 critic）:")
    ri = eval_ippo(args.episodes)
    mi, si, ni = summarize("IPPO", ri)
    print("\n真 MAPPO（集中式 critic）:")
    rc = eval_central(args.episodes)
    mc, sc, nc = summarize("MAPPO-central", rc)

    if si == 0 and sc == 0:
        print('\n两组回合方差均为 0，无法估计差异的不确定性，不做显著性判定。')
        return

    # Welch t 近似
    with np.errstate(over='ignore', invalid='ignore', divide='ignore'):
        se = np.sqrt(si ** 2 / ni + sc ** 2 / nc) + 1e-9
        tval = (mc - mi) / se
    if not np.isfinite([se, tval]).all():
        raise ValueError('Comparison statistics must be finite')
    print("\n----------------------------------------------------------")
    print(f"  Δ(MAPPO - IPPO) = {mc - mi:+.2f}   (Welch t ≈ {tval:+.2f})")
    if abs(tval) < 2.0:
        print("  |t|<2：差异在噪声范围内，尚不能判定谁更优（需更多回合或更长训练）。")
    else:
        winner = "MAPPO(集中式 critic)" if tval > 0 else "IPPO"
        print(f"  |t|≥2：{winner} 显著更优。")
    print("----------------------------------------------------------")


if __name__ == "__main__":
    main()
