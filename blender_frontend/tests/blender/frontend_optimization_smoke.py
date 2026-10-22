"""Nonstandard IDs, hidden work, shared geometry and grass budget."""
from pathlib import Path
import sys, math
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import bpy,bmesh,wfrl_blender
from wfrl_blender import wake
from wfrl_blender.live_scene import build_live_scene
from wfrl_blender.scene_model import SceneDTO
wfrl_blender.register()
try:
    dto=SceneDTO.from_mapping(dict(layout=[dict(id='WT01',x=0,y=0),dict(id='WT02',x=504,y=0)]))
    build_live_scene(dto);s=bpy.context.scene
    assert [item[0] for item in wfrl_blender._turbine_items(s,None)]==['WT01','WT02']
    s.wfrl_selected_turbine='WT02'
    assert s.wfrl_selected_turbine=='WT02'
    s.wfrl_wake_display='CINEMATIC'
    ring=s.objects['WFRL.WakeProxy.WT01.Ring0'];before=tuple(ring.data.splines[0].points[0].co)
    assert ring.hide_get()
    assert wake.update_proxy_objects(s,phase=3)==0
    assert tuple(ring.data.splines[0].points[0].co)==before
    s['wfrl_proxy_phase']=3;s.wfrl_wake_display='SCIENTIFIC'
    assert tuple(ring.data.splines[0].points[0].co)!=before
    blades=[s.objects[f'WFRL.Turbine.{tid}.Blade{i}'] for tid in ('WT01','WT02') for i in range(1,4)]
    assert len({o.data.as_pointer() for o in blades})==1
    assert len({o.parent.as_pointer() for o in blades})==6
    grass=s.objects['WFRL.Landscape.GrassTufts']
    assert 50000<len(grass.data.vertices)<200000
    m=bmesh.new();m.from_mesh(s.objects['WFRL.Turbine.WT01.Spinner'].data)
    assert all(e.is_manifold and e.is_contiguous for e in m.edges)
    assert m.calc_volume(signed=True)>0;m.free()
    print('FRONTEND_OPTIMIZATION_SMOKE=PASS',len(grass.data.vertices))
finally:wfrl_blender.unregister()
