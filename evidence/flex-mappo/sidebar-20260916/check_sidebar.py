import os, runpy, json
from pathlib import Path
from types import SimpleNamespace
import bpy
root=Path('/Users/eason/Desktop/wfcrl/wind farm RL')
loaded=runpy.run_path(os.environ.get('WFRL_FARM_LAUNCHER',str(root/'scripts/blender/open_farm_flex.py')))
from wfrl_blender.panels import farm_replay,gimbal
from wfrl_blender import clearance_replay,farm_flex
scene=bpy.context.scene
class Layout:
    def __init__(self,opened=()):self.opened=set(opened);self.labels=[];self.ops=[];self.props=[];self.events=[]
    def label(self,*,text='',**kw):self.labels.append(text);self.events.append(('label',text))
    def row(self,**kw):return self
    def column(self,**kw):return self
    def box(self):return self
    def panel(self,name,**kw):return self,self if name in self.opened else None
    def operator(self,name,**kw):self.ops.append((name,kw));self.events.append(('op',name));return SimpleNamespace()
    def prop(self,owner,name,**kw):self.props.append(name)
    def prop_enum(self,*a,**kw):pass
assert farm_replay.unified_panel_active(scene)
assert not gimbal.WFRL_PT_Gimbal.poll(bpy.context)
assert loaded['WFRL_PT_FarmFlex'].poll(bpy.context)
layout=Layout();farm_replay.draw(layout,bpy.context)
assert [o[0] for o in layout.ops].count('wfrl.clearance_playback')==1
assert [o[0] for o in layout.ops].count('wfrl.clearance_restart')==1
assert layout.props.count('wfrl_flex_show_tip_trails')==1
assert layout.props.count('wfrl_clearance_progress')==1
assert not any('wfrl_clearance_normal_path'==p for p in layout.props)
assert layout.events.index(('op','wfrl.clearance_playback'))<layout.events.index(('label','雷达与净空 · T1 · B2'))
assert '雷达到叶片距离：-- m' in layout.labels
assert '仿真真值：-- m' in layout.labels
assert '' not in layout.labels
full=Layout(['wfrl_farm_more_views','wfrl_farm_gimbal','wfrl_farm_measurement_details','wfrl_farm_data'])
farm_replay.draw(full,bpy.context)
assert [o[0] for o in full.ops].count('wfrl.clearance_view')==2
assert '["wfrl_farm_flex_path"]' in full.props
# Observe real fresh, held and missing samples without changing their semantics.
reader=clearance_replay.reader_for(scene)
row=next(r for r in reader.package.measurements if r['beams']['B2']['valid'])
for sample in (reader.at(row['time_s']),reader.at(row['time_s']+.1),reader.at(reader.start_s)):
    card=Layout();farm_replay.draw_radar(card,scene,sample)
    m=sample['measurement'] or {}
    from wfrl_blender.panels.clearance import number
    for label,key in [('雷达到叶片距离：','slant_range_m'),('仿真真值：','truth_m'),('B2 估计：','estimate_m')]:
        assert label+number(m.get(key))+' m' in card.labels
bpy.ops.wfrl.farm_flex_view(turbine='T2')
assert scene.wfrl_gimbal_turbine=='T2'
assert scene['wfrl_clearance_turbine']=='T2'
# Advanced cameras leave replay time and selection intact.
stamp=clearance_replay.sample(scene)['time_s']
for view in ('MEASUREMENT','RADAR'):
    assert bpy.ops.wfrl.clearance_view(view=view)=={'FINISHED'}
    assert clearance_replay.sample(scene)['time_s']==stamp
    assert 'T2' in scene.camera.name
farm_flex._ACTIVE.enabled=False
assert gimbal.WFRL_PT_Gimbal.poll(bpy.context)
assert not loaded['WFRL_PT_FarmFlex'].poll(bpy.context)
farm_flex._ACTIVE.enabled=True
out=Path(os.environ['WFRL_TEST_OUTPUT']);out.mkdir(parents=True,exist_ok=True)
(out/'sidebar-checks.json').write_text(json.dumps(dict(status='PASS',labels=layout.labels,expanded_labels=full.labels),ensure_ascii=False,indent=2))
print('UNIFIED_SIDEBAR_PASS',flush=True)
