"""Isolated visible fixture for testing the box installer with actual UI input."""
from pathlib import Path
import os
import sys
import json
import traceback
import importlib
from types import SimpleNamespace
import bpy
from mathutils import Vector
ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[os.environ.get('WFRL_ADDON_ROOT', str(ROOT/'blender_frontend')),str(ROOT)]
MODULE=os.environ.get('WFRL_ADDON_MODULE','wfrl_blender')
addon=importlib.import_module(MODULE)
core=importlib.import_module(MODULE+'.custom_cameras')
rig=importlib.import_module(MODULE+'.stacked_camera_rig')
history=importlib.import_module(MODULE+'.custom_camera_history')
panel=importlib.import_module(MODULE+'.panels.stacked_camera_rig')
addon.register();addon.load_demo_scene()
scene=bpy.context.scene
root=scene.objects[core.ROOT_NAME]
area=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D')
space=area.spaces.active
space.show_region_ui=True
space.overlay.show_overlays=False
space.shading.type='MATERIAL'
space.region_3d.view_perspective='PERSP'
point=root.matrix_world @ Vector((0,0,1.6))
eye=root.matrix_world @ Vector((-8,-10,7))
space.region_3d.view_rotation=(point-eye).to_track_quat('-Z','Y')
space.region_3d.view_location=point
space.region_3d.view_distance=11
out=Path(os.environ.get('WFRL_TEST_OUTPUT','/tmp/wfrl-box-surface-window'))
out.mkdir(parents=True,exist_ok=True)
# Distinct title identifies this disposable test window, preserving the user's scene.
bpy.ops.wm.save_as_mainfile(filepath=str(out/'WFRL-9.27-盒体贴合验证.blend'))
state={'phase':0}
def check_window():
    try:
        window=bpy.context.window
        region=next(r for r in area.regions if r.type=='WINDOW')
        with bpy.context.temp_override(window=window,area=area,region=region):
            if state['phase']==0:
                assert bpy.ops.wfrl.custom_camera_action(action='SLOT',slot=2)=={'FINISHED'}
                assert bpy.context.window_manager.wfrl_custom_slot==2
                for r in area.regions:
                    if r.type=='UI':r.active_panel_category='View'
                state['baseline']=core.live_layout_hash(scene)
                assert bpy.ops.wfrl.stacked_box_pick('INVOKE_DEFAULT')=={'RUNNING_MODAL'}
                state['phase']=1
                return 1.
            if state['phase']==1:
                op=panel._ACTIVE
                # Exercise actual screen-to-world picking at a visible shell point.
                from bpy_extras.view3d_utils import location_3d_to_region_2d
                tree=core._geometry(scene)[0][-1]
                point,normal,*_=tree.ray_cast(Vector((0,-10,1.5)),Vector((0,1,0)))
                target=root.matrix_world @ point
                xy=location_3d_to_region_2d(region,space.region_3d,target)
                assert xy is not None
                event=SimpleNamespace(type='LEFTMOUSE',value='PRESS',shift=False,
                    mouse_x=region.x+xy.x,mouse_y=region.y+xy.y)
                assert op.modal(bpy.context,event)=={'RUNNING_MODAL'}
                assert op.transaction.ready,op.transaction.error
                before=core.live_layout_hash(scene)
                assert bpy.ops.wfrl.stacked_box_action(action='RIGHT')=={'FINISHED'}
                assert core.live_layout_hash(scene)!=before
                assert not op.transaction.error,op.transaction.error
                # Property editing uses the same preview transaction.
                bpy.context.window_manager.wfrl_box_spin=37.
                assert abs(rig.pose(scene)['surface_mount']['spin_deg']-37)<1e-5
                arm=scene.objects[rig.PREFIX+'.MountArm']
                bpy.context.view_layer.update()
                support_matrix=arm.matrix_world.copy()
                bpy.context.window_manager.wfrl_box_aim=-30.
                assert abs(rig.pose(scene)['surface_mount']['aim_deg']+30)<1e-5
                assert bpy.ops.wfrl.stacked_box_action(action='AIM_RIGHT')=={'FINISHED'}
                assert abs(rig.pose(scene)['surface_mount']['aim_deg']+15)<1e-5
                assert bpy.ops.wfrl.stacked_box_action(action='AIM_LEFT')=={'FINISHED'}
                assert abs(rig.pose(scene)['surface_mount']['aim_deg']+30)<1e-5
                bpy.context.view_layer.update()
                assert max(abs(arm.matrix_world[i][j]-support_matrix[i][j]) for i in range(4) for j in range(4))<3e-5
                # A sidebar click must pass through to its Confirm/Cancel buttons.
                ui=next(r for r in area.regions if r.type=='UI')
                event.mouse_x=ui.x+ui.width/2;event.mouse_y=ui.y+ui.height/2
                assert op.modal(bpy.context,event)=={'PASS_THROUGH'}
                # Close-up of the actual attached back face, while keeping controls visible.
                point=root.matrix_world @ Vector(rig.pose(scene)['center'])
                eye=point + root.matrix_world.to_3x3() @ Vector((-1,-1,.7))
                space.region_3d.view_rotation=(point-eye).to_track_quat('-Z','Y')
                space.region_3d.view_location=point
                space.region_3d.view_distance=1.35
                area.tag_redraw()
                state['phase']=2
                return 1.
            if state['phase']==2:
                bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP',iterations=1)
                bpy.ops.screen.screenshot(filepath=str(out/'installation-preview.png'))
                assert bpy.ops.wfrl.stacked_box_action(action='CANCEL')=={'FINISHED'}
                assert panel._ACTIVE is None
                assert core.live_layout_hash(scene)==state['baseline']
                state['phase']=21
                return .5
            if state['phase']==21:
                assert bpy.ops.wfrl.stacked_box_pick('INVOKE_DEFAULT')=={'RUNNING_MODAL'}
                op=panel._ACTIVE
                geometry=core._geometry(scene)[0]
                point,normal,*_=geometry[-1].ray_cast(Vector((0,-10,1.5)),Vector((0,1,0)))
                op.update(core.SurfaceAnchor(tuple(point),tuple(normal),0.,geometry[0]))
                bpy.context.window_manager.wfrl_box_aim=-30.
                assert bpy.ops.wfrl.stacked_box_action(action='CONFIRM')=={'FINISHED'}
                assert panel._ACTIVE is None
                installed_hash=core.live_layout_hash(scene)
                # Re-enter at the stored support anchor: orientation adjusts
                # immediately without making the user pick the same point again.
                assert bpy.ops.wfrl.stacked_box_pick('INVOKE_DEFAULT')=={'RUNNING_MODAL'}
                assert panel._ACTIVE.transaction.ready,panel._ACTIVE.transaction.error
                assert abs(bpy.context.window_manager.wfrl_box_aim+30)<1e-5
                assert bpy.ops.wfrl.stacked_box_action(action='AIM_RIGHT')=={'FINISHED'}
                assert abs(rig.pose(scene)['surface_mount']['aim_deg']+15)<1e-5
                assert bpy.ops.wfrl.stacked_box_action(action='CANCEL')=={'FINISHED'}
                assert core.live_layout_hash(scene)==installed_hash
                history.undo(scene)
                assert core.live_layout_hash(scene)==state['baseline']
                state['phase']=3
                return .5
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP',iterations=1)
            bpy.ops.screen.screenshot(filepath=str(out/'installation-controls.png'))
            (out/'window-validation.json').write_text(json.dumps({'status':'PASS','module':MODULE,
                'method':'scripted native-window operators and screen-ray modal event; not physical mouse input',
                'checks':['screen ray pick','inline rotate button','angle property preview','sidebar pass-through',
                          'housing aim property and +/-15 buttons keep support fixed','re-entry reuses anchor and aim',
                          'cancel restores layout','confirm and undo','visible panel']},indent=2))
            print('BOX_SURFACE_WINDOW_PASS',flush=True)
            bpy.ops.wm.quit_blender()
    except Exception:
        (out/'window-error.txt').write_text(traceback.format_exc())
        print(traceback.format_exc(),flush=True)
        bpy.ops.wm.quit_blender()
    return None
bpy.app.timers.register(check_window,first_interval=1.)
