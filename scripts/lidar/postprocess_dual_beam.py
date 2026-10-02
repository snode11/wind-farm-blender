"""Full saved-grid S1 alarm and S2/S3 reconstruction; never starts FAST.Farm."""
from __future__ import annotations
import argparse
from collections import Counter
import json
import math
import os
import platform
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from wfrl.camera_video.data import SourceGeometry, blade_root_frame
from wfrl.lidar.dual_beam import METHOD_VERSIONS, validate_calibration, validate_inner_origins, observe, reconstruct, s1_state
from wfrl.lidar.dual_beam_replay import SCHEMA, digest, precompute, error_statistics
from wfrl.lidar.moving_tower import horizontal_clearance, tip_surface_clearance


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))+'\n')


def installation(output, config):
    origins, _, _ = validate_calibration(config)
    c = config['installation']
    height, radius = c['nominal_tip_height_m'], c['nominal_tower_radius_m']
    s = -origins[0, 0]
    dz = origins[0, 2]-height
    projected_x = dz*math.tan(math.radians(config['angles_deg'][0]))+s
    lateral = float(origins[0, 1])
    actual = math.hypot(projected_x, lateral)-radius
    target_radius = c['target_gap_m']+radius
    required = (math.sqrt(target_radius**2-lateral**2)-dz*math.tan(math.radians(config['angles_deg'][0]))
                if abs(lateral) <= target_radius else None)
    selected = config['status'] == 'SIMULATION_SELECTED'
    audit = dict(status=config['status'], nominal_gap_m=actual, target_gap_m=c['target_gap_m'],
                 current_offset_m=s, candidate_offset_for_target_m=required,
                 lateral_offset_m=lateral, forward_projection_at_tip_m=projected_x,
                 simulation_mount_selected=selected, candidate_approved=False,
                 tip_height_m=height, radius_m=radius,
                 scope='nominal vertical tower radial wall gap at rest, including lateral offset; physical installation not certified')
    write_json(output/'installation-audit.json', audit)
    # Same metric scale on both axes. Distances/angles are annotated rather than enlarged.
    scale, x0, y0 = 6., 210., 650.
    def pt(s, z): return (x0+scale*s, y0-scale*z)
    def line(a, b, color, width=2):
        return f'<line x1="{a[0]}" y1="{a[1]}" x2="{b[0]}" y2="{b[1]}" stroke="{color}" stroke-width="{width}"/>'
    lines = [line(pt(3, 0), pt(1.935, 87.6), '#667085', 4), line(pt(0, 0), pt(0, 90), '#aab1be')]
    for i, angle in enumerate(config['angles_deg']):
        lo = -origins[i, 0]
        lines.append(line(pt(lo, origins[i,2]), pt(lo+dz*math.tan(math.radians(angle)), height), ('#d42c44','#247da5','#9362c6')[i]))
    lines.append(line(pt(radius, height), pt(projected_x, height), '#d42c44', 3))
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="920" height="720" viewBox="0 0 920 720">
<rect width="920" height="720" fill="#fafbfc"/><g font-family="sans-serif" fill="#17212f">
<text x="36" y="40" font-size="23">Dual-beam installation — metres</text>
<text x="36" y="72" font-size="16">S1/S2/S3 = {'/'.join(f'{a:g}°' for a in config['angles_deg'])}; common emitter approximation; rest pose</text>
{''.join(lines)}
<text x="330" y="125">Emitter z = {origins[0,2]:.1f} m; forward = {s:.2f} m; lateral = {lateral:.2f} m</text>
<text x="330" y="450">Nominal tip z = {height:.1f} m; tower radius = {radius:.2f} m</text>
<text x="330" y="485">S1 radial wall gap = {actual:.3f} m (includes lateral offset)</text>
<text x="330" y="525">Reference target = {c['target_gap_m']:.2f} m; no forced numerical offset</text>
<text x="330" y="560">{'Simulation mount selected; see saved-pose audit.' if selected else 'Old mount retained only for diagnosis.'}</text>
<text x="36" y="690">Equal-scale side projection; lateral offset annotated; physical installation not certified.</text>
</g></svg>'''
    (output/'installation.svg').write_text(svg)
    return audit


def passage_summary(rows, dt):
    groups = {}
    for row in rows:
        if row['passage_id'] is not None:
            groups.setdefault(row['passage_id'], []).append(row)
    result = []
    for key, subset in groups.items():
        valid = [r['reconstruction']['valid'] and r['reconstruction']['blade_id']==r['expected_blade_id'] for r in subset]
        runs, current = [], []
        missing, max_missing = 0, 0
        for row, ok in zip(subset, valid):
            if ok:
                current.append(row['time_s']); missing = 0
            else:
                missing += 1; max_missing = max(max_missing, missing)
                if current: runs.append(current); current=[]
        if current: runs.append(current)
        errors = [r['evaluation']['clearance_error_m'] for r,ok in zip(subset,valid)
                  if ok and r['evaluation']['clearance_error_m'] is not None]
        result.append(dict(passage_id=key, blade_id=subset[0]['expected_blade_id'],
                           start_s=subset[0]['time_s'], end_s=subset[-1]['time_s'],
                           boundary_truncated=subset[0] is rows[0] or subset[-1] is rows[-1],
                           expected_samples=len(subset), valid_samples=sum(valid), missed=not any(valid),
                           max_missing_samples=max_missing, max_missing_grid_duration_s=max_missing*dt,
                           max_consecutive_valid_samples=max(map(len,runs),default=0),
                           common_hit_runs=[dict(first_s=r[0],last_s=r[-1],samples=len(r)) for r in runs],
                           **error_statistics(errors)))
    return result


def process(source, config):
    origins, directions, limits = validate_calibration(config)
    method = config.get('reconstruction_method', 'hub-axis.v1')
    if method not in METHOD_VERSIONS:
        raise ValueError('Unsupported dual-beam reconstruction method')
    reference = json.loads((source.path/'blade-reference.json').read_text())
    tip_local = np.asarray(reference['tip_local_m'],float)
    if not np.isclose(config['effective_length_m'], np.linalg.norm(tip_local), atol=1e-9, rtol=0):
        raise ValueError('Configured length differs from fixed unloaded hub-to-tip reference')
    rest_nacelle = np.column_stack((np.eye(3), np.zeros(3)))
    if config['status'] == 'SIMULATION_SELECTED':
        if config.get('installation', {}).get('region') != 'INNER_NACELLE':
            raise ValueError('Selected installation must declare INNER_NACELLE')
        shell = json.loads((ROOT/'blender_frontend/wfrl_blender/assets/nrel5mw_geometry.json').read_text())['shell']
        center_x = shell['nacelle_x_bias']*source.scalars['OverHang']
        rest_hub, _ = blade_root_frame(source.scalars, [0]*6, 1, rest_nacelle)
        validate_inner_origins(origins, rest_hub, source.scalars['ShftTilt'],
                               [center_x-shell['length']/2, center_x+shell['length']/2], shell['width']/2)
    rest_tips=[]
    for b in (1,2,3):
        hub, axes = blade_root_frame(source.scalars, [0]*6, b, rest_nacelle)
        rest_tips.append(hub + axes@tip_local)
    rows=[]
    for index, time in enumerate(source.times):
        nacelle = source.nacelles[index]
        tr = source.transforms[index]
        blades = np.einsum('bsij,bsvj->bsvi',tr[...,:3],source.blade_reference)+tr[:,:,None,:,3]
        blades = blades.reshape(3,-1,3)+source.layout
        tt = source.tower_transforms[index,source.tower_station]
        tower = np.einsum('nij,nj->ni',tt[...,:3],source.tower_reference)+tt[...,3]+source.layout
        obs={}
        for i in range(3):
            origin=nacelle[:,:3]@origins[i]+nacelle[:,3]+source.layout
            direction=nacelle[:,:3]@directions[i]
            # Source support rotation is checked by SourceGeometry. Normalize
            # roundoff only; do not rotate rays toward reference blade points.
            direction=direction/np.linalg.norm(direction)
            obs[f'S{i+1}']=observe(float(time),origin,direction,blades,source.blade_triangles,
                                  tower,source.tower_triangles,limits)
        hub,_=blade_root_frame(source.scalars,source.poses[index],1,nacelle)
        pair=reconstruct(obs['S2'],obs['S3'],hub+source.layout,config['effective_length_m'],
                         lambda point: horizontal_clearance(point,tower,source.tower_triangles)[0],
                         time_s=float(time),limits=limits,method=method)
        evaluation=dict(tip_reference_m=None,clearance_reference_m=None,position_error_m=None,
                        position_error_norm_m=None,clearance_error_m=None,surface_clearance_m=None,
                        relative_surface_deviation_m=None,reason='no_paired_blade')
        # Evaluation is a separate branch. The estimator above cannot access this tip.
        b=pair['blade_id']
        if b is not None:
            transform=tr[b-1,-1]
            tip=transform[:,:3]@rest_tips[b-1]+transform[:,3]+source.layout
            evaluation['tip_reference_m']=tip.tolist()
            try:
                ref=horizontal_clearance(tip,tower,source.tower_triangles)[0]
                evaluation.update(clearance_reference_m=ref,reason='valid')
                if pair['valid']:
                    error=np.asarray(pair['tip_estimate_m'])-tip
                    surface=tip_surface_clearance(blades[b-1],tower,source.tower_triangles)[0]
                    evaluation.update(position_error_m=error.tolist(),position_error_norm_m=float(np.linalg.norm(error)),
                                      clearance_error_m=pair['clearance_estimate']-ref,surface_clearance_m=surface,
                                      relative_surface_deviation_m=pair['clearance_estimate']-surface)
            except ValueError:
                evaluation['reason']='no_tower_section_at_reference_height'
        phase=source.poses[index,1]+np.arange(3)*120
        cycle=np.floor((phase-180+180)/360).astype(int)
        delta=(phase-180+180)%360-180
        expected=np.flatnonzero(abs(delta)<=config['expected_window_half_angle_deg'])
        b=int(expected[0])+1 if len(expected) else None
        rows.append(dict(time_s=float(time),sample_kind='source',observations=obs,reconstruction=pair,
                         evaluation=evaluation,s1_observation_state=s1_state(obs['S1']),expected_blade_id=b,
                         passage_id=f'blade{b}-cycle{cycle[b-1]}' if b is not None else None))
        if index%400==0: print(source.turbine_id,index+1,'/',len(source.times),flush=True)
    cumulative,events=precompute(rows,config)
    passages=passage_summary(rows,1/source.manifest['source_fps'])
    summary=dict(**cumulative[-1]['statistics'],alarm_events=len(events),
                 raw_alarm_hits=sum(r['s1_observation_state']=='triggered' for r in rows),
                 unknown_alarm_samples=sum(r['s1_observation_state']=='unknown' for r in rows),
                 pair_reasons=dict(Counter(r['reconstruction']['reason'] for r in rows)),
                 first_objects={s:dict(Counter(r['observations'][s]['first_object'] or 'none' for r in rows)) for s in obs},
                 valid_outside_expected_window=sum(r['reconstruction']['valid'] and r['passage_id'] is None for r in rows),
                 passages=passages,
                 by_blade={str(b):dict(valid_samples=sum(r['reconstruction']['valid'] and r['reconstruction']['blade_id']==b for r in rows),
                    **error_statistics([r['evaluation']['clearance_error_m'] for r in rows if r['reconstruction']['blade_id']==b and r['evaluation']['clearance_error_m'] is not None])) for b in (1,2,3)})
    positions=[r['evaluation']['position_error_m'] for r in rows if r['evaluation']['position_error_m'] is not None]
    summary['position_error']=(dict(mean_xyz_m=np.mean(positions,axis=0).tolist(),
                               max_abs_xyz_m=np.max(np.abs(positions),axis=0).tolist(),
                               max_norm_m=float(np.max(np.linalg.norm(positions,axis=1)))) if positions else None)
    summary['sampling_conclusion']='saved-grid baseline only; isolated hits or unresolved event edges require real output refinement before stability/latency acceptance'
    return dict(samples=rows,cumulative=cumulative,events=events,summary=summary)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=ROOT/'blender_frontend/wfrl_blender/assets/mappo')
    parser.add_argument('--calibration',type=Path,default=ROOT/'configs/lidar/dual-beam-diagnostic.json')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--method', choices=tuple(METHOD_VERSIONS),
                        help='Override reconstruction method; absent retains calibration or legacy default')
    args=parser.parse_args()
    config=json.loads(args.calibration.read_text())
    method=args.method or config.get('reconstruction_method', 'hub-axis.v1')
    if method not in METHOD_VERSIONS:
        raise ValueError('Unsupported dual-beam reconstruction method')
    if args.method is None and config.get('algorithm_version', METHOD_VERSIONS[method]) != METHOD_VERSIONS[method]:
        raise ValueError('Calibration algorithm version differs from reconstruction method')
    config.update(reconstruction_method=method, algorithm_version=METHOD_VERSIONS[method])
    validate_calibration(config)
    output=args.output.resolve()
    output.mkdir(parents=True,exist_ok=False)
    write_json(output/'calibration.json',config)
    audit=installation(output,config)
    results={}
    source_manifest=json.loads((args.source/'manifest.json').read_text())
    for tid in source_manifest['turbine_ids']:
        source=SourceGeometry(args.source,tid)
        results[tid]=process(source,config)
        write_json(output/f'{tid}-summary.json',results[tid]['summary'])
    write_json(output/'results.json',results)
    import scipy
    from scipy.spatial import _qhull
    manifest=dict(schema=SCHEMA,status='REVIEW_ONLY',algorithm_version=METHOD_VERSIONS[method],
                  reconstruction_method=method,
                  performance_status='PENDING_ACCEPTANCE',sample_kind='source',source_fps=source.manifest['source_fps'],
                  source_package=os.path.relpath(source.path,output),source_hashes=source.source_hashes,
                  segment=source.manifest['segment'],turbine_ids=source.manifest['turbine_ids'],
                  reference_definition='independent unloaded structural tip transported by saved terminal section transform',
                  clearance_definition='signed horizontal distance to known same-global-height moving tower section',
                  surface_definition='separate terminal contour surface minimum; not whole-blade clearance',
                  calibration_status=config['status'],
                  calibration_sha256=digest(output/'calibration.json'),
                  runtime=dict(python=platform.python_version(),numpy=np.__version__,scipy=scipy.__version__,
                      numpy_core_sha256=digest(np.core._multiarray_umath.__file__),
                      scipy_qhull_sha256=digest(_qhull.__file__)),
                  implementation_hashes={name:digest(ROOT/name) for name in
                      ('wfrl/lidar/dual_beam.py','wfrl/lidar/dual_beam_replay.py','scripts/lidar/postprocess_dual_beam.py',
                       'wfrl/lidar/physics.py','wfrl/lidar/moving_tower.py','wfrl/camera_video/data.py',
                       'wfrl/lidar/replay.py','wfrl/lidar/evidence.py','wfrl/__init__.py','wfrl/paths.py',
                       'blender_frontend/wfrl_blender/assets/nrel5mw_geometry.json')},
                  files={p.name:digest(p) for p in output.iterdir() if p.is_file()})
    write_json(output/'manifest.json',manifest)
    lines=[f"# 双束与 S1 独立报警：{source.manifest['source_fps']:g} Hz 保存网格结果",'',
           f'方法：`{method}`；算法版本：`{METHOD_VERSIONS[method]}`；性能状态：`PENDING_ACCEPTANCE`。', '',
           f"安装状态：`{config['status']}`。S1 名义径向塔壁间距 {audit['nominal_gap_m']:.3f} m（包含侧向偏距）；4.5 m 为参考目标，不强制平移读数。",'',
           f"结果来自源包 {source.manifest['segment']['start_s']:g}–{source.manifest['segment']['end_s']:g} s 全部保存时刻，未重跑 FAST.Farm。采用仿真叶片编号、机舱姿态和已知变形塔筒。",
           '固定有效长度由预弯参考 [-1,0,63] 的模确定，包含轮毂到叶根距离；不按当前帧调整。',
           '报警和读数分别保持 1 仿真秒；未知通道不删除报警历史。无双束测量不表示安全。','',
           '|机组|双束有效/全网格|预期区有效/样本|经过/整次漏测|MAE (m)|S1 事件/命中样本|',
           '|---|---:|---:|---:|---:|---:|']
    for tid,data in results.items():
        s=data['summary'];mae='不适用' if s['mae_m'] is None else f"{s['mae_m']:.4f}"
        lines.append(f"|{tid}|{s['all_valid_samples']}/{len(data['samples'])}|{s['valid_samples']}/{s['expected_samples']}|{s['passage_count']}/{s['missed_passage_count']}|{mae}|{s['alarm_events']}/{s['raw_alarm_hits']}|")
    lines += ['', '完整逐束斜距、首交类别、世界坐标、重建和独立评估见 results.json；各叶片、各次经过及 nearest-rank P95 见各机组 summary。',
              '预期区预先固定为各叶片朝下 ±15°，报警和射线扫描覆盖全部时刻；报告同时保留区外命中。',
              '整次漏测只统计完整经过；片段起止截断的测量区另标 boundary_truncated，不计为整次漏测。',
              f"相邻保存样本不证明中间持续命中。事件边界时间不确定性至少受 {1000/source.manifest['source_fps']:g} ms 保存间隔约束，原始窄事件仍可能完全漏存。",
              '精度、覆盖率和端到端延迟门槛未约定，结论为待性能验收。没有报警的片段仅表示本片段未保存到 S1 有效命中。',
              ('当前安装位已按仿真源几何选定，实体安装仍待现场核验；不能用插值替代采样验收。'
               if config['status']=='SIMULATION_SELECTED' else
               '当前仅诊断标定；先确认安装布局再决定是否加密真实几何输出，不能用插值替代采样验收。')]
    (output/'report.md').write_text('\n'.join(lines)+'\n')
    print(output,flush=True)


if __name__=='__main__':
    main()
