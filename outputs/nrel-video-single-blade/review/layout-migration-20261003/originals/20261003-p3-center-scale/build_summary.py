"""Render P3 diagnostics from saved outputs only; no fitting or truth access."""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parent
geometry = json.loads((ROOT / "geometry/center_scale_audit.json").read_text())
family = json.loads((ROOT / "family/verified/family_audit.json").read_text())
rgb = json.loads((ROOT / "rgb/root_rgb_audit.json").read_text())
plt.rcParams.update({"font.size": 10, "axes.titlesize": 12, "axes.spines.top": False,
                     "axes.spines.right": False, "savefig.facecolor": "white"})
fig, axes = plt.subplots(2, 2, figsize=(13.2, 9.2), layout="constrained")
colors = {"A": "#0072B2", "B": "#D55E00", "C": "#009E73"}
fit = sorted((x for x in geometry["response_summary"] if x["width"] == 640
              and x["split"] == "fit" and x["region"] == "root_band"),
             key=lambda x: x["amplitude_m"])
amps = [x["amplitude_m"] * 1000 for x in fit]
ax = axes[0, 0]
labels = {"A": "A: original scale + center shift", "B": "B: scale, ring centers held",
          "C": "C: center shift only"}
for mode, marker in zip("ABC", ["o", "s", "^"]):
    ax.plot(amps, [x["modes"][mode]["symmetric_response_rms_native_px"] for x in fit],
            marker=marker, color=colors[mode], label=labels[mode])
ax.set(title="1  Root image response remains after compensation", xlabel="Half-chord perturbation amplitude (mm)",
       ylabel="Symmetric raw O2C response RMS (native px)", ylim=(0, None))
ax.legend(fontsize=9, loc="upper left")
ax.set_xticks(amps)

ax = axes[0, 1]
for mode, marker in zip("AB", ["o", "s"]):
    for width, linestyle in [(640, "-"), (320, "--")]:
        rows = sorted((x for x in geometry["response_summary"] if x["width"] == width
                       and x["split"] == "fit" and x["region"] == "root_band"),
                      key=lambda x: x["amplitude_m"])
        ax.plot(amps, [x["response_matrices"][mode]["singular_values_native_px_per_m"][-1] for x in rows],
                color=colors[mode], marker=marker, linestyle=linestyle, label=f"[X,Y,{mode}], width {width}")
ax.set(title="2  Both response matrices retain a weak direction", xlabel="Half-chord perturbation amplitude (mm)",
       ylabel="Smallest singular value of J / sqrt(N) (px/m)", ylim=(0, 20))
ax.set_xticks(amps)
ax.legend(fontsize=9, ncol=2, loc="upper right")

ax = axes[1, 0]
for sign, marker, linestyle in [(1, "s", "-"), (-1, "o", "--")]:
    rows = sorted((x for x in family["cases"] if x["halfwidth_amplitude_m"] * sign > 0),
                  key=lambda x: abs(x["halfwidth_amplitude_m"]))
    ax.plot(amps, [x["all_ring_residual"]["max_vector_norm_m"] * 1000 for x in rows],
            marker=marker, linestyle=linestyle, color="#CC79A7" if sign > 0 else "#444444",
            label="Positive amplitude" if sign > 0 else "Negative amplitude")
ax.set(title="3  Exact ring compensation leaves the original family", xlabel="Absolute half-chord perturbation (mm)",
       ylabel="Maximum ring-center projection residual (mm)", ylim=(0, None))
ax.set_xticks(amps)
ax.legend(fontsize=9, loc="upper left")
ax.text(.03, .72, "Best projection into the original center basis\nNumerical discrepancy, not an accuracy tolerance",
        transform=ax.transAxes, fontsize=9, color="#555555")

ax = axes[1, 1]
rows = rgb["frames"]
y = np.arange(len(rows))
eligible = np.array([x["native_ray_visible_eligible_length_px"] for x in rows])
excluded = np.array([x["native_ray_visible_excluded_length_px"] for x in rows])
ax.barh(y, eligible, color="#0072B2", label="Eligible under saved domain")
ax.barh(y, excluded, left=eligible, color="#E69F00", hatch="///", label="Excluded by saved domain")
ax.set_yticks(y, [f"{x['camera']} #{x['frame']}" + (" *" if x["role"] != "fit" else "") for x in rows])
ax.invert_yaxis()
ax.set(title="4  Native root silhouette support varies by frame", xlabel="Same-model ray-visible projected line length (native px)")
ax.set_xlim(0, 1050)
ax.text(12, 5, "0: root silhouette outside image", va="center", fontsize=9)
ax.legend(fontsize=8.5, loc="lower right")
ax.text(.98, .45, "* nonblind\ndiagnostic frames", transform=ax.transAxes, ha="right", fontsize=8.5)
for ax in axes.flat:
    ax.grid(axis="y" if ax is not axes[1, 1] else "x", alpha=.18)
    ax.set_axisbelow(True)
fig.suptitle("NREL 5MW / P3 controlled forward audit\nFrozen v2 model; root band 6.15–12.30 m; no refit, new capture or truth scoring", fontsize=15)
fig.savefig(ROOT / "p3_summary.png", dpi=180)
fig.savefig(ROOT / "p3_summary.pdf")
plt.close(fig)
