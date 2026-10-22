#!/usr/bin/env python3
"""Audit static sectional reference identities without reading flexible truth.

The source material offset is an algebraic relation, not a fitted model-tip map.
No offset from this audit is exported to or applied by the image diagnostic.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def declared_table(text, count_key, header, columns):
    """Read exactly the declared rows; trailing historical numbers are unused."""
    import numpy as np
    lines = text.splitlines()
    counts = [int(m.group(1)) for line in lines
              if (m := re.match(r'\s*(\d+)\s+' + re.escape(count_key) + r'\b', line))]
    starts = [i for i, line in enumerate(lines) if line.strip().startswith(header)]
    if len(counts) != 1 or counts[0] < 1 or len(starts) != 1:
        raise ValueError('Missing or ambiguous declared static table')
    start = starts[0] + 2  # Skip the column units row.
    rows = []
    for line in lines[start:start + counts[0]]:
        try:
            rows.append([float(v.replace('D', 'E')) for v in line.split()[:columns]])
        except ValueError as error:
            raise ValueError('Invalid declared static table row') from error
    array = np.asarray(rows, dtype=float)
    if array.shape != (counts[0], columns) or not np.isfinite(array).all():
        raise ValueError('Incomplete or nonfinite declared static table')
    return array


def material_offset(aerodynamic_center, structural_point, *, anchor_span_m):
    """An offset must be anchored to the exact source material station."""
    import numpy as np
    a, b = np.asarray(aerodynamic_center, float), np.asarray(structural_point, float)
    if (a.shape != (3,) or b.shape != (3,) or not np.isfinite([a, b]).all()
            or not np.isfinite(anchor_span_m)
            or not np.isclose(a[2], anchor_span_m, atol=1e-10, rtol=0)):
        raise ValueError('Source offset requires its declared aerodynamic station')
    return b - a


def run(args):
    import numpy as np
    from wfrl.nrel_reconstruction.dynamic_geometry import DynamicBladeReference
    from wfrl.nrel_reconstruction.model import BladeModel

    paths = {name: Path(getattr(args, name)).resolve() for name in
             ('aerodyn', 'beamdyn', 'reference', 'source_run', 'geometry_scalars', 'model')}
    signatures = {str(p): digest(p) for p in paths.values()}
    signatures[str(Path(__file__).resolve())] = digest(__file__)
    for name in ('model.py', 'dynamic_geometry.py'):
        p = ROOT / 'wfrl/nrel_reconstruction' / name
        signatures[str(p)] = digest(p)
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=False)
    frozen = {'schema': 'nrel-static-tip-map-inputs.v1', 'files': signatures,
              'scope': 'Static tables, static source provenance and saved video model only',
              'flexible_truth_read': False}
    (out / 'input_freeze.json').write_text(json.dumps(frozen, indent=2) + '\n')
    aero = declared_table(paths['aerodyn'].read_text(), 'NumBlNds', 'BlSpn', 7)
    beam = declared_table(paths['beamdyn'].read_text(), 'kp_total', 'kp_xr', 4)
    reference = json.loads(paths['reference'].read_text())
    source = json.loads(paths['source_run'].read_text())['blade_config']
    geometry = json.loads(paths['geometry_scalars'].read_text())
    if reference.get('schema') != 'wfrl.blade-reference.v1':
        raise ValueError('Unsupported static structural reference')
    for name in ('aerodyn', 'beamdyn'):
        key = '5MW_Baseline/' + paths[name].name
        if digest(paths[name]) != source['inputs'].get(key):
            raise ValueError('Static table differs from registered package input hash')
    curve = np.asarray(reference['curve'], float)
    if beam.shape != curve.shape or not np.allclose(beam, curve, atol=1e-10, rtol=0):
        raise ValueError('BeamDyn table and packaged structural reference differ')
    if not np.array_equal(curve, np.asarray(source['reference_curve'], float)):
        raise ValueError('Package static reference and source-run definition differ')
    expected_aero = np.asarray(geometry['blade_stations'], float).copy()
    expected_aero[:, 1] -= (expected_aero[:, 0] / beam[-1, 2]) ** 2
    if aero.shape != expected_aero.shape or not np.allclose(aero, expected_aero, atol=1e-9, rtol=0):
        raise ValueError('AeroDyn table does not match static station + prebend formula')
    if not np.all(np.diff(aero[:, 0]) > 0) or not np.all(np.diff(beam[:, 2]) > 0):
        raise ValueError('Source material stations are not strictly increasing')
    hub_radius = float(geometry['scalars']['HubRad'])
    structural_b = beam[-1, :3]
    structural_hub_local = structural_b + [0., 0., hub_radius]
    if not np.allclose(structural_hub_local, reference['tip_local_m'], atol=1e-10, rtol=0):
        raise ValueError('Blade-root versus hub-local structural tip mismatch')
    terminal = aero[-1]
    ac_b = np.array([terminal[1], terminal[2], terminal[0]])
    delta = material_offset(ac_b, structural_b, anchor_span_m=terminal[0])
    candidate = DynamicBladeReference.from_model_state(paths['model'])
    metadata = json.loads(paths['model'].read_text())['models']['T1_B1']
    template = BladeModel(candidate.span_samples, candidate.ring_samples,
                          len(metadata['control_z_m']), interpolation=metadata['interpolation'])
    model_chord = float(template.base_chord[-1]) * float(np.exp(metadata['control_chord_log_scale'][-1]))
    result = {
        'schema': 'nrel-static-tip-mapping-audit.v1',
        'status': 'STATIC_SOURCE_RELATION_CHECKED_MODEL_MAPPING_NOT_ESTABLISHED',
        'static_source_identity': 'Two DAT hashes match package source-run registered inputs; numeric tables match package static references',
        'structural_tip_root_B_m': structural_b.tolist(),
        'structural_tip_hub_local_m': structural_hub_local.tolist(),
        'source_terminal_aerodynamic_center_B_m': ac_b.tolist(),
        'source_ac_to_structural_offset_B_m': delta.tolist(),
        'offset_anchor_span_m': float(terminal[0]),
        'structural_span_m': float(structural_b[2]),
        'station_difference_m': float(structural_b[2] - terminal[0]),
        'source_terminal_chord_m': float(terminal[5]),
        'source_terminal_twist_deg': float(terminal[4]),
        'candidate_endpoint_span_m': candidate.span_m,
        'candidate_endpoint_chord_m': model_chord,
        'candidate_endpoint_twist_deg': float(np.degrees(template.twist[-1].item())),
        'source_point_relation': 'For the same source affine section map A=[L,t], b_current=AC_current+L@(b_static-AC_static). L is not automatically rigid.',
        'perimeter_relation': 'b_current=mean_current+L@(b_static-mean_static) only for the same original material samples/weights.',
        'candidate_mapping': 'NOT_ESTABLISHED',
        'offset_applied_to_estimator': False,
        'reasons': [
            'Video model reference-axis endpoint has different station, chord and twist priors.',
            'The loaded video reference-axis/section frame has not been matched to the source material axis/frame.',
            'Adding the complete source offset at model s=61.5 would double-count the source station difference.',
            'An equal-weight model ellipse centroid is not the source 399-sample perimeter mean.',
            'Static reference identity does not verify the dynamic model-to-structural-point mapping.'
        ],
        'forbidden_inputs_read': [], 'dynamic_truth_comparison': False,
        'static_table_max_error_m': {'beam_reference': float(abs(beam-curve).max()),
                                    'aero_station_formula': float(abs(aero-expected_aero).max())},
        'files': signatures,
    }
    changed = [p for p, h in signatures.items() if digest(p) != h]
    (out / 'mapping_audit.json').write_text(json.dumps(result, indent=2) + '\n')
    (out / 'post_run_integrity.json').write_text(json.dumps({'unchanged': not changed, 'changed': changed}, indent=2) + '\n')
    if changed:
        raise ValueError('Static mapping audit input changed during execution')
    print(json.dumps({k: result[k] for k in ('status', 'source_ac_to_structural_offset_B_m', 'candidate_endpoint_chord_m', 'candidate_mapping')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('aerodyn', 'beamdyn', 'reference', 'source-run', 'geometry-scalars', 'model', 'output'):
        parser.add_argument('--' + name, required=True)
    run(parser.parse_args())
