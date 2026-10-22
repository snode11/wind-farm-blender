"""Development-only observability experiment on independent unloaded surfaces.

Three polynomial TEST shapes are illustrative coordinates, not identified or
manufacturer-supplied structural modes. No saved dynamic blade transform, true
hit station, measured range, reference clearance or source tip enters this model.
The output cannot replace TLS or provide a physically validated clearance bound.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.lidar.compare_dual_beam_methods import independent_clearance
from wfrl.camera_video.data import blade_root_frame
from wfrl.lidar.dual_beam import validate_calibration
from wfrl.lidar.dual_beam_replay import digest
from wfrl.lidar.moving_tower import background_hit
from wfrl.lidar.physics import first_hit

PHASES_DEG = (165., 170., 175., 180., 185., 190., 195.)
PITCHES_DEG = (0., 5., 10.)
POWERS = (2, 3, 4)
FD_STEP_M = .001
PROBE_COEFFICIENT_NORM_M = 1.
STATE_SCALE_M = 1.
RANGE_SCALE_M = 1.
RANK_RELATIVE_TOLERANCE = 1e-8
RANK_ABSOLUTE_TOLERANCE = 1e-10
DERIVATIVE_CONVERGENCE_ABSOLUTE = 1e-5
DERIVATIVE_CONVERGENCE_RELATIVE = 1e-5
REST_NACELLE = np.column_stack((np.eye(3), np.zeros(3)))


def test_shape_weights(span_m, tip_span_m, root_span_m=0.):
    """Dimensionless, root-clamped polynomials; each has unit tip displacement."""
    span = np.asarray(span_m, float)
    if (not np.isfinite(span).all() or not np.isfinite(tip_span_m)
            or not np.isfinite(root_span_m) or tip_span_m <= root_span_m
            or np.any(span < root_span_m-1e-3) or np.any(span > tip_span_m+1e-3)):
        raise ValueError('Invalid independent reference span')
    coordinate = np.clip((span-root_span_m)/(tip_span_m-root_span_m), 0., 1.)
    return np.stack([coordinate**power for power in POWERS], axis=-1)


def world_to_rest_local(points, scalars, blade_id):
    """Remove the reference rigid pose once; never apply saved section transforms."""
    hub, axes = blade_root_frame(scalars, [0.]*6, blade_id, REST_NACELLE)
    local = (np.asarray(points, float)-hub) @ axes
    residual = float(np.max(np.abs(hub+local@axes.T-points)))
    if residual > 1e-10:
        raise ValueError('Independent rest-frame inverse round trip failed')
    return local, residual


@dataclass
class IndependentModel:
    local_blades: np.ndarray
    shape_weights: np.ndarray
    blade_triangles: np.ndarray
    tower: np.ndarray
    tower_triangles: np.ndarray
    tip_local: np.ndarray
    scalars: dict
    origins: np.ndarray
    directions: np.ndarray
    limits: object

    def forward(self, amplitudes_m, phase_deg, pitch_deg):
        """Deform supplied shape, then re-intersect every opaque first-hit object.

        The three amplitudes describe blade 1 only; the other two nominal blades
        remain competing occluders. This is an analytic pose case, not a time
        sample of the source simulation. Known yaw/nacelle pose is fixed at rest.
        """
        a = np.asarray(amplitudes_m, float)
        if a.shape != (3,) or not np.isfinite(a).all():
            raise ValueError('Expected three finite amplitudes in metres')
        local = self.local_blades.copy()
        local[0, ..., 0] += self.shape_weights[0] @ a
        pose = [0., float(phase_deg), 0., float(pitch_deg), float(pitch_deg), float(pitch_deg)]
        surfaces = []
        for blade in (1, 2, 3):
            hub, axes = blade_root_frame(self.scalars, pose, blade, REST_NACELLE)
            surfaces.append((hub+local[blade-1]@axes.T).reshape(-1, 3))
        hub, axes = blade_root_frame(self.scalars, pose, 1, REST_NACELLE)
        tip = self.tip_local.copy()
        tip[0] += float(np.sum(a))  # f_k(1)=1; fixed unloaded tip, no terminal motion.
        tip_world = hub+axes@tip
        try:
            clearance = independent_clearance(tip_world, self.tower, self.tower_triangles)
            clearance_reason = 'valid_analytic_model'
        except ValueError:
            clearance, clearance_reason = None, 'no_nominal_tower_section_at_tip_height'
        observations = [ray_observation(self.origins[i], self.directions[i], surfaces,
                                        self.blade_triangles, self.tower, self.tower_triangles,
                                        self.limits) for i in (1, 2)]
        return dict(observations=observations, clearance_m=clearance,
                    clearance_reason=clearance_reason, model_tip_m=tip_world.tolist())


def ray_observation(origin, direction, blades, blade_triangles, tower, tower_triangles, limits):
    """Object, blade and facet identity are recorded before range filtering."""
    hits = []
    bg = background_hit(origin, direction, tower, tower_triangles)
    if bg:
        hits.append((bg[0], bg[2], None, None))
    for blade, surface in enumerate(blades, 1):
        hit = first_hit(origin, direction, surface, blade_triangles)
        if hit:
            hits.append((hit[0], 'blade', blade, hit[2]))
    if not hits:
        return dict(slant_range_m=None, first_object=None, blade_id=None, triangle_id=None,
                    valid=False, reason='no_return', range_branch='non_blade')
    value, kind, blade, triangle = min(hits, key=lambda row: row[0])
    in_range = limits.min_range_m <= value <= limits.max_range_m
    valid = kind == 'blade' and in_range
    return dict(slant_range_m=float(value), first_object=kind, blade_id=blade,
                triangle_id=int(triangle) if triangle is not None else None,
                valid=valid, reason='valid' if valid else 'out_of_range' if not in_range else kind+'_first',
                range_branch=('near' if value < 12. else 'far') if kind == 'blade' else 'non_blade')


def branch_signature(observation):
    return tuple(observation.get(k) for k in
                 ('first_object', 'blade_id', 'triangle_id', 'valid', 'range_branch'))


def two_expected_ranges(state):
    obs = state['observations']
    if len(obs) != 2 or any(not o['valid'] or o['blade_id'] != 1 for o in obs):
        return None
    return np.asarray([o['slant_range_m'] for o in obs], float)


def finite_difference(forward, step_m=FD_STEP_M):
    """Central range/clearance derivatives only when all first-hit branches hold."""
    if not np.isfinite(step_m) or step_m <= 0:
        raise ValueError('Positive finite difference step required')
    nominal = forward(np.zeros(3))
    ranges = two_expected_ranges(nominal)
    result = dict(nominal=nominal, step_m=float(step_m), valid=False, reason=None, columns=[])
    if ranges is None or nominal['clearance_m'] is None:
        result['reason'] = 'nominal_ranges_or_clearance_unavailable'
        return result
    j, g = np.zeros((2, 3)), np.zeros(3)
    signatures = [branch_signature(o) for o in nominal['observations']]
    for column in range(3):
        delta = np.zeros(3); delta[column] = step_m
        plus, minus = forward(delta), forward(-delta)
        plus_range, minus_range = two_expected_ranges(plus), two_expected_ranges(minus)
        changed = [name for name, state in (('plus', plus), ('minus', minus))
                   if [branch_signature(o) for o in state['observations']] != signatures]
        ok = (not changed and plus_range is not None and minus_range is not None
              and plus['clearance_m'] is not None and minus['clearance_m'] is not None)
        result['columns'].append(dict(mode_index=column, plus=plus, minus=minus,
                                       branch_changes=changed, valid=ok))
        if ok:
            j[:, column] = (plus_range-minus_range)/(2*step_m)
            g[column] = (plus['clearance_m']-minus['clearance_m'])/(2*step_m)
    if not all(c['valid'] for c in result['columns']):
        result['reason'] = 'perturbation_invalid_or_first_hit_branch_changed'
        return result
    if not np.isfinite(j).all() or not np.isfinite(g).all():
        raise ValueError('Nonfinite finite difference')
    result.update(valid=True, reason='valid_local_derivatives',
                  range_jacobian_m_per_m=j.tolist(), clearance_gradient_m_per_m=g.tolist())
    return result


def nullspace_diagnostic(jacobian, gradient, state_scale_m=STATE_SCALE_M,
                         range_scale_m=RANGE_SCALE_M):
    """Rank on explicitly normalized Euclidean metres, not a sensor noise model.

    q=a/state_scale_m; y=r/range_scale_m; normalized J=dr/da*s/r.
    Null directions are unit coefficient L2 vectors. All three state coordinates
    have the same declared scale; no singular value mixes dimensional columns.
    """
    j, g = np.asarray(jacobian, float), np.asarray(gradient, float)
    if (j.shape != (2, 3) or g.shape != (3,) or not np.isfinite(j).all()
            or not np.isfinite(g).all() or not np.isfinite(state_scale_m)
            or not np.isfinite(range_scale_m) or min(state_scale_m, range_scale_m) <= 0):
        raise ValueError('Invalid Jacobian/gradient or metric scale')
    normalized = j*state_scale_m/range_scale_m
    _, singular, vt = np.linalg.svd(normalized, full_matrices=True)
    threshold = max(RANK_ABSOLUTE_TOLERANCE, RANK_RELATIVE_TOLERANCE*singular[0])
    rank = int(np.count_nonzero(singular > threshold))
    basis = vt[rank:].T
    projected = basis @ (basis.T@g)
    sensitivity = float(np.linalg.norm(projected))
    direction = projected/sensitivity if sensitivity > 1e-12 else basis[:, 0]
    return dict(normalized_range_jacobian=normalized.tolist(), normalized_singular_values=singular.tolist(),
                rank_threshold=threshold, rank=rank, nullity=3-rank, nullspace_basis=basis.T.tolist(),
                unit_coefficient_null_direction=direction.tolist(),
                null_range_residual_m_per_m=float(np.linalg.norm(j@direction)),
                null_clearance_sensitivity_m_per_m=float(g@direction),
                maximum_null_clearance_sensitivity_m_per_m=sensitivity,
                metric=dict(state_scale_m=state_scale_m, range_scale_m=range_scale_m,
                            definition='q=a/state_scale; y=r/range_scale; Euclidean L2; equal scales; no hardware noise model'))


def analytic_cases():
    """Freeze poses before any return validity or actual source result is read."""
    return [dict(id=f'phase{phase:g}_pitch{pitch:g}', phase_deg=phase, pitch_deg=pitch)
            for pitch in PITCHES_DEG for phase in PHASES_DEG]


def audit_case(model, case):
    forward = lambda a: model.forward(a, case['phase_deg'], case['pitch_deg'])
    fd = finite_difference(forward)
    result = dict(**case, finite_difference=fd, clearance_output_status='UNKNOWN_UNVALIDATED_SHAPE_PRIOR',
                  nominal_range_pair_available=two_expected_ranges(fd['nominal']) is not None,
                  observability=None, half_step_check=None, half_step_finite_difference=None,
                  finite_null_probes=[])
    if not fd['valid']:
        return result
    half = finite_difference(forward, FD_STEP_M/2)
    result['half_step_finite_difference'] = half
    result['half_step_check'] = dict(valid=half['valid'], reason=half['reason'])
    if not half['valid']:
        result['finite_difference']['valid'] = False
        result['finite_difference']['reason'] = 'half_step_branch_changed_or_invalid'
        return result
    j, g = np.asarray(fd['range_jacobian_m_per_m']), np.asarray(fd['clearance_gradient_m_per_m'])
    half_j, half_g = np.asarray(half['range_jacobian_m_per_m']), np.asarray(half['clearance_gradient_m_per_m'])
    converged = bool(np.allclose(j, half_j, atol=DERIVATIVE_CONVERGENCE_ABSOLUTE,
                                rtol=DERIVATIVE_CONVERGENCE_RELATIVE)
                     and np.allclose(g, half_g, atol=DERIVATIVE_CONVERGENCE_ABSOLUTE,
                                     rtol=DERIVATIVE_CONVERGENCE_RELATIVE))
    result['half_step_check'].update(passed=converged,
        max_jacobian_difference_m_per_m=float(np.max(abs(j-half_j))),
        max_gradient_difference_m_per_m=float(np.max(abs(g-half_g))))
    if not converged:
        result['finite_difference']['valid'] = False
        result['finite_difference']['reason'] = 'finite_difference_not_converged_at_declared_tolerance'
        return result
    diagnostic = nullspace_diagnostic(j, g)
    result['observability'] = diagnostic
    direction = np.asarray(diagnostic['unit_coefficient_null_direction'])
    nominal, nominal_ranges = fd['nominal'], two_expected_ranges(fd['nominal'])
    for sign in (-1, 1):
        amplitudes = sign*PROBE_COEFFICIENT_NORM_M*direction
        state = forward(amplitudes)
        ranges = two_expected_ranges(state)
        changed = [i+2 for i, (a, b) in enumerate(zip(nominal['observations'], state['observations']))
                   if branch_signature(a) != branch_signature(b)]
        result['finite_null_probes'].append(dict(amplitudes_m=amplitudes.tolist(), coefficient_l2_m=float(np.linalg.norm(amplitudes)),
            state=state, first_hit_branch_changes=changed,
            range_delta_m=(ranges-nominal_ranges).tolist() if ranges is not None else None,
            clearance_delta_m=state['clearance_m']-nominal['clearance_m'] if state['clearance_m'] is not None else None,
            scope='finite illustrative probe, re-hit actual model surfaces; neither physical prior nor guaranteed interval'))
    return result


def load_independent_model(source, calibration):
    """Read a strict whitelist; geometry.npz/tower-motion.npz/data.json are unused."""
    source, calibration = Path(source).resolve(), Path(calibration).resolve()
    manifest = json.loads((source/'manifest.json').read_text())
    if (manifest.get('schema') != 'wfrl.farm-flex-review.v3'
            or manifest.get('status') != 'REVIEW_ONLY'
            or manifest.get('reference_frame') != 'independent zero-load stationary solve'):
        raise ValueError('Requires independently supplied zero-load v3 reference surface')
    names = ('reference-surfaces.npz', 'blade-reference.json', 'deflection-t1.json')
    hashes = {'manifest.json': digest(source/'manifest.json')}
    for name in names:
        actual = digest(source/name)
        if manifest['files'].get(name) != actual:
            raise ValueError('Independent source input hash mismatch: '+name)
        hashes[name] = actual
    scalars = json.loads((source/'deflection-t1.json').read_text())['scalars']
    reference = json.loads((source/'blade-reference.json').read_text())
    if reference.get('reference_state') != 'independent zero-load stationary solve':
        raise ValueError('Unloaded structural tip provenance is required')
    tip = np.asarray(reference['tip_local_m'], float)
    if tip.shape != (3,) or not np.isfinite(tip).all() or tip[2] <= 0:
        raise ValueError('Invalid independent unloaded structural tip')
    with np.load(source/'reference-surfaces.npz', allow_pickle=False) as archive:
        blades = archive['blades'].astype(float).reshape(3, 19, -1, 3)
        triangles = archive['triangles'].copy()
        tower, tower_triangles = archive['tower'].astype(float), archive['tower_triangles'].copy()
    local, residuals = [], []
    for blade in (1, 2, 3):
        points, residual = world_to_rest_local(blades[blade-1], scalars, blade)
        local.append(points); residuals.append(residual)
    local = np.asarray(local)
    # A whole supplied section gets one deformation offset. Neither true ray
    # station nor true source deformation enters these independent shape weights.
    span = local[..., 2].mean(axis=2)
    weights = test_shape_weights(span, tip[2], scalars['HubRad'])[:, :, None, :]
    for mesh, topology in ((blades.reshape(3, -1, 3)[0], triangles), (tower, tower_triangles)):
        if (not np.isfinite(mesh).all() or topology.ndim != 2 or topology.shape[1] != 3
                or not np.issubdtype(topology.dtype, np.integer) or np.any(topology < 0)
                or np.any(topology >= len(mesh))):
            raise ValueError('Invalid independent nominal surface topology')
    config = json.loads(calibration.read_text())
    origins, directions, limits = validate_calibration(config)
    model = IndependentModel(local, weights, triangles, tower, tower_triangles, tip, scalars,
                             origins, directions, limits)
    inputs = dict(source_path=str(source), source_input_hashes=hashes, calibration_path=str(calibration),
                  calibration_sha256=digest(calibration), effective_calibration=config,
                  rest_inverse_round_trip_residual_m=max(residuals),
                  tip_reference=reference,
                  independent_nominal_tower='unchanged independent reference-surfaces tower; no saved tower motion',
                  input_boundary=['manifest metadata', 'reference-surfaces: blades/triangles/tower/tower_triangles',
                                  'deflection-t1: fixed scalars only', 'blade-reference: unloaded tip/provenance',
                                  'fixed calibration'],
                  excluded_inputs=['geometry.npz', 'tower-motion.npz', 'data.json', 'dynamic blade transforms',
                                   'real tip/reference clearance', 'per-frame hit stations', 'TLS saved valid sample masks'])
    return model, inputs


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2)+'\n')


def run(source, calibration, output):
    model, inputs = load_independent_model(source, calibration)
    output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=False)
    declaration = dict(schema='wfrl.dual-beam-observability-declaration.v1', status='FROZEN_BEFORE_CASE_LOOPS',
        cases=analytic_cases(), input=inputs, model_status='ILLUSTRATIVE_ANALYTIC_TEST_MODES_NOT_VALIDATED',
        mode_definition='blade-1 local +x offsets a_k*s^k, k=2,3,4, s=clip((z-HubRad)/(tip_z-HubRad),0,1) at independent nominal sections; amplitudes m; others undeformed',
        attachment_span_m=float(model.scalars['HubRad']), tip_span_m=float(model.tip_local[2]),
        mode_powers=list(POWERS), nominal_amplitudes_m=[0., 0., 0.],
        rigid_pose='analytic rest nacelle/yaw 0; fixed phase and common pitch; inverse rest mapping then current analytic rigid mapping once',
        tower_status='independent unloaded reference tower, static; no moving tower simulation is reproduced',
        ray_model='instantaneous ideal opaque first positive triangle/tower/ground intersection; S2/S3 recomputed for every perturbation',
        finite_difference_step_m=FD_STEP_M, half_step_check_m=FD_STEP_M/2,
        derivative_convergence_absolute_m_per_m=DERIVATIVE_CONVERGENCE_ABSOLUTE,
        derivative_convergence_relative=DERIVATIVE_CONVERGENCE_RELATIVE,
        rank_relative_tolerance=RANK_RELATIVE_TOLERANCE, rank_absolute_tolerance=RANK_ABSOLUTE_TOLERANCE,
        normalization=dict(state_scale_m=STATE_SCALE_M, range_scale_m=RANGE_SCALE_M),
        finite_probe_coefficient_l2_m=PROBE_COEFFICIENT_NORM_M,
        probe_basis='declared illustrative amplitude only; no physical uncertainty or probability bound',
        scope='21 analytic pose cases selected before ray validity; no saved-frame coverage, performance acceptance or physical validation',
        implementation_hashes={name: digest(ROOT/name) for name in (
            'scripts/lidar/audit_dual_beam_observability.py', 'tests/lidar/test_dual_beam_observability.py',
            'scripts/lidar/compare_dual_beam_methods.py', 'wfrl/camera_video/data.py',
            'wfrl/lidar/physics.py', 'wfrl/lidar/moving_tower.py', 'wfrl/lidar/dual_beam.py')})
    write_json(output/'declaration.json', declaration)
    cases = [audit_case(model, case) for case in declaration['cases']]
    valid = [c for c in cases if c['observability'] is not None]
    summary = dict(declared_cases=len(cases), nominal_two_range_cases=sum(c['nominal_range_pair_available'] for c in cases),
        local_derivative_cases=len(valid), local_derivative_unavailable_cases=len(cases)-len(valid),
        rank_counts=dict(Counter(str(c['observability']['rank']) for c in valid)),
        null_clearance_sensitive_cases=sum(c['observability']['maximum_null_clearance_sensitivity_m_per_m'] > 1e-8 for c in valid),
        max_null_clearance_sensitivity_m_per_m=max((c['observability']['maximum_null_clearance_sensitivity_m_per_m'] for c in valid), default=None),
        unknown_clearance_outputs=len(cases),
        unknown_reason='no independently validated structural modes, amplitude bounds, or dynamics; two ranges cannot certify the three TEST coordinates',
        derivative_unavailable_reasons=dict(Counter(c['finite_difference']['reason'] for c in cases if c['observability'] is None)))
    result = dict(schema='wfrl.dual-beam-observability.v1', status='DEVELOPMENT_DIAGNOSTIC_ONLY',
        performance_status='PENDING_ACCEPTANCE', physical_validation='NOT_PERFORMED',
        declaration_sha256=digest(output/'declaration.json'), declaration=declaration, summary=summary, cases=cases,
        runtime=dict(python=sys.version.split()[0], numpy=np.__version__))
    write_json(output/'results.json', result)
    report = ['# 双束柔性可观测性开发诊断', '',
        '状态：DEVELOPMENT_DIAGNOSTIC_ONLY；多项式为说明性 TEST 形状，未做结构或物理验证；默认方法与 TLS 候选均未替换。', '',
        '只读取独立零载参考表面、固定结构参数、零载结构叶尖与标定。参考世界表面先逆转零载根部刚体坐标，再施加已声明的解析方位角/pitch 一次；没有读取本帧真实站位或终端柔性变换。塔筒使用静态零载参考，未复现真实动态工况。', '',
        '三个状态为叶片 1 局部 +x 方向的 a₂ s²、a₃ s³、a₄ s⁴，s=clip((z−HubRad)/(tip_z−HubRad),0,1)，本包 HubRad=1.5m、tip_z=63m、叶根到尖展长61.5m；a 的单位为 m。它们在叶片连接根部位移与斜率为零，叶尖权重为 1。该族只说明面外形状自由度，不代表 NREL 制造商模态、实际振型或完整叶片状态。', '',
        '预声明 21 个方位角/pitch 组合；没有按 TLS 有效样本、真净空或真误差选样，RPM 不参与。S2/S3 每次重新计算全部叶片、塔筒和地面竞争首交。对象、叶片、三角形、近远支路变化会明确导致局部导数不可用。', '',
        f"声明案例 {len(cases)}；名义双量测可用 {summary['nominal_two_range_cases']}；局部导数可用 {len(valid)}；不可用 {summary['local_derivative_unavailable_cases']}；量测零空间具有净空一阶敏感性的案例 {summary['null_clearance_sensitive_cases']}。", '',
        'Jacobian dr/da 和净空梯度 dc/da 均以 m/m 表示。归一化定义 q=a/1m、y=r/1m，SVD 使用相同尺度的欧氏范数；1m 仅为坐标单位，不是设备噪声或允许结构扰动。中心差分 ±0.001m 与半步一致性一起输出，预声明绝对/相对收敛容限均为1e−5；不通过则导数不可用。三形状 DOF、两距离的局部秩最多为 2。', '',
        '零空间方向上的 ±1m 系数 L2 扰动再次射线求交，其距离与净空变化见逐案例 JSON。有限扰动可能产生二阶测距变化、换面或失效；这些探针不是误差预算，也不是具有保证的净空区间。', '',
        '全部 21 例净空认证输出为 UNKNOWN_UNVALIDATED_SHAPE_PRIOR。尚未取得独立验证的模态、幅值范围与动态约束，因此不能以本诊断恢复真实形变或发布保护边界。这是解析案例的未知输出，不能换算为原有 1794 样本的覆盖损失。', '',
        '| 案例 | 两量测 | 局部导数 | 秩 | 最大零空间净空敏感性 m/m |',
        '|---|---|---|---|---|']
    for case in cases:
        o = case['observability']
        report.append(f"| {case['id']} | {case['nominal_range_pair_available']} | {o is not None} | {o['rank'] if o else '-'} | {o['maximum_null_clearance_sensitivity_m_per_m'] if o else '-'} |")
    report += ['', '详细输入 SHA-256、声明、所有成功/失效首交、每列差分、半步比较、SVD 与有限探针均保存于 declaration.json / results.json。']
    (output/'report.md').write_text('\n'.join(report)+'\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, help='v3 independent unloaded source geometry package')
    parser.add_argument('--calibration', required=True)
    parser.add_argument('--output', required=True, help='new output directory; existing directories are rejected')
    args = parser.parse_args()
    result = run(args.source, args.calibration, args.output)
    print(json.dumps(result['summary'], ensure_ascii=False))


if __name__ == '__main__':
    main()
