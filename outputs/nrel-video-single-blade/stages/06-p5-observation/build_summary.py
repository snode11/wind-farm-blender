"""Post-freeze figures from saved P5 scores and same-target image residuals."""
from pathlib import Path
import hashlib
import json
import numpy as np
import matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt

BASE = Path(__file__).resolve().parent
BRANCHES = ('original_raw', 'revised_boundary')
COLORS = ('#53667C', '#007F87')
LABELS = ('Original observations', 'Added RGB boundary')
DIRECTIONS = ('reconstruction_to_truth', 'truth_to_reconstruction')


def load(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    freeze = load(BASE / 'fit/model_freeze.json')
    for name, signature in freeze['sha256'].items():
        if sha(BASE / 'fit' / name) != signature:
            raise RuntimeError(f'Changed frozen artifact: {name}')
    record = load(BASE / 'evaluation-only/evaluation_record.json')
    if record.get('status') not in ('SCORING_COMPLETE_SIGNATURES_UNCHANGED', 'COMPLETE_SIGNATURES_UNCHANGED'):
        raise RuntimeError(f"Scoring did not complete: {record.get('status')}")
    scores = {b: load(BASE / 'evaluation-only' / b / 'scores.json') for b in BRANCHES}
    residuals = load(BASE / 'fit/cross_observation_residuals.json')['results']
    nonblind = load(BASE / 'fit/nonblind_residuals.json')['results']
    summary = {'branches': {}, 'image_residuals_on_same_target': {},
               'image_loss_warning': 'Compare models within a named observation target only; pixel objective is not a metric geometry score.',
               'nonblind_image_mean': {}, 'scoring_config': scores[BRANCHES[0]]['scoring_config']}
    for b in BRANCHES:
        final = scores[b]['models']['final']
        sections = [{'z_m': s['truth']['z_m'],
                     'long_side_abs_error_m': abs(s['reconstruction']['chord_length_m'] - s['truth']['chord_length_m']),
                     'short_side_abs_error_m': abs(s['reconstruction']['body_thickness_m'] - s['truth']['body_thickness_m'])}
                    for s in final['sections']]
        summary['branches'][b] = {'global': final['global'], 'regions': final['regions'], 'sections': sections,
            'image_contribution': scores[b]['comparison']['image_contribution'],
            'training_objective': load(BASE / 'fit' / b / 'optimization.json')['final_objective_under_training_observation_version']}
        summary['nonblind_image_mean'][b] = float(np.mean([r['image_loss'] for r in nonblind[b]]))
    for target in BRANCHES:
        summary['image_residuals_on_same_target'][target] = {
            b: float(np.mean([r['image_loss'] for r in residuals[target][b]])) for b in BRANCHES}
    summary['geometry_delta_revised_minus_original_m'] = {
        d: {m: summary['branches']['revised_boundary']['global'][d][m] - summary['branches']['original_raw']['global'][d][m]
            for m in ('mean_m', 'p95_m')} for d in DIRECTIONS}
    (BASE / 'summary_results.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    plt.rcParams.update({'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False})
    fig, ax = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
    x = np.arange(4)
    for i, b in enumerate(BRANCHES):
        values = [summary['branches'][b]['global'][d][m] for d in DIRECTIONS for m in ('mean_m', 'p95_m')]
        ax[0, 0].bar(x+(i-.5)*.35, values, width=.33, color=COLORS[i], label=LABELS[i])
    ax[0, 0].set_xticks(x, ['R to T\nmean', 'R to T\nP95', 'T to R\nmean', 'T to R\nP95'])
    ax[0, 0].set_title('A  Global surface distances')
    ax[0, 0].set_ylabel('Distance (m), lower is better')
    ax[0, 0].legend(fontsize=8)
    for a, key, title in ((ax[0, 1], 'long_side_abs_error_m', 'B  Fixed-section long-side error'),
                           (ax[1, 0], 'short_side_abs_error_m', 'C  Fixed-section short-side error')):
        for i, b in enumerate(BRANCHES):
            sections = summary['branches'][b]['sections']
            a.plot([s['z_m'] for s in sections], [s[key] for s in sections], color=COLORS[i],
                   marker='o' if i == 0 else 's', label=LABELS[i])
        a.set_xticks([9.225, 30.75, 52.275], ['9.225', '30.750', '52.275'])
        a.set_xlabel('Fixed section z (m)')
        a.set_ylabel('Absolute size error (m)')
        a.set_title(title)
        a.legend(fontsize=8)
    for i, b in enumerate(BRANCHES):
        values = [summary['image_residuals_on_same_target'][target][b] for target in BRANCHES]
        values += [summary['nonblind_image_mean'][b]]
        ax[1, 1].bar(np.arange(3)+(i-.5)*.35, values, width=.33, color=COLORS[i], label=LABELS[i])
    ax[1, 1].set_xticks([0, 1, 2], ['Original fit\ntarget', 'Revised fit\ntarget', '44/45 nonblind\ncommon target'])
    ax[1, 1].set_ylabel('Image loss (working pixels)')
    ax[1, 1].set_title('D  Frozen models on each common image target')
    ax[1, 1].legend(fontsize=8)
    for a in ax.flat:
        a.set_axisbelow(True)
        a.grid(axis='y', alpha=.2)
    fig.suptitle('P5: one observation revision, unchanged 100-step budget\nR = reconstructed surface; T = scoring surface. Seen sequence, no truth feedback.', fontsize=13)
    fig.savefig(BASE / 'p5_summary.png', dpi=200)
    fig.savefig(BASE / 'p5_summary.pdf')
    plt.close(fig)
    print(json.dumps({'summary': str(BASE / 'summary_results.json'), 'figure': str(BASE / 'p5_summary.png')}))


if __name__ == '__main__':
    main()
