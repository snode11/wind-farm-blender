"""Native source contracts for area samples, installation import and blade inference."""
import json
import os
from pathlib import Path
import sys
from dataclasses import replace
import numpy as np
import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
import wfrl_blender as addon
from wfrl_blender import farm_flex
from wfrl_blender.radar_x100 import runtime
from wfrl.radar_x100 import installation

out = Path(os.environ['WFRL_TEST_OUTPUT'])
out.mkdir(parents=True, exist_ok=False)
addon.register()
try:
    addon.load_demo_scene(farm_flex.default_package())
    scene = bpy.context.scene
    session = runtime.start(scene)
    assert session.frame.provenance['material_sampling'] == 'AREA'
    assert session.blade_result is not None, session.blade_error
    before = session.frame.points.tobytes()
    original_result = json.dumps(session.blade_result, sort_keys=True)
    original = session.frame
    session.frame = replace(original, material_ids=tuple('irrelevant' for _ in original.points),
        provenance=original.provenance | dict(truth='not available to estimator'))
    session.blade_key = None
    session.blade_tracker.reset()
    session.derive_blades()
    assert json.dumps(session.blade_result, sort_keys=True) == original_result
    assert session.frame.points.tobytes() == before
    session.frame = original
    windows = [[dict(status='COMPLETE', indices=[0,1], start_s=0,end_s=1/30)] for _ in range(3)]
    detection_windows = [[dict(status='COMPLETE', indices=[2*b, 2*b+1], start_s=2*b/30,
        end_s=(2*b+1)/30, nadir_time_s=(2*b+.5)/30)] for b in range(3)]
    synthetic = dict(target_associated_points=8, tower_distance_error_m=.02)
    detection_support = installation.summarize_passage_detections(np.arange(6)/30,
        [synthetic]*6, detection_windows, [[synthetic]]*3)
    # Synthetic evidence exercises the import contract, not physical installation performance.
    choice = dict(id='candidate', location_m=[-3.5,-1.7,-2], rotation_deg=[0,75,180],
        mechanical=dict(status='PASS_RESEARCH', motion_clearance=installation.summarize_motion([
            dict(clearance_m=.2, inside=False), dict(clearance_m=.2, inside=False)])),
        detection_profiles=[dict(simulated_detection_support=dict(status='KNOWN',
            expected_frames=2, known_frames=2, unknown_frames=0, supported_frames=2, support_fraction=1.),
            passage_detection_support=detection_support)],
        profiles=[dict(seed=100, budget=512, geometric_coverage=installation.summarize(np.full((2,3),8), windows), occluded_fraction=0.)])
    report = dict(schema=installation.SCHEMA, source_id=session.sampler.source_id,
        geometry_id=session.sampler.geometry_id, sampling_method='AREA', sampling_algorithm=installation.AREA_ALGORITHM,
        winner_id='candidate', candidates=[choice])
    stamp = scene.get('wfrl_clearance_time_s')
    runtime.apply_mount_preset(scene, 'SEARCH', search_report=report)
    assert scene.get('wfrl_clearance_time_s') == stamp
    assert tuple(scene.wfrl_x100_rotation) == (0.,75.,180.)
    assert session.blade_result is not None
    identity = session.frame.identity
    installed_frame, installed_points = session.frame, session.frame.points.tobytes()
    installed = (scene.wfrl_x100_mount, tuple(scene.wfrl_x100_location), tuple(scene.wfrl_x100_rotation),
                 session.sampler, session.mount, session.mount.parent)
    mount_basis = np.array(session.mount.matrix_basis)
    parent_inverse = np.array(session.mount.matrix_parent_inverse)
    scene_frame = scene.frame_current, scene.frame_subframe
    legacy_choice = {key: value for key, value in choice.items() if key not in {'mechanical', 'detection_profiles'}}
    legacy_choice['profiles'] = [dict(seed=100, budget=512,
        coverage=choice['profiles'][0]['geometric_coverage'], occluded_fraction=0.)]
    for rejected, message in (
            (report | dict(source_id='other'), '来源'),
            (report | dict(errors=[dict(source_time_s=-1., reason='outside passage')]), '未知帧'),
            (report | dict(schema='x100-installation-search.v1', candidates=[legacy_choice]), '版本不受支持'),
            (report | dict(candidates=[legacy_choice]), '缺少机械约束证据'),
            (report | dict(candidates=[{k: v for k, v in choice.items() if k != 'mechanical'}]), '缺少机械约束证据'),
            (report | dict(candidates=[choice | dict(mechanical=dict(status='PASS_RESEARCH'))]), '缺少运动净空证据'),
            (report | dict(candidates=[{k: v for k, v in choice.items() if k != 'detection_profiles'}]), '缺少检测证据')):
        try:
            runtime.apply_mount_preset(scene, 'SEARCH', search_report=rejected)
            raise AssertionError('Invalid search result accepted')
        except ValueError as exc:
            assert message in str(exc), str(exc)
            if message not in {'来源', '未知帧'}:
                assert '请重新运行 scripts/radar_x100/optimize_mount.py 生成 v2 报告' in str(exc)
        assert runtime.session_for(scene) is session
        assert installed == (scene.wfrl_x100_mount, tuple(scene.wfrl_x100_location), tuple(scene.wfrl_x100_rotation),
                             session.sampler, session.mount, session.mount.parent)
        assert np.array_equal(np.array(session.mount.matrix_basis), mount_basis)
        assert np.array_equal(np.array(session.mount.matrix_parent_inverse), parent_inverse)
        assert session.frame is installed_frame and session.frame.points.tobytes() == installed_points
        assert session.frame.identity == identity
        assert scene.get('wfrl_clearance_time_s') == stamp
        assert (scene.frame_current, scene.frame_subframe) == scene_frame
    for offset in (1/30,2/30,.3,.1):
        scene['wfrl_clearance_time_s'] = session.sampler.interval[0] + offset
        session.update()
        assert session.blade_result is not None, session.blade_error
        assert session.frame.time.source_time_s == session.context.identity[5]
    scene.wfrl_x100_blade_tracking = False
    session.update()
    assert session.blade_result is None
    scene.wfrl_x100_blade_tracking = True
    session.update()
    assert session.blade_result is not None
    session._display(None)
    assert session.blade_result is None and not session.blade_tracker.tracks
    result = dict(status='PASS', addon=addon.__file__, checks=['area_sampler', 'truth_isolation',
        'source_bound_installation', 'clock_preserved', 'unknown_search_rejected', 'rejected_import_preserves_frame',
        'legacy_search_rejected', 'missing_evidence_rejected', 'rejected_import_preserves_installation',
        'source_time_pairing', 'rewind', 'toggle', 'source_invalid_clears_tracking'])
    (out / 'result.json').write_text(json.dumps(result, indent=2))
    print('X100_OBSERVATIONS_PASS', str(out))
finally:
    addon.unregister()
