"""Denominators, temporal input isolation and retained fine-grid extremes."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts.lidar import compare_dual_beam_time_refinement as temporal
from wfrl.lidar.dual_beam_replay import digest


def record(time, estimate=8., reference=7., valid=True):
    method=dict(fresh_valid=valid,reason=None if valid else 'miss',
                estimate_clearance_m=estimate if valid else None,error_m=estimate-reference if valid else None,
                reconstruction_valid=valid,blade_id=1 if valid else None,reconstruction_reason='valid' if valid else 'miss')
    return dict(turbine_id='T1',time_s=time,expected_blade_id=1,passage_id='blade1-cycle1',
                boundary_truncated=False,reference_clearance_m=reference,reference_reason='valid',
                baseline=deepcopy(method),candidate=deepcopy(method),
                s1=dict(observation=dict(valid=valid,first_object='blade' if valid else 'none',
                                         slant_range_m=10. if valid else None),
                        observation_state='triggered' if valid else 'not_triggered',alarm={'active':valid}))


def grids():
    coarse={('T1',t):record(t) for t in (18.,19.,20.)}
    fine={('T1',t):record(t) for t in (18.,18.5,19.,19.5,20.)}
    return coarse,fine


def test_added_fine_time_extreme_retained_in_own_metrics():
    coarse,fine=grids();fine[('T1',18.5)]=record(18.5,estimate=20.,reference=2.)
    report=temporal.compare_grids(coarse,fine,1.,2.,[18.,20.])
    assert report['full_grid_summary']['fine']['methods']['candidate']['statistics']['max_abs_error_m']==18.
    assert report['fine_additional_summary']['methods']['candidate']['statistics']['max_abs_error_m']==18.
    assert [r['time_s'] for r in report['fine_additional_samples']]==[18.5,19.5]
    assert report['fine_additional_samples'][0]['reference_clearance_m']==2.
    assert len(report['matched_coarse_samples'])==3


def test_coarse_valid_fine_invalid_remains_and_counts_unknown():
    coarse,fine=grids();fine[('T1',19.)]=record(19.,valid=False)
    report=temporal.compare_grids(coarse,fine,1.,2.,[18.,20.])
    row=next(r for r in report['matched_coarse_samples'] if r['time_s']==19.)
    assert row['methods']['candidate']['validity_transition']=='True->False'
    assert row['methods']['candidate']['estimate_difference_m'] is None
    assert row['fine']['candidate']['reason']=='miss'
    assert row['s1']['state_changed']
    cov=report['full_grid_summary']['fine']['methods']['candidate']['coverage']
    assert cov['fresh_valid_samples']==4 and cov['expected_samples']==5
    assert cov['unknown_reasons']=={'miss':1}


def test_missing_matched_time_rejected():
    coarse,fine=grids();del fine[('T1',19.)]
    with pytest.raises(ValueError,match='Missing matched'):
        temporal.compare_grids(coarse,fine,1.,2.,[18.,20.])


def make_runs(tmp_path):
    runs=[]
    template=tmp_path/'template';template.mkdir();(template/'probe-config.json').write_text('{}')
    reference=tmp_path/'reference';reference.mkdir();(reference/'probe-config.json').write_text('{"reference":true}')
    solver=tmp_path/'solver';solver.write_text('fixture solver')
    for name,fps,dt in [('coarse',40,.00625),('fine',80,.003125)]:
        run=tmp_path/name;run.mkdir();(run/'FarmInputs').mkdir()
        (run/'FarmInputs/case.fst').write_text(f'{dt} DT - solver\n{1/fps} DT_Out - saved\n{fps} VTK_fps - mesh\n2 CompElast - BeamDyn\n')
        (run/'FarmInputs/wind.dat').write_text('12 HWindSpeed\n')
        config=dict(schema='wfrl.dual-beam-validation-run.v1',status='SOLVER_COMPLETE',fps=fps,dt_s=dt,
            duration_s=26.,wind_mps=12.,rpm=9.,pitch_deg=0.,azimuth_deg=0.,evaluation_window_s=[18.,26.],
            executable=str(solver),solver_sha256=digest(solver),template_path=str(template),reference_path=str(reference),
            template_config_sha256=digest(template/'probe-config.json'),reference_config_sha256=digest(reference/'probe-config.json'),
            template_input_hashes={'FarmInputs/wind.dat':'d'*64},
            inputs=[{'path':str(p.relative_to(run)),'sha256':digest(p),'bytes':p.stat().st_size}
                    for p in sorted((run/'FarmInputs').iterdir())])
        (run/'run-config.json').write_text(json.dumps(config))
        (run/'exit-status.json').write_text(json.dumps({'status':'COMPLETE','exit_code':0}))
        runs.append(run)
    return runs


def test_only_declared_fst_temporal_fields_allowed(tmp_path):
    coarse,fine=make_runs(tmp_path)
    configs,integrity=temporal.verify_run_inputs(coarse,fine)
    assert integrity['allowed_fst_fields']==['DT','DT_Out','VTK_fps']
    assert len(integrity['input_comparisons'])==2
    assert configs[0]['fps']==40 and configs[1]['fps']==80


@pytest.mark.parametrize('kind',['wind_metadata','wind_file','structural_fst','reference','missing_input','stale_hash'])
def test_different_inputs_or_physics_rejected(tmp_path,kind):
    coarse,fine=make_runs(tmp_path);path=fine/'run-config.json';config=json.loads(path.read_text())
    if kind=='wind_metadata':config['wind_mps']=13.
    elif kind=='reference':config['reference_config_sha256']='e'*64
    elif kind=='missing_input':config['inputs']=config['inputs'][:-1]
    else:
        file=fine/('FarmInputs/case.fst' if kind=='structural_fst' else 'FarmInputs/wind.dat')
        file.write_text(file.read_text().replace('2 CompElast','1 CompElast') if kind=='structural_fst' else '13 HWindSpeed\n')
        if kind!='stale_hash':
            for entry in config['inputs']:
                if entry['path']==str(file.relative_to(fine)):entry['sha256']=digest(file)
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError):temporal.verify_run_inputs(coarse,fine)


def test_fst_other_text_never_normalized():
    a='0.00625 DT - solver\n0.025 DT_Out - saved\n40 VTK_fps - mesh\n2 CompElast\n'
    b='0.003125    DT - solver\n0.0125 DT_Out - saved\n80 VTK_fps - mesh\n2 CompElast\n'
    assert temporal.normalize_fst(a)==temporal.normalize_fst(b)
    assert temporal.normalize_fst(a)!=temporal.normalize_fst(b.replace('2 CompElast','1 CompElast'))


def test_report_has_no_acceptance_pass_marker():
    assert temporal.STATUS=='TEMPORAL_SENSITIVITY_TWO_LEVELS'


def test_passage_fine_only_extremes_and_unknown_are_preserved():
    def minimum(reference,prediction,unknown=False):
        m=dict(valid_samples=1,unknown=unknown,predicted_min_m=prediction,predicted_min_time_s=18.5,
               error_m=prediction-reference if prediction is not None else None)
        return dict(turbine_id='T1',passage_id='p1',reference_min_m=reference,reference_min_time_s=18.5,
                    baseline=deepcopy(m),candidate=deepcopy(m))
    a,b=minimum(7.,8.),minimum(2.,None,True)
    extra=minimum(1.,20.);extra['passage_id']='fine-only'
    result=temporal.compare_passage_minima([a],[b,extra])
    matched=next(r for r in result if r['presence']=='both')
    assert matched['reference_min_difference_m']==-5.
    assert matched['methods']['candidate']['unknown_changed']
    assert matched['methods']['candidate']['predicted_min_difference_m'] is None
    assert next(r for r in result if r['presence']=='fine_only')['fine']['reference_min_m']==1.


def test_moved_package_uses_relative_runner_link_and_current_inputs(tmp_path):
    import shutil
    original=tmp_path/'original';original.mkdir()
    coarse,fine=make_runs(original)
    for run in (coarse,fine):
        config=json.loads((run/'run-config.json').read_text())
        config.update(executable='/old-machine/FAST.Farm',template_path='/old-machine/template',
                      reference_path='/old-machine/reference')
        (run/'run-config.json').write_text(json.dumps(config))
    (original/'source').mkdir()
    manifest=dict(run_path='/old-machine/raw/coarse',run_package='../coarse',
                  run_config_sha256=digest(coarse/'run-config.json'))
    moved=tmp_path/'moved';shutil.copytree(original,moved);shutil.rmtree(original)
    configs,integrity=temporal.verify_run_inputs(moved/'coarse',moved/'fine')
    link=temporal.verify_source_link(manifest,moved/'source',moved/'coarse',integrity['run_config_hashes']['coarse'])
    assert link['historical_run_path']=='/old-machine/raw/coarse'
    assert configs[0]['wind_mps']==12.
    with pytest.raises(ValueError,match='pointer differs'):
        temporal.verify_source_link({**manifest,'run_package':'../fine'},moved/'source',moved/'coarse',manifest['run_config_sha256'])
    with pytest.raises(ValueError,match='not linked'):
        temporal.verify_source_link(manifest,moved/'source',moved/'coarse','f'*64)
