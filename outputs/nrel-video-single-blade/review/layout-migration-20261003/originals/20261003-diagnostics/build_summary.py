"""Rebuild the diagnostic figure from frozen audit JSON; no fitting."""
from pathlib import Path
import json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

BASE = Path(__file__).resolve().parent
surface_path = BASE / 'surface/final/surface_audit.json'
surface = json.loads(surface_path.read_text())
parameters = json.loads((BASE / 'parameters/parameter_audit.json').read_text())
model = surface['models']['formal_reconstruction']
groups = ['fit', 'all_reviewed_nonblind_diagnostic']
foreground = np.array([model['groups'][g]['visible_F_any']['fraction'] * 100 for g in groups])
visible = np.array([model['groups'][g]['visible_any']['fraction'] * 100 for g in groups])
heat = np.full((3, 4), np.nan)
for r in parameters['records']:
    if r['variant'] != 'baseline' or r['width'] != 640 or r['sampling'] != 'default':
        continue
    heat[int(r['camera_id'][1:])-1, r['frame_id']-42] = r['regions'][1]['o2c']['mean_native_px'] or np.nan

plt.rcParams.update({'font.size': 11, 'axes.spines.top': False, 'axes.spines.right': False})
fig, axes = plt.subplots(1, 2, figsize=(12.5, 5.2), gridspec_kw={'width_ratios': [1.1, 1]})
fig.patch.set_facecolor('#f7f8fa')
fig.suptitle('NREL 5MW | Frozen-model evidence audit', x=.055, ha='left', fontsize=19, weight='bold')
ax = axes[0]
ax.barh([0, 1], foreground, color='#238a83', label='Visible + RGB foreground')
ax.barh([0, 1], visible-foreground, left=foreground, color='#e8b44c', label='Visible without F support')
ax.barh([0, 1], 100-visible, left=visible, color='#cbd0d9', label='Never model-visible')
for i in range(2):
    ax.text(foreground[i]/2, i, f'{foreground[i]:.1f}%', ha='center', va='center', color='white', weight='bold')
    ax.text((100+visible[i])/2, i, f'{100-visible[i]:.1f}%', ha='center', va='center', color='#263348', weight='bold')
ax.set_yticks([0, 1], ['Fit: 42 / 43', 'All: 42 / 43 / 44 / 45'])
ax.invert_yaxis()
ax.set_xlim(0, 100)
ax.set_xlabel('Area quadrature on the final mesh (%)')
ax.set_title('A. Model-conditioned surface projection', loc='left', fontsize=12, pad=18)
handles, labels = ax.get_legend_handles_labels()
fig.legend(handles, labels, loc='upper left', bbox_to_anchor=(.155, .18), ncol=3, frameon=False, fontsize=9)
fig.text(.16, .22, 'This is not recovered area.', fontsize=10, color='#9c4329')

ax = axes[1]
cmap = plt.get_cmap('YlOrRd').copy()
cmap.set_bad('#e5e7ec')
im = ax.imshow(heat, cmap=cmap, vmin=0, vmax=25, aspect='auto')
for i in range(3):
    for j in range(4):
        ax.text(j, i, 'None' if np.isnan(heat[i,j]) else f'{heat[i,j]:.2f}',
                ha='center', va='center', fontsize=10, color='white' if heat[i,j]>17 else '#263348')
ax.set_xticks(range(4), ['42\nfit', '43\nfit', '44\nnonblind', '45\nnonblind'])
ax.set_yticks(range(3), ['C1', 'C2', 'C3'])
ax.set_title('B. Root-band observed-to-model contour residual', loc='left', fontsize=12, pad=18)
fig.colorbar(im, ax=ax, fraction=.045, pad=.035, label='Native image pixels')
fig.text(.055, .025, '3 points/face; self-occlusion checked; external occlusion not fully certified. Root band: z = 6.15-12.30 m.\n'
         'Residuals use the retained 640 x 360 contour backend; None = no samples, not zero error.', fontsize=9, color='#515b6b')
fig.subplots_adjust(left=.16, right=.93, bottom=.36, top=.79, wspace=.4)
fig.savefig(BASE / 'diagnostic_summary.png', dpi=170, facecolor=fig.get_facecolor())
print(BASE / 'diagnostic_summary.png')
