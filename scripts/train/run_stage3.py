"""阶段 3 的训练批次 —— 逐 seed 成对跑「零偏航基线 / 学习策略」。

为什么要成对：README §8 第 6 条，n=3 下"seed 区间恰好不重叠"不是证据，判据是
**间隙 > max(seed 标准差)**。而要比得动，基线必须和策略走**同一条**采样路径：
同样的回合长度、同样的 warmup、同样的来流调度、同样的奖励口径。所以基线不是
另写一个脚本，而是 `--policy zero` —— 唯一的差别是动作恒为 0 且不更新网络。

规模的账（单机，FAST.Farm 约 0.6 s/控制步，每回合重起约 25 s）：

    每 seed 墙钟 ≈ (iters·n_steps + baseline_steps) · 0.6 s
                 + (回合数) · 25 s

默认 40×128 学习 + 5×128 基线、回合 128 步 ⇒ 每 seed 约 (5760)·0.6 + 45·25
≈ 58 + 19 = **77 min**，3 个 seed 约 **3.9 h**。用 --iters/--seeds 调。

    "C:\\Program Files\\Microsoft MPI\\Bin\\mpiexec.exe" -n 1 ^
        python -X utf8 scripts/train/run_stage3.py --seeds 0,1,2

跑之前先确认没有孤儿 FAST.Farm 进程（README §8 第 4 条），否则第一次 spawn
会无声挂起。中途断了直接重跑：已经落盘的 hist 会被跳过。
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wfrl import paths                                   # noqa: E402
from train_fastfarm import train                         # noqa: E402

ENV = "Dec_Turb3_Row1_Fastfarm"


def hist_path(tag):
    safe = ENV.replace("/", "_")
    return os.path.join(paths.RUNS, f"fastfarm_{safe}_{tag}_hist.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="0,1,2")
    ap.add_argument("--iters", type=int, default=40)
    ap.add_argument("--n-steps", type=int, default=128)
    ap.add_argument("--baseline-iters", type=int, default=5,
                    help="零偏航基线的轮数。基线不学习，只要统计稳就够")
    ap.add_argument("--episode-steps", type=int, default=128)
    ap.add_argument("--warmup-steps", type=int, default=8)
    ap.add_argument("--wind-sched", default="pin:8",
                    help="阶段 3/4 用 pin:8（受控对照）；阶段 5 变风况再换 weibull")
    ap.add_argument("--reward", default="level")
    ap.add_argument("--load-coef", type=float, default=1.0)
    ap.add_argument("--recurrent", action="store_true")
    ap.add_argument("--tag", default="stage3")
    ap.add_argument("--dry-run", action="store_true",
                    help="只算墙钟预算并列出要跑的组合，不真跑")
    args = ap.parse_args()

    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    algo = "gru-mappo" if args.recurrent else "mappo"
    sched_tag = args.wind_sched.replace(":", "").replace(",", "-")

    jobs = []
    for sd in seeds:
        for policy, iters in (("zero", args.baseline_iters),
                              ("learn", args.iters)):
            name = "zero" if policy == "zero" else algo
            tag = (f"{name}_s{sd}_{args.reward}_E{args.episode_steps}"
                   f"_{sched_tag}_{args.tag}")
            jobs.append({"seed": sd, "policy": policy, "iters": iters,
                         "tag": tag, "path": hist_path(tag)})

    steps = sum(j["iters"] * args.n_steps for j in jobs)
    eps = sum(max(1, j["iters"] * args.n_steps // max(args.episode_steps, 1))
              for j in jobs)
    print(f"[stage3] {len(jobs)} 个 run，共 {steps} 控制步、约 {eps} 个回合")
    print(f"[stage3] 墙钟预算 ≈ {steps*0.6/60:.0f} min 仿真 + "
          f"{eps*25/60:.0f} min 重启 = {(steps*0.6+eps*25)/3600:.1f} h")
    for j in jobs:
        done = os.path.exists(j["path"])
        print(f"  {'跳过(已有)' if done else '待跑    '}  {j['tag']}")
    if args.dry_run:
        return 0

    t0 = time.time()
    for k, j in enumerate(jobs, 1):
        if os.path.exists(j["path"]):
            print(f"[stage3] ({k}/{len(jobs)}) 跳过 {j['tag']}（已有产物）",
                  flush=True)
            continue
        print(f"\n[stage3] ({k}/{len(jobs)}) {j['tag']}  "
              f"已用 {(time.time()-t0)/60:.0f} min", flush=True)
        train(ENV, j["iters"], args.n_steps,
              recurrent=args.recurrent, seed=j["seed"],
              episode_steps=args.episode_steps,
              warmup_steps=args.warmup_steps,
              wind_sched=args.wind_sched, reward=args.reward,
              load_coef=args.load_coef, policy=j["policy"],
              tag_extra=args.tag)
    print(f"\n[stage3] 全部完成，用时 {(time.time()-t0)/3600:.2f} h", flush=True)
    print("STAGE3_RUNS_OK", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
