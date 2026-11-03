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
    choice = dict(id='candidate', location_m=[-3.5,-1.7,-2], rotation_deg=[0,75,180],
        profiles=[dict(seed=100, budget=512, coverage=installation.summarize(np.full((2,3),8), windows), occluded_fraction=0.)])
    report = dict(schema=installation.SCHEMA, source_id=session.sampler.source_id,
        geometry_id=session.sampler.geometry_id, sampling_method='AREA', sampling_algorithm=installation.AREA_ALGORITHM,
        winner_id='candidate', candidates=[choice])
    stamp = scene.get('wfrl_clearance_time_s')
    runtime.apply_mount_preset(scene, 'SEARCH', search_report=report)
    assert scene.get('wfrl_clearance_time_s') == stamp
    assert tuple(scene.wfrl_x100_rotation) == (0.,75.,180.)
    assert session.blade_result is not None
    identity = session.frame.identity
    for rejected in (report | dict(source_id='other'),
                     report | dict(errors=[dict(source_time_s=-1., reason='outside passage')])):
        try:
            runtime.apply_mount_preset(scene, 'SEARCH', search_report=rejected)
            raise AssertionError('Invalid search result accepted')
        except ValueError:
            pass
        assert session.frame.identity == identity
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
        'source_time_pairing', 'rewind', 'toggle', 'source_invalid_clears_tracking'])
    (out / 'result.json').write_text(json.dumps(result, indent=2))
    print('X100_OBSERVATIONS_PASS', str(out))
finally:
    addon.unregister()
