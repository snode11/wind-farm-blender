"""Run seed-paired zero-yaw baselines and learned policies.

A completed run is reusable only when its effective configuration, source/input
identity, full checkpoint, history, terminal status, and actual budget agree.
Readable labels organize output; each execution has an independent run identity.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wfrl import paths                                   # noqa: E402
from scripts.train.train_fastfarm import (  # noqa: E402
    train, experiment_config, source_input_identity, display_label,
)
from wfrl.training_integrity import find_completed_run  # noqa: E402

ENV = "Dec_Turb3_Row1_Fastfarm"


def build_jobs(args, source_identity):
    jobs = []
    for seed in [int(value) for value in args.seeds.split(",") if value.strip()]:
        for policy, iters in (("zero", args.baseline_iters), ("learn", args.iters)):
            options = dict(recurrent=args.recurrent, seed=seed,
                           episode_steps=args.episode_steps, warmup_steps=args.warmup_steps,
                           wind_sched=args.wind_sched, reward=args.reward,
                           load_coef=args.load_coef, policy=policy)
            config = experiment_config(ENV, iters, args.n_steps, **options)
            jobs.append({"seed": seed, "policy": policy, "iters": iters,
                         "tag": display_label(config, args.tag), "config": config,
                         "source_identity": source_identity, "options": options})
    return jobs


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
                    help="pin:8 用于受控对照；weibull 用于逐回合变风况")
    ap.add_argument("--reward", default="level")
    ap.add_argument("--load-coef", type=float, default=1.0)
    ap.add_argument("--recurrent", action="store_true")
    ap.add_argument("--tag", default="stage3")
    ap.add_argument("--dry-run", action="store_true",
                    help="只算墙钟预算并列出要跑的组合，不真跑")
    args = ap.parse_args()

    jobs = build_jobs(args, source_input_identity())

    steps = sum(j["iters"] * args.n_steps for j in jobs)
    eps = sum(max(1, j["iters"] * args.n_steps // max(args.episode_steps, 1))
              for j in jobs)
    print(f"[stage3] {len(jobs)} 个 run，共 {steps} 控制步、约 {eps} 个回合")
    print(f"[stage3] 墙钟预算 ≈ {steps*0.6/60:.0f} min 仿真 + "
          f"{eps*25/60:.0f} min 重启 = {(steps*0.6+eps*25)/3600:.1f} h")
    for j in jobs:
        done = find_completed_run(paths.RUNS, j["config"], j["source_identity"]) is not None
        print(f"  {'跳过(已有)' if done else '待跑    '}  {j['tag']}")
    if args.dry_run:
        return 0

    t0 = time.time()
    for k, j in enumerate(jobs, 1):
        completed = find_completed_run(paths.RUNS, j["config"], j["source_identity"])
        if completed is not None:
            print(f"[stage3] ({k}/{len(jobs)}) 跳过 {j['tag']}（已验证完成：{completed.name}）",
                  flush=True)
            continue
        print(f"\n[stage3] ({k}/{len(jobs)}) {j['tag']}  "
              f"已用 {(time.time()-t0)/60:.0f} min", flush=True)
        train(ENV, j["iters"], args.n_steps, **j["options"], tag_extra=args.tag)
    print(f"\n[stage3] 全部完成，用时 {(time.time()-t0)/3600:.2f} h", flush=True)
    print("STAGE3_RUNS_OK", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
