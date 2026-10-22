"""Publish only completed solver cases with explicit saved validation evidence."""
from pathlib import Path
import sys,json,hashlib,math
import numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from wfrl.lidar.physics import Calibration,background_first_hit
from wfrl.lidar.replay import publish_package,_stats
from wfrl.lidar.evidence import CONTRACT, ASSESSMENT, validate_numerical_evidence, require
from wfrl.lidar.sampling import compare_grids
from scripts.lidar.validate_physics import analytic_evidence

def validate_sources(name, cfg, evidence):
    """Reject incomplete/mismatched evidence before upgrading or writing anything."""
    validate_numerical_evidence(evidence, name)
    require(evidence['analytic'] == analytic_evidence(), 'analytic evidence differs from independent analytic checks')
    names = [name, evidence['spatial']['refined_run'], evidence['temporal']['refined_run']]
    configs, datasets, sources = [], [], []
    for run_name in names:
        run = ROOT/'results/lidar/raw'/run_name
        config = json.loads((run/'run_config.json').read_text())
        require(config.get('name') == run_name, 'run_config name mismatch')
        status = json.loads((run/'exit_status.json').read_text())
        require(status.get('exit_code') == 0 and 'FAST.Farm terminated normally.' in (run/'solver.log').read_text(), 'solver incomplete')
        configs.append(config)
        datasets.append(json.loads((run/'processed.json').read_text()))
        sources.extend(run/p for p in ('run_config.json', 'exit_status.json', 'solver.log', 'processed.json'))
    b, space, time = configs
    # Historical base runs predate source_case; never invent a missing path.
    require(len({c['source_case'] for c in configs if c.get('source_case')}) <= 1, 'source case paths mismatch')
    require(all(c.get(k) == b.get(k) for c in configs for k in ('wind_mps', 'duration_s', 'startup_discard_s', 'controller', 'tower', 'blade_dofs')), 'physical case configuration mismatch')
    require(b.get('span_refined') is False and space.get('span_refined') is True and time.get('span_refined') is True, 'source spatial grids mismatch')
    require(evidence['sampling']['time_range_s'] == [b['startup_discard_s'], b['duration_s']], 'source time interval mismatch')
    for section, left, right, data in (('spatial', b, space, datasets[0]), ('temporal', space, time, datasets[1])):
        e = evidence[section]
        require(e['paired_samples'] == len(data['measurements']), 'paired sample count mismatch')
        for prefix, config in (('baseline', left), ('refined', right)):
            require(e[prefix+'_dt_s'] == config['dt_s'] and e[prefix+'_fps'] == config['fps'], 'source time grid mismatch')
    actual = compare_grids(datasets[1], datasets[2], space['fps'], time['fps'])
    for key in ('time_range_s', 'base', 'refined', 'refined_minus_base', 'passage_comparison', 'verdict'):
        require(evidence['sampling'][key] == actual[key], 'sampling evidence differs from source: '+key)
    require(cfg == b, 'base configuration changed')
    return sources

def upgrade(run):
    draft=json.loads((run/'processed.json').read_text());cal=Calibration()
    for r in draft['measurements']:
        for name,direction in zip(('B1','B2','B3'),cal.directions()):
            b=r['beams'][name];hit=background_first_hit(cal.origin_m,direction)
            if hit and (b['slant_range_m'] is None or hit[0]<b['slant_range_m']):
                b.update(valid=False,slant_range_m=hit[0],hit_point_m=hit[1],estimate_m=None,error_m=None,reason=hit[2])
    lines=(run/'FarmInputs/Case.T1.out').read_text().splitlines();header=next(i for i,l in enumerate(lines) if l.startswith('Time\t'));names=lines[header].split();state=np.loadtxt(lines[header+2:]);phase=np.degrees(np.unwrap(np.radians(state[:,names.index('Azimuth')])));cfg=json.loads((run/'run_config.json').read_text())
    for m in draft['motion']:m['azimuth_deg']=float(phase[round(m['time_s']*cfg['fps'])])
    draft['background_geometry']='opaque plane z=0 and rigid cone radii 3m at z0,1.935m at z87.6; first positive intersection competes with all three blades'
    # Upgrade only in memory: publication never writes into the archived raw run.
    target=run/'processed-v2.json'
    if target.exists() and json.loads(target.read_text())!=draft:
        raise ValueError('existing processed-v2 differs; preserve it and use a new run directory')
    return draft

def publish(name,segment,validation_path,destination=None):
    run=ROOT/'results/lidar/raw'/name;cfg=json.loads((run/'run_config.json').read_text());v=json.loads(Path(validation_path).read_text())
    source_paths=validate_sources(name,cfg,v)
    d=upgrade(run)
    if not d['collision_excluded']:raise ValueError('collision exclusion unresolved')
    raw=[]
    for p in dict.fromkeys(source_paths+[run/'FarmInputs/Case.T1.out',run/'surface_hashes.json',Path(validation_path)]):
        raw.append(dict(path=str(p.resolve()),sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
    manifest=dict(schema_version='1.0',status='READY',run_id=name,turbine_id='T1',source='FAST.Farm',units=dict(time='s',distance='m',angle='deg'),coordinate_system='FAST inertial: x downwind, y lateral, z up; tower center origin. Blade1 azimuth0 up, positive rotation about+x.',model=dict(id='nrel5mw',tower_height_m=87.6,rotor_radius_m=63.,tip_reference=d['tip_reference']),flexibility=dict(blade_enabled=True,tower_assumption='rigid tower; four tower and six platform DOFs disabled',reconstruction_validated=True,method='native OpenFAST AeroDyn Line2 nodes+orientations+airfoil coordinates; no load-derived displacement'),controller=dict(type='prescribed_speed',description=cfg['controller']),calibration=d['calibration'],algorithm=dict(version='molascl-v3.0-section3.5.3-ideal-b2-v1',description='manual simplified estimate, ideal ray range; estimator receives only range and fixed calibration'),postprocess_version='physics-v2-evidence-v2',raw_sources=raw,original_time_range_s=[0,cfg['duration_s']],segment=dict(id=segment,start_s=cfg['startup_discard_s'],end_s=cfg['duration_s'],selection_basis=f'Predefined steady {cfg["wind_mps"]}m/s, prescribed9rpm, discard first {cfg["startup_discard_s"]}s; retain full remaining window; no error-based selection'),validation=dict(evidence_contract=CONTRACT,analytic_verified=True,spatial_comparison_completed=True,temporal_comparison_completed=True,convergence_assessment=ASSESSMENT,numerical_evidence=v,truth_numerical_error_m=v['truth_numerical_error_m'],collision_excluded=True,evidence_path=str(Path(validation_path).resolve()),scope=f'two-level empirical refinement, not rigorous uncertainty bound; collision separating certificates at every saved {cfg["fps"]}Hz state only'),replay=dict(threshold_m=7.,hysteresis_m=.1,max_hold_s=5.,passage_margin=1.25),motion_azimuth='unwrapped_deg')
    manifest['calibration'].update(angle_reference='degrees from -z toward -x; B1 nearest tower, B3 nearest rotor',manual_offset_mapping='Y_lidar in section3.5.3 = positive upstream tower-axis distance (called X_lidar in figure2-5)',range_model='ideal surface first positive ray intersection, no hardware echo/noise',rigid_tower_radius_model='3+(1.935-3)*z/87.6',r_tip_basis='fixed calibration at nominal27.2m tip height; not instantaneous truth')
    rs=d['measurements'];stats=_stats(rs);valid=[r for r in rs if r['beams']['B2']['valid']];errs=np.array([r['beams']['B2']['error_m'] for r in valid]);passes=set(r['passage_id'] for r in rs);seen=set(r['passage_id'] for r in valid)
    if not valid:raise ValueError('no B2 measurements')
    report=f'''# FAST.Farm 理想测距简化算法误差 — {segment}\n\n真实{cfg['duration_s']}秒单机FAST.Farm，固定9rpm、0度变桨、{cfg['wind_mps']}m/s有剪切稳态风。无策略checkpoint；ElastoDyn两阶挥舞与一阶摆振启用，塔筒刚性。剔除0–{cfg['startup_discard_s']}秒启动段，完整保留{cfg['startup_discard_s']}–{cfg['duration_s']}秒。正常/较小净空按预设8/12m/s工况选定，不按误差选择。\n\n预期测量区：叶片方位距正下方不超过3度，固定{cfg['fps']}Hz网格。B2有效{len(valid)}/{len(rs)}={len(valid)/len(rs):.6%}；经过{len(passes)}次，完全漏测{len(passes-seen)}次。MAE={np.abs(errs).mean():.9f}m，最大绝对误差={np.abs(errs).max():.9f}m，P95={stats['p95_abs_error_m']:.9f}m，最大正偏差={stats['max_positive_bias_m']:.9f}m。完整逐束及累计统计见数据包。\n\n叶尖参考点为外端AeroDyn截面周界坐标均值；真值为该点到同高度刚性圆锥塔截面的最短距离，不是整片叶片全局碰撞距离。原生VTP表面来自同一求解时刻的位置、截面姿态和翼型坐标。前端不显示弹性变形。\n\n每个保存时刻三叶片均通过保守三角面/塔筒半空间分离证书，共{d['collision_certificates']}份；这是{cfg['fps']}Hz离散状态检查，不是连续时域碰撞证明。三束首交同时检查三叶片、圆锥塔筒和地面。估计器仅输入斜距与固定标定，不接触真值或实时叶尖。\n\n解析和分开时空细化证据：{Path(validation_path).name}；实测数值差异{v['truth_numerical_error_m']:.9f}m。此为有限两级敏感性检查，不能视为严格误差上界或现场精度。解析与比较证据完整性已检查；未指定精度容差，收敛验收状态为 NOT_ASSESSED_NO_TOLERANCE。B2误差包括手册直叶片假设、命中截面与叶尖差异、标定和几何离散，不能全部归因于弯曲。\n\n资料核验：已逐图核对用户提供的 V3.0 原件（SHA256 1a126ba6420f47136b17986064908215d6b0d7e0d574ff56247c1255b07a0363）；图2-5与图3-26的X/Y命名不同，按物理主轴偏距映射。此核验不表示硬件精度或完整厂家算法验收。资料：MolasCL手册V3.0第3.5.3节（印刷第33页/PDF第34页、图3-26）及图2-5（印刷第8页/PDF第9页）；[OpenFAST表面生成说明](https://openfast.readthedocs.io/en/dev/_downloads/5f2ddf006568adc9b88d8118dc3f1732/FAST8_README.pdf)。\n'''
    report+='\n演示阈值7.0m在两预设工况真值区间计算后选定，滞回0.1m，仅用于演示，不用于保护或安全判断。\n\n逐束统计（P95采用nearest rank，与包内统计一致）：\n\n|光束|有效/预期|MAE(m)|MaxAbs(m)|P95(m)|最大正偏差(m)|完全漏测经过|\n|---|---|---|---|---|---|---|\n'
    for beam in ['B1','B2','B3']:
        per=_stats([{**r,'beams':{'B2':r['beams'][beam]}} for r in rs])
        def fmt(x):return '--' if x is None else f'{x:.9f}'
        report+=f"|{beam}|{per['valid_samples']}/{per['expected_samples']}|{fmt(per['mae_m'])}|{fmt(per['max_abs_error_m'])}|{fmt(per['p95_abs_error_m'])}|{fmt(per['max_positive_bias_m'])}|{per['missed_passage_count']}|\n"
    report+='\n完整验证实数（同包可追溯）：\n```json\n'+json.dumps(v,ensure_ascii=False,indent=2)+'\n```\n'
    return publish_package(Path(destination) if destination else ROOT/'results/lidar/packages'/segment,manifest,d['motion'],rs,report)
if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('name');p.add_argument('segment',choices=['normal','close']);p.add_argument('validation_path');p.add_argument('--destination',type=Path);a=p.parse_args();print(publish(a.name,a.segment,a.validation_path,a.destination))
