"""Plot frozen P4 outputs only; does not run fitting or inspect truth meshes."""
from pathlib import Path
import json
import os
import tempfile
import numpy as np
os.environ.setdefault('MPLCONFIGDIR', str(Path(tempfile.gettempdir())/'nrel-p4-matplotlib'))
os.environ.setdefault('XDG_CACHE_HOME', str(Path(tempfile.gettempdir())/'nrel-p4-cache'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent
NAMES = ['original_raw', 'physical_reference_center', 'greville_normalized']
LABELS = ['Original raw (v2 reproduced)', 'Physical reference center', 'Greville center']
COLORS = ['#5e6572', '#d5872d', '#237fa5']
STYLES = ['-', '--', '-.']


def main():
    fits = [json.loads((ROOT/'fit'/n/'optimization.json').read_text()) for n in NAMES]
    scores = [json.loads((ROOT/'evaluation-only'/n/'scores.json').read_text())['models']['final'] for n in NAMES]
    fig, axes = plt.subplots(2, 2, figsize=(12.8, 8.8), constrained_layout=True)
    for fit, label, color, style in zip(fits, LABELS, COLORS, STYLES):
        last = [h for h in fit['video_history'] if h['stage'] == 2]
        y = [h['total_loss_pre_step'] for h in last] + [fit['final_640_total_objective']]
        axes[0,0].plot(np.arange(80,101), y, label=label, color=color, linestyle=style, linewidth=1.8)
    axes[0,0].set(title='A  Final 640 × 360 stage', xlabel='Completed optimization steps', ylabel='Six-image mean contour loss + regularization')
    axes[0,0].legend(fontsize=8, loc='upper right')
    axes[0,0].grid(alpha=.16)

    x = np.arange(4)
    for i, (score, color) in enumerate(zip(scores, COLORS)):
        values = [score['global'][direction][metric] for direction,metric in
                  [('reconstruction_to_truth','mean_m'), ('truth_to_reconstruction','mean_m'),
                   ('reconstruction_to_truth','p95_m'), ('truth_to_reconstruction','p95_m')]]
        axes[0,1].bar(x+(i-1)*.24, values, width=.23, color=color)
    axes[0,1].set(title='B  Whole-surface distances', ylabel='Distance (m)', xticks=x,
                  xticklabels=['R→T mean','T→R mean','R→T P95','T→R P95'])
    axes[0,1].grid(axis='y',alpha=.16)

    for axis,metric,title in [(axes[1,0], 'chord_length_m','C  Section long-side absolute error'),
                              (axes[1,1], 'body_thickness_m','D  Section short-side absolute error')]:
        for i, (score,color) in enumerate(zip(scores,COLORS)):
            values = [abs(s['reconstruction'][metric]-s['truth'][metric]) for s in score['sections']]
            axis.bar(np.arange(3)+(i-1)*.24,values,width=.23,color=color)
        axis.set(title=title, ylabel='Absolute dimension error (m)', xlabel='Fixed z (m)',
                 xticks=[0,1,2],xticklabels=['9.225 (root)','30.750','52.275'])
        axis.grid(axis='y',alpha=.16)
    for ax in axes.flat:
        ax.spines[['top','right']].set_visible(False)
    fig.suptitle('P4 · Same model, observations, objective and 100-step budget\n'
                 'Coordinate experiment; diagnostic sequence; no formal model replacement',fontsize=14)
    fig.savefig(ROOT/'p4_summary.png',dpi=180)
    fig.savefig(ROOT/'p4_summary.pdf')
    plt.close(fig)


if __name__ == '__main__':
    main()
