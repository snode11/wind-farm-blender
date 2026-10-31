"""Isolated installed-window X100 GPU and raw RGB pixel checks; leaves the window open."""
from dataclasses import replace, asdict
from pathlib import Path
import hashlib
import importlib
import json
import os
import sys
import traceback
import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path[:0] = [str(ROOT/'blender_frontend'), str(ROOT)]
addon = importlib.import_module(MODULE)
farm = importlib.import_module(MODULE+'.farm_flex')
radar = importlib.import_module(MODULE+'.radar_x100.runtime')
api = importlib.import_module(MODULE+'.radar_x100.core')
scene_api = importlib.import_module(MODULE+'.radar_x100.scene')
camera = importlib.import_module(MODULE+'.custom_cameras')
preview = importlib.import_module(MODULE+'.custom_camera_preview')
capture = importlib.import_module(MODULE+'.custom_camera_capture')
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=False)
STATE = {'step':0}


def image_hash(context, cam):
    images = preview.render_group(context, [cam], long_edge=320, create_texture=False)
    image = images[1]
    result = hashlib.sha256(image.rgba()).hexdigest(), image.state_hash, image.width, image.height
    assert len(set(image.rgba())) > 32, 'Pixel check must render nonuniform scene geometry'
    for item in images.values():
        item.free()
    return result


def run():
    try:
        if STATE['step'] == 0:
            expected = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
            if expected:
                assert Path(addon.__file__).resolve().parent == Path(expected).resolve()
            if MODULE == 'wfrl_blender':
                addon.register()
            addon.load_demo_scene(farm.default_package(), camera_rig=False)
            scene = bpy.context.scene
            scene.name = 'X100 Isolated Validation'
            scene.wfrl_x100_candidates = 512
            session = radar.start(scene)
            area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
            region = next(r for r in area.regions if r.type == 'WINDOW')
            bpy.context.window_manager.wfrl_home_page = 'X100'
            area.spaces.active.show_region_ui = True
            area.spaces.active.region_3d.view_location = (-3,0,60)
            area.spaces.active.region_3d.view_distance = 130
            area.spaces.active.region_3d.view_rotation = Vector((160,-210,100)).to_track_quat('Z','Y')
            area.spaces.active.region_3d.view_perspective = 'PERSP'
            area.spaces.active.shading.type = 'MATERIAL'
            geometry = camera._geometry(scene)[0]
            point, normal, *_ = geometry[-1].find_nearest(Vector((0,-10,.2)))
            cam = camera.begin_draft(scene, 1)
            camera.place_on_surface(cam,camera.SurfaceAnchor(tuple(point),tuple(normal),.1,geometry[0]))
            camera.apply_parameters(cam,replace(camera.parameters(cam),yaw=180,pitch=-50,
                fov=90,vfov=60,output_long_edge_px=320))
            camera.commit_draft(scene,1,cam)
            STATE.update(step=1,area=area,region=region,cam=cam,session=session)
            return 3.
        area, region = STATE['area'], STATE['region']
        with bpy.context.temp_override(area=area,region=region):
            session, scene, cam = STATE['session'], bpy.context.scene, STATE['cam']
            image_hash(bpy.context,cam)
            before = image_hash(bpy.context,cam)
            fov_state = {layer.name:session.fov.hide_get(view_layer=layer) for layer in scene.view_layers}
            clock = (scene.frame_current,scene.frame_subframe)
            optics = asdict(camera.parameters(cam))
            session.fov.data.vertices[1].co.x += 50
            session.fov.data.update()
            other = api.simulation.simulate(session.sampler.sample,session.sampler.interval[0]+.1,
                sensor_id='X100-T1',session_id='pixel-probe',sequence=3,interval_s=session.sampler.interval,
                candidate_budget=512,visibility=scene_api.bvh_visibility)
            session._display(other)
            after = image_hash(bpy.context,cam)
            assert before == after, (before,after)
            assert (scene.frame_current,scene.frame_subframe) == clock
            assert asdict(camera.parameters(cam)) == optics
            assert {layer.name:session.fov.hide_get(view_layer=layer) for layer in scene.view_layers} == fov_state
            job = capture.Capture(bpy.context, OUT, [float(session.sampler.interval[0])])
            assert capture.active()
            for action in (radar.start, radar.stop):
                try:
                    action(scene)
                except ValueError:
                    pass
                else:
                    raise AssertionError('Installation changes must be refused during camera capture')
            assert radar.session_for(scene) is session
            import time
            deadline = time.monotonic() + 20
            while not job.closed:
                job.step(bpy.context)
                if job._writer is not None:
                    job._writer.done.wait(.001)
                assert time.monotonic() < deadline, 'Raw capture did not finish'
            capture_manifest = json.loads((job.path/'manifest.json').read_text())
            assert capture_manifest['status'] == 'complete', capture_manifest
            result = dict(status='PASS',addon_path=addon.__file__,core_path=api.schema.__file__,
                raw_rgba_sha256=before[0],raw_state_sha256=before[1],resolution=list(before[2:]),
                auxiliary_update_preserves_raw_pixels_state_visibility_clock_and_optics=True,
                simulation_points=len(session.frame.points),source=session.frame.source_kind,
                actual_raw_capture_complete=True, installation_guard_during_capture=True,
                raw_capture_path=str(job.path))
            (OUT/'result.json').write_text(json.dumps(result,indent=2))
            print('X100_WINDOW_PASS',json.dumps(result),flush=True)
            radar.start(scene)
            # A saved validation copy gives the dedicated UI agent a distinct window title.
            bpy.ops.wm.save_as_mainfile(filepath=str(OUT/'X100-Isolated-Validation.blend'))
        return None
    except Exception:
        (OUT/'result.json').write_text(json.dumps(dict(status='ERROR',error=traceback.format_exc()),indent=2))
        traceback.print_exc()
        return None


bpy.app.timers.register(run,first_interval=1.)
