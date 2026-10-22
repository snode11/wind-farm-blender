#!/usr/bin/env python3
"""Read-only P0 evidence audit and all-frame RGB contact sheets; no fitting."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wfrl.nrel_reconstruction.artifact_paths import relocated_path
from wfrl.nrel_reconstruction.dataset import load_bundle, iter_rgb, probe_video, write_json
from wfrl.nrel_reconstruction.diagnostics import read_mesh_ply


def signature(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def snapshot(first, second):
    """Freeze relevant existing files, including evidence not historically signed."""
    paths = set(second.rglob('*'))
    for relative in ('formal/algorithm-input', 'formal/reconstruction', 'formal/evaluation-only'):
        paths.update((first / relative).rglob('*'))
    paths.update(ROOT.joinpath('wfrl/nrel_reconstruction').glob('*.py'))
    paths.update([first / 'README.md', first / 'formal/manifest.json',
                  ROOT / 'scripts/nrel_single_blade.py',
                  ROOT / 'docs/proposal/NREL5MW_三摄离线视频与单叶片三维重建验证方案_v1.0.md'])
    return {str(p.relative_to(ROOT)): {'bytes': p.stat().st_size, 'sha256': signature(p)}
            for p in sorted(paths) if p.is_file() and '__pycache__' not in p.parts
            and p.name not in {'surface_audit.py', 'area_audit.py', 'parameter_audit.py'}}


def verify_existing(root, expected):
    return [{'path': str((root / path).relative_to(ROOT)), 'expected_sha256': sha,
             'actual_sha256': signature(root / path), 'matches': signature(root / path) == sha}
            for path, sha in expected.items()]


def section_points(vertices, z):
    rings = vertices.reshape(25, 12, 3)
    levels = rings[:, 0, 2]
    i = int(np.clip(np.searchsorted(levels, z) - 1, 0, len(levels) - 2))
    t = (z - levels[i]) / (levels[i + 1] - levels[i])
    return rings[i] * (1 - t) + rings[i + 1] * t


def view_directions(calibrations, motion, vertices, frame):
    centre = section_points(vertices, 9.225).mean(axis=0)
    result = {}
    for camera in ('C1', 'C2', 'C3'):
        transform = np.asarray(calibrations[camera, frame]['T_camera_cv_from_world']) @ np.asarray(motion[frame]['T_world_from_blade_root'])
        delta = np.linalg.inv(transform)[:3, 3] - centre
        result[camera] = delta / np.linalg.norm(delta)
    return result


def angle_degrees(a, b):
    return float(np.degrees(np.arccos(np.clip(a @ b, -1, 1))))


def save_candidate_geometry(calibrations, motion, vertices, out):
    # This list is the retained human/assistant thumbnail-screening decision,
    # not a new automatic B1 detector or a certified annotation.
    reference = view_directions(calibrations, motion, vertices, 43)
    candidates = []
    for camera, frames in [('C1', [40, 41, 46, 47, 50, 52]), ('C2', [40, 41, 46, 47])]:
        for frame in frames:
            candidates.append({'camera_id': camera, 'frame_id': frame, 'sim_time_s': motion[frame]['sim_time_s'],
                'angle_from_frame43_deg': angle_degrees(reference[camera], view_directions(calibrations, motion, vertices, frame)[camera]),
                'status': 'RGB_REVIEW_CANDIDATE_NOT_ANNOTATED_NOT_FIT'})
    write_json(out / 'candidate_review.json', {
        'method': 'Retained Codex visual screening of all 300 decoded RGB thumbnails; direction angle uses permitted rigid pose and frozen v2 ring centroid.',
        'candidates': candidates, 'C3': 'No extra candidate in this B1 passage beyond 42-45; other blade passages not admitted as B1.',
        'limits': ['Candidates are not certified better observations; full-resolution identity and boundary review is required.',
                   'Existing exclusion domains may still remove root boundary.',
                   'No new masks or fit frames; static shape validity away from t_ref is unverified.']})
    frames = []
    for frame in (42, 43, 44, 45):
        directions = view_directions(calibrations, motion, vertices, frame)
        frames.append({'frame_id': frame, 'root_local_to_camera_unit_directions': {c: d.tolist() for c, d in directions.items()},
            'ray_angles_deg': {f'{a}-{b}': angle_degrees(directions[a], directions[b])
                              for a, b in [('C1', 'C2'), ('C1', 'C3'), ('C2', 'C3')]}})
    write_json(out / 'root_view_angles.json', {'root_ring_z_m': 9.225,
        'method': 'Frozen v2 polygonal ring centroid plus declared camera/root rigid transforms.', 'frames': frames,
        'interpretation': 'Direction geometry only; a camera without trusted root contours does not contribute a constraint. Not real-camera uncertainty or a triangulation condition number.'})


def main(output):
    first = ROOT / 'outputs/nrel-video-single-blade/stages/01-first-run'
    second = ROOT / 'outputs/nrel-video-single-blade/stages/02-second-run'
    out = Path(output).resolve()
    if out.exists() and any(out.iterdir()):
        raise FileExistsError('Use a fresh empty output directory')
    out.mkdir(parents=True, exist_ok=True)
    report_path = out / 'delivery_audit.json'
    if report_path.exists():
        raise FileExistsError(report_path)
    before = snapshot(first, second)
    write_json(out / 'baseline_before.json', before)
    checks = verify_existing(first / 'formal', json.loads((first / 'formal/manifest.json').read_text())['artifacts_sha256'])
    checks += verify_existing(second, json.loads((second / 'model_freeze.json').read_text())['sha256'])
    if not all(c['matches'] for c in checks):
        write_json(out / 'signature_mismatches.json', [c for c in checks if not c['matches']])
        raise RuntimeError('Existing signed evidence differs; inspect signature_mismatches.json')
    source_checks = verify_existing(ROOT, json.loads((second / 'reconstruction/implementation_at_launch.json').read_text())['sha256'])
    # Source may have changed after launch while the frozen outputs remained
    # intact. Report such drift rather than declaring historical source identity.
    checks += source_checks
    final_source_checks = verify_existing(ROOT, json.loads((second / 'manifest.json').read_text())['source_sha256'])
    write_json(out / 'final_source_reconciliation.json', {
        'checks': final_source_checks, 'matches': sum(c['matches'] for c in final_source_checks),
        'count': len(final_source_checks),
        'interpretation': 'Launch snapshot and final delivery snapshot are different evidence times. boundary_metrics.py matches the final delivery snapshot, not the fit-launch snapshot. Existing model/optimizer/contour files and historical models remain unchanged.'})
    bundle = first / 'formal/algorithm-input'
    data, calibrations, motion = load_bundle(bundle)
    observations = json.loads((second / 'observations/annotations.json').read_text())['annotations']
    by_view = {(o['camera_id'], o['frame_id']): o for o in observations}
    vertices, _ = read_mesh_ply(second / 'reconstruction/T1_B1.ply')
    sections = {str(z): section_points(vertices, z) for z in (9.225, 30.75, 52.275)}
    decoder_checks, candidates = [], []
    for camera in ('C1', 'C2', 'C3'):
        path = bundle / f'{camera}.mp4'
        info = probe_video(path)
        stream, frames = info['streams'][0], info['frames']
        assert len(frames) == 100
        assert (stream['width'], stream['height']) == (1920, 1080)
        assert all(abs(float(f['best_effort_timestamp_time']) - i / 20) < 1e-6 for i, f in enumerate(frames))
        sheet = Image.new('RGB', (2400, 1590), '#191d24')
        draw = ImageDraw.Draw(sheet)
        selected = []
        count = 0
        for i, rgb in enumerate(iter_rgb(path, 1920, 1080)):
            count += 1
            if (camera, i) in by_view:
                actual = hashlib.sha256(rgb.tobytes()).hexdigest()
                annotation = by_view[(camera, i)]
                assert actual == annotation['rgb_sha256']
                assert signature(path) == annotation['source_video_sha256']
                selected.append({'frame_id': i, 'rgb_sha256': actual, 'matches_annotation': True})
            cal = calibrations[(camera, i)]
            transform = np.asarray(cal['T_camera_cv_from_world']) @ np.asarray(motion[i]['T_world_from_blade_root'])
            item = {'camera_id': camera, 'frame_id': i, 'sim_time_s': motion[i]['sim_time_s'],
                    'role': 'fit' if i in (42, 43) else 'nonblind_temporal' if i in (44, 45) else 'new_diagnostic',
                    'sections': {}}
            thumb = Image.fromarray(rgb).resize((240, 135), Image.Resampling.LANCZOS)
            td = ImageDraw.Draw(thumb)
            for z, points in sections.items():
                xyz = points @ transform[:3, :3].T + transform[:3, 3]
                h = xyz @ np.asarray(cal['K']).T
                uv = h[:, :2] / np.where(np.abs(h[:, 2:3]) > 1e-10, h[:, 2:3], np.nan)
                inside = (xyz[:, 2] > .01) & (uv[:, 0] >= -.5) & (uv[:, 0] < 1919.5) & (uv[:, 1] >= -.5) & (uv[:, 1] < 1079.5)
                item['sections'][z] = {'in_frame_vertices': int(inside.sum()), 'total_vertices': len(points)}
                if z == '9.225':
                    for xy in uv[inside]:
                        x, y = (xy + .5) / 8 - .5
                        td.ellipse((x-1, y-1, x+1, y+1), fill='#ffcd36')
            candidates.append(item)
            x, y = i % 10 * 240, i // 10 * 159
            sheet.paste(thumb, (x, y + 24))
            draw.text((x+4, y+5), f'{camera} #{i:02d} t={motion[i]["sim_time_s"]:.3f}', fill='white')
        assert count == 100
        sheet.save(out / f'{camera}_all100.jpg', quality=90)
        decoder_checks.append({'camera_id': camera, 'decoded_frames': count, 'pts_verified': True,
                               'source_sha256': signature(path), 'selected_rgb_checks': selected})
    write_json(out / 'all_frame_screen.json', {
        'method': 'Actual signed MP4 RGB; yellow dots are frozen v2 z=9.225 ring under declared rigid motion.',
        'limits': 'Model-conditioned frustum test only; no occlusion, identity or usable-contour certification. Static v2 shape outside t_ref is not exact flexible geometry. Other blades may be visible. No annotations or fitting frames changed.',
        'frames': candidates})
    save_candidate_geometry(calibrations, motion, vertices, out)
    after = snapshot(first, second)
    assert before == after, 'Existing source or evidence changed during audit'
    write_json(report_path, {'schema': 'nrel-existing-evidence-audit.v1',
        'utc': datetime.now(timezone.utc).isoformat(), 'date_local': '2026-10-03',
        'historical_signature_checks': checks, 'historical_signature_check_count': len(checks),
        'signature_matches': sum(c['matches'] for c in checks),
        'source_drift': [c for c in source_checks if not c['matches']],
        'baseline_file_count': len(before), 'baseline_unchanged': True,
        'decode_checks': decoder_checks, 'observation_count': len(observations),
        'isolation_evidence': {'file': str(second / 'isolation_probe.json'),
            'state': 'historical probe retained and hash inventoried; old fit process not rerun'},
        'geometry_scores': 'existing reports retained; not recomputed by P0',
        'actions': {'capture': False, 'encode': False, 'fit': False, 'truth_for_fit': False}})
    print(json.dumps({'output': str(out), 'signature_checks': len(checks), 'baseline_files': len(before),
                      'decoded_frames': sum(c['decoded_frames'] for c in decoder_checks),
                      'selected_rgb_checks': sum(len(c['selected_rgb_checks']) for c in decoder_checks)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    main(parser.parse_args().output)
