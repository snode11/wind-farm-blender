"""Rebuild only review graphics from saved audit rows; never recalculate a mask."""
import csv
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from wfrl.nrel_reconstruction import root_rgb_audit as audit
from wfrl.nrel_reconstruction.surface_audit import read_mesh, sha256

out = Path(__file__).resolve().parent
repo = out.parents[3]
source = repo/'outputs/nrel-video-single-blade/20261002-second'
report_path = out/'root_rgb_audit.json'
report_sha = sha256(report_path)
report = json.loads(report_path.read_text())
assert all(sha256(path) == expected for path, expected in report['input_sha256'].items())
obs = json.loads((source/'observations/observations.json').read_text())
annotations = {(a['camera_id'], a['frame_id']): a for a in json.loads((source/'observations/annotations.json').read_text())['annotations']}
vertices, faces = read_mesh(source/'reconstruction/T1_B1.ply')
samples = list(csv.DictReader((out/'native_root_samples.csv').open()))
for row in samples:
    for name in ('u_px', 'v_px', 'z_m'): row[name] = float(row[name])
    for name in ('ray_visible', 'independent_contour_valid'): row[name] = row[name] == 'True'
for o in obs:
    camera, frame = o['camera_id'], o['frame_id']
    if camera not in ('C1', 'C2'): continue
    key = f'{camera}_{frame:06d}'
    rgb = np.asarray(Image.open(out/f'{key}_native_rgb.png'))
    assert hashlib.sha256(rgb.tobytes()).hexdigest() == annotations[(camera, frame)]['rgb_sha256']
    rows = [r for r in samples if r['camera'] == camera and int(r['frame']) == frame]
    observed = np.load(o['contour_points_path'], allow_pickle=False)
    valid = np.asarray(Image.open(o['contour_valid_path'])) > 0
    overlay = audit.native_overlay(rgb, o, annotations[(camera, frame)], vertices, faces, [], rows, observed, valid)
    overlay.save(out/f'{key}_native_overlay.png')
    crops = {'upper_boundary': (300, 110, 1250, 350), 'lower_boundary': (300, 770, 1250, 1010),
             'root_upper_boundary': (1050, 110, 1700, 470), 'root_lower_boundary': (1050, 650, 1700, 1000)} if camera == 'C1' else {'entry_boundary': (0, 0, 850, 340), 'lower_entry': (0, 740, 850, 1080)}
    for name, box in crops.items():
        w, h = box[2]-box[0], box[3]-box[1]
        canvas = Image.new('RGB', (w, h*2+80), (14, 23, 31)); draw = ImageDraw.Draw(canvas)
        canvas.paste(Image.fromarray(rgb).crop(box), (0, 40)); canvas.paste(overlay.crop(box), (0, h+80))
        draw.text((10, 8), f'{key} {name} | original RGB', font=audit.font(20), fill='white')
        draw.text((10, h+48), 'Same native pixels + saved domain / outline + model projection', font=audit.font(20), fill='white')
        canvas.save(out/f'{key}_{name}_crop.png')
assert sha256(report_path) == report_sha
assert all(sha256(path) == expected for path, expected in report['input_sha256'].items())
(out/'visual_redraw.json').write_text(json.dumps({
    'purpose': 'Improve color legend and add correctly centered local root crops; no numerical re-audit or segmentation.',
    'numeric_report_sha256_unchanged': report_sha,
    'source_sha256': {str(Path(audit.__file__).resolve()): sha256(audit.__file__), str(Path(__file__).resolve()): sha256(__file__)},
    'overlay_format': 'Top-left original native pixel field is 1920x1080; 125-pixel bottom caption appended without image resampling.',
    'legend': {'bright_green': 'eligible model root silhouette', 'violet': 'model root silhouette excluded by saved independent contour domain', 'cyan': 'saved RGB observed boundary', 'amber': 'saved review exclusion domain', 'green_dashed': 'full z=6.15 and12.30 cut', 'blue_dashed': 'full z=9.225 cut', 'red_tip': 'original RGB blade paint, not an audit classification'},
    'inputs_unchanged': True}, indent=2)+'\n')
print('Redrew eight overlays and 24 local review crops; numerical report and all historical inputs unchanged.')
