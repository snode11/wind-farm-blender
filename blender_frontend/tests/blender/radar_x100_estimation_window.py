"""Read real canvas pixels for blade estimates and scripted toggle withdrawal."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback
import bpy
import gpu
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
import wfrl_blender as addon
from wfrl_blender import farm_flex
from wfrl_blender.radar_x100 import output, runtime
from wfrl_blender.radar_x100.core import analysis

OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=False)
STATE = dict(phase=0, captures={}, start=time.monotonic(), error=None)
NAMES = ('enabled', 'disabled', 'restored')


def capture():
    item = STATE.get('item')
    if (item is None or bpy.context.area.as_pointer() != item.area_id
            or bpy.context.window.as_pointer() != item.window_id
            or STATE['phase'] in STATE['captures'] or STATE['error']):
        return
    try:
        assert not item.error, item.error
        assert item.draw_count > STATE['baseline_draws']
        x, y, width, height = gpu.state.viewport_get()
        pixels = np.array(gpu.state.active_framebuffer_get().read_color(
            x, y, width, height, 4, 0, 'UBYTE'), dtype=np.uint8).reshape(height, width, 4)
        factor, tx, ty = item.auxiliary.items['grid'][1][0]
        tip = analysis.local([STATE['tip']], STATE['session'].context.world_from_analysis)[0, (0, 2)]
        center = tip * factor + (tx, ty)
        radius = int(12 * item.scale)
        cx, cy = np.rint(center).astype(int)
        region = pixels[max(0, cy-radius):min(height, cy+radius+1),
                        max(0, cx-radius):min(width, cx+radius+1), :3]
        orange = ((region[..., 0] > 180) & (region[..., 1] > 40)
                  & (region[..., 1] < 190) & (region[..., 2] < 110))
        count = int(orange.sum())
        middle = analysis.local([STATE['curve_middle']], STATE['session'].context.world_from_analysis)[0, (0, 2)]
        mx, my = np.rint(middle * factor + (tx, ty)).astype(int)
        region = pixels[max(0, my-6):min(height, my+7), max(0, mx-6):min(width, mx+7), :3]
        curve_count = int(((region[..., 0] > 180) & (region[..., 1] > 40)
                          & (region[..., 1] < 190) & (region[..., 2] < 110)).sum())
        phase = STATE['phase']
        STATE['captures'][phase] = dict(name=NAMES[phase], orange_tip_pixels=count,
            orange_curve_pixels=curve_count, tip_pixel=center.tolist(),
            draw_count=item.draw_count, width=width, height=height)
        image = bpy.data.images.new('X100 Canvas Evidence', width=width, height=height, alpha=True)
        try:
            image.pixels.foreach_set((pixels.astype(np.float32) / 255).ravel())
            image.filepath_raw = str(OUT / (NAMES[phase] + '.png'))
            image.file_format = 'PNG'
            image.save()
        finally:
            bpy.data.images.remove(image)
        assert count == 0 if phase == 1 else count > 20, (NAMES[phase], count, center)
        assert curve_count == 0 if phase == 1 else curve_count > 10, (NAMES[phase], curve_count)
    except Exception:
        STATE['error'] = traceback.format_exc()


def finish():
    handle = STATE.pop('handle', None)
    if handle is not None:
        bpy.types.SpaceImageEditor.draw_handler_remove(handle, 'WINDOW')
    result = dict(status='ERROR' if STATE['error'] else 'PASS', addon_path=addon.__file__,
        error=STATE['error'], captures=list(STATE['captures'].values()),
        checks=['visible_model_tip_and_curve_pixels', 'toggle_removes_estimate_pixels', 'toggle_restores_estimate_pixels',
                'toggle_preserves_raw_frame_and_clock'],
        scope='native GPU framebuffer with scripted controls; no external mouse or keyboard validation',
        output_source_sha256=hashlib.sha256(Path(output.__file__).read_bytes()).hexdigest())
    (OUT / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    addon.unregister()
    if STATE['error']:
        print(STATE['error'], flush=True)
    else:
        print('X100_ESTIMATION_WINDOW_PASS', str(OUT), flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        if 'item' not in STATE:
            addon.register()
            addon.load_demo_scene(farm_flex.default_package(), camera_rig=False)
            scene = bpy.context.scene
            scene.wfrl_x100_candidates = 2048
            session = runtime.start(scene)
            tip = session.blade_result['blades'][0]['tip_world']
            curve = session.blade_result['blades'][0]['curve_world']
            assert tip is not None, session.blade_result
            area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
            region = next(r for r in area.regions if r.type == 'WINDOW')
            with bpy.context.temp_override(area=area, region=region):
                assert bpy.ops.wfrl.x100_output() == {'RUNNING_MODAL'}
            item = output.for_scene(scene)
            item.reset('NACELLE')
            session.derive_analysis(item.roi)
            item.consume()
            item.zoom = 2.48832
            STATE.update(item=item, scene=scene, session=session, tip=tip, curve_middle=curve[len(curve)//2],
                frame=session.frame, raw=session.frame.points.tobytes(),
                stamp=scene.get('wfrl_clearance_time_s'), baseline_draws=item.draw_count)
            STATE['handle'] = bpy.types.SpaceImageEditor.draw_handler_add(capture, (), 'WINDOW', 'POST_PIXEL')
        if STATE['error']:
            finish()
            return None
        assert time.monotonic() - STATE['start'] < 45, 'Timed out awaiting canvas pixels'
        if STATE['phase'] in STATE['captures']:
            scene, session, item = STATE['scene'], STATE['session'], STATE['item']
            assert session.frame is STATE['frame'] and session.frame.points.tobytes() == STATE['raw']
            assert scene.get('wfrl_clearance_time_s') == STATE['stamp']
            if STATE['phase'] == 2:
                finish()
                return None
            STATE['phase'] += 1
            scene.wfrl_x100_blade_tracking = STATE['phase'] == 2
            session.update()
            assert (session.blade_result is not None) == scene.wfrl_x100_blade_tracking
            STATE['baseline_draws'] = item.draw_count
        STATE['item'].alive()[1].tag_redraw()
        return .1
    except Exception:
        STATE['error'] = traceback.format_exc()
        finish()
        return None


bpy.app.timers.register(tick, first_interval=.5)
