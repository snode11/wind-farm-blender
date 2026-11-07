"""Compare seed-paired zero-yaw baselines and learned policies.

Pairs require completed fresh runs, matching source/input identity and every
common configuration field. Only policy and iteration budget may differ.
Repeated candidates require explicit --pair ZERO_RUN_ID:LEARN_RUN_ID selection.
"""
import argparse
import glob
import json
import os
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                          # noqa: E402
import numpy as np                                       # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
from wfrl import paths                                   # noqa: E402
from wfrl.training_integrity import SCHEMA, experiment_identity, file_digest  # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def load(tag):
    runs = []
    for path in sorted(glob.glob(os.path.join(paths.RUNS, "*_hist.json"))):
        try:
            with open(path, encoding="utf-8") as stream:
                data = json.load(stream)
            if tag and tag not in data.get("tag", ""):
                continue
            hist = data["hist"]
            runs.append(dict(path=path, tag=data["tag"], cfg=hist["cfg"], hist=hist,
                             checkpoint_sha256=data.get("checkpoint_sha256")))
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ValueError(f"Invalid history {path}: {exc}") from exc
    return runs


def _common_config(run):
    return {key: value for key, value in run["cfg"].items() if key not in {"policy", "iters"}}


def _pair_key(run):
    return json.dumps([run["hist"]["run"]["source_identity"], _common_config(run)],
                      sort_keys=True, allow_nan=False)


def _validate_run(run):
    try:
        config, hist = run["cfg"], run["hist"]
        identity = hist["run"]
        if (not isinstance(config, dict) or not isinstance(identity, dict) or
                hist["cfg"] != config or config["policy"] not in {"zero", "learn"} or
                type(config["seed"]) is not int or
                any(type(config[key]) is not int or config[key] <= 0 for key in ("iters", "n_steps")) or
                identity.get("schema") != SCHEMA or identity.get("status") != "completed" or
                identity.get("recovery_mode") != "fresh" or identity.get("parent_run_id") is not None or
                not isinstance(identity.get("run_id"), str) or not identity["run_id"] or
                not isinstance(identity.get("source_identity"), str) or not identity["source_identity"] or
                identity.get("experiment_id") != experiment_identity(config, identity["source_identity"]) or
                identity.get("control_steps") != config["iters"] * config["n_steps"]):
            raise ValueError("completed fresh run identity/configuration required")
        power = np.asarray(hist["power_norm"], dtype=float)
        if power.shape != (config["iters"],) or not np.isfinite(power).all():
            raise ValueError("power history must contain one finite value per completed iteration")
        with np.errstate(over='ignore', invalid='ignore'):
            mean = float(power.mean())
        if not np.isfinite(mean):
            raise ValueError("power mean must be finite")
        if config["policy"] == "zero" and mean <= 0:
            raise ValueError("baseline power must be positive")
        if file_digest(Path(identity["checkpoint"])) != run.get("checkpoint_sha256"):
            raise ValueError("checkpoint digest mismatch")
    except (KeyError, TypeError, OSError, ValueError) as exc:
        raise ValueError(f"Invalid comparison run {run.get('path', '?')}: {exc}") from exc


def pair_runs(runs, selected=()):
    """Return unambiguous pairs, refusing missing partners or repeated candidates."""
    if selected:
        pairs = []
        used = set()
        for zero_id, learn_id in selected:
            pair = []
            for run_id in (zero_id, learn_id):
                candidates = [run for run in runs if run["hist"].get("run", {}).get("run_id") == run_id]
                if len(candidates) != 1 or run_id in used:
                    raise ValueError(f"Missing, ambiguous or repeated run_id: {run_id}")
                used.add(run_id)
                pair.append(candidates[0])
            pairs.append(tuple(pair))
    else:
        groups, identities = {}, set()
        for run in runs:
            _validate_run(run)
            run_id = run["hist"]["run"]["run_id"]
            if run_id in identities:
                raise ValueError(f"ambiguous duplicate run_id: {run_id}")
            identities.add(run_id)
            group = groups.setdefault(_pair_key(run), {"zero": [], "learn": []})
            group[run["cfg"]["policy"]].append(run)
        pairs = []
        for group in groups.values():
            if any(len(candidates) != 1 for candidates in group.values()):
                ids = {policy: [r["hist"]["run"]["run_id"] for r in candidates]
                       for policy, candidates in group.items()}
                raise ValueError(f"Missing or ambiguous pair {ids}; select --pair ZERO_RUN_ID:LEARN_RUN_ID")
            pairs.append((group["zero"][0], group["learn"][0]))
    seen = set()
    for zero, learn in pairs:
        for run in (zero, learn):
            _validate_run(run)
        if zero["cfg"]["policy"] != "zero" or learn["cfg"]["policy"] != "learn":
            raise ValueError("Pair order must be zero:learn")
        if _common_config(zero) != _common_config(learn):
            raise ValueError("Pair common configuration mismatch")
        if zero["hist"]["run"]["source_identity"] != learn["hist"]["run"]["source_identity"]:
            raise ValueError("Pair source/input identity mismatch")
        key = _pair_key(zero)
        if key in seen:
            raise ValueError("A seed/configuration/source may contribute only one independent pair")
        seen.add(key)
    if not pairs:
        raise ValueError("No completed pairs selected")
    return pairs


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tag", default="stage3", help="只看 tag 里含这段的 run")
    ap.add_argument("--last", type=int, default=5, help="策略取末 N 轮的平均")
    ap.add_argument("--pair", action="append", default=[], metavar="ZERO_RUN_ID:LEARN_RUN_ID",
                    help="显式配对，可重复；不按文件名或文件顺序选择运行")
    args = ap.parse_args()
    if args.last <= 0:
        ap.error("--last must be positive")
    try:
        selected = []
        for value in args.pair:
            ids = value.split(":")
            if len(ids) != 2 or not all(ids):
                raise ValueError("--pair requires ZERO_RUN_ID:LEARN_RUN_ID")
            selected.append(tuple(ids))
        pairs = pair_runs(load(args.tag), selected)
    except ValueError as exc:
        print(f"Comparison refused: {exc}", file=sys.stderr)
        return 1

    groups = {}
    try:
        for zero, learn in pairs:
            with np.errstate(over='ignore', invalid='ignore'):
                bm = float(np.asarray(zero["hist"]["power_norm"], dtype=float).mean())
                pm = float(np.asarray(learn["hist"]["power_norm"], dtype=float)[-args.last:].mean())
            gain = (pm - bm) / bm * 100.
            if not np.isfinite([bm, pm, gain]).all():
                raise ValueError("comparison means and gain must be finite")
            config = _common_config(zero)
            config.pop("seed")
            key = json.dumps([zero["hist"]["run"]["source_identity"], config,
                              zero["cfg"]["iters"], learn["cfg"]["iters"]], sort_keys=True)
            groups.setdefault(key, []).append((zero, learn, bm, pm, gain))
    except ValueError as exc:
        print(f"Comparison refused: {exc}", file=sys.stderr)
        return 1
    for key, group in groups.items():
        source, config, baseline_iters, policy_iters = json.loads(key)
        print(f"\n=== source {source} / baseline {baseline_iters} / learn {policy_iters} ===")
        print("  common configuration: " + json.dumps(config, sort_keys=True, ensure_ascii=False))
        gains = []
        for zero, learn, bm, pm, gain in sorted(group, key=lambda pair: pair[0]["cfg"]["seed"]):
            baseline = np.asarray(zero["hist"]["power_norm"], dtype=float)
            policy = np.asarray(learn["hist"]["power_norm"], dtype=float)
            gains.append(gain)
            print(f"  seed {zero['cfg']['seed']}: {zero['hist']['run']['run_id']} : "
                  f"{learn['hist']['run']['run_id']}")
            print(f"    基线 P/u³ {bm:.3f}（{len(baseline)} 轮）  "
                  f"策略末{min(args.last, len(policy))}轮 {pm:.3f}  →  {gain:+.2f}%")
        if len(gains) < 2:
            print("  seed 不足 2 个，不做判据")
            continue
        gains = np.asarray(gains)
        print(f"  增益 {gains.mean():+.2f}% ± {gains.std():.2f}%（n={len(gains)}）")
        print("  判据 → " + ("通过：增益均值 > seed 标准差" if gains.mean() > gains.std()
                            else "不通过：增益均值落在 seed 散布之内，还不能归因于策略"))

    fig, ax = plt.subplots(figsize=(8, 4.5))
    for zero, learn in pairs:
        line, = ax.plot(np.arange(1, len(learn["hist"]["power_norm"]) + 1),
                        learn["hist"]["power_norm"], "-o", ms=2.5,
                        label=f"seed {learn['cfg']['seed']} / {learn['hist']['run']['run_id'][:12]}")
        ax.axhline(float(np.mean(zero["hist"]["power_norm"])), ls="--", lw=1,
                   alpha=.5, color=line.get_color())
    ax.set_xlabel("iter")
    ax.set_ylabel(r"$P_{farm}/u_\infty^3$  [kW/(m/s)$^3$]")
    ax.set_title("Matched baseline and policy runs")
    ax.grid(True, alpha=.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    out = os.path.join(paths.RUNS, f"stage3_{args.tag}_curves.png")
    fig.savefig(out, dpi=130)
    plt.close(fig)
    print(f"\n→ {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
