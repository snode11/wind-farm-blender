"""Independent editor, output and G scan regression without scene rendering.

The guard covers render dispatch through gw184.defect_outputs only; it does not
intercept arbitrary Blender operators elsewhere in the process.
"""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import resource
import statistics
import struct
import sys
import time
import zlib
from unittest.mock import patch
import bpy
from mathutils import Vector
ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(Path(__file__).resolve().parent), str(ROOT/'projects/gw184-single'), str(ROOT/'blender_frontend')]
from defect_render_guard import GUARD_SCOPE, forbid_output_render
from gw184.defect_editor import register, EditorSession, props_from_defect, draft_from_props
from gw184.defect_outputs import create_run, save_analysis, crop_png, write_json
from gw184.defect_timeline import FrameMapping, scan
from gw184.defect_schema import new_defect
from gw184.scene import at_time

out = Path(sys.argv[sys.argv.index('--')+1]).resolve()
out.mkdir(parents=True, exist_ok=True)
checks = []

def check(name, test):
    assert test, name
    checks.append(name)


def timed_pose(editor):
    costs=[]
    before=editor.geometry.boolean_calls
    for i in range(100):
        started=time.perf_counter()
        at_time(editor.rig, 100+i/30)
        costs.append((time.perf_counter()-started)*1000)
    check('pose updates perform no Boolean', before==editor.geometry.boolean_calls)
    return dict(median_ms=statistics.median(costs), p95_ms=sorted(costs)[94], samples=len(costs))

with forbid_output_render() as renderer:
    spec=importlib.util.spec_from_file_location('defect_entry',ROOT/'scripts/blender/gw184_defects.py')
    entry=importlib.util.module_from_spec(spec);spec.loader.exec_module(entry)
    try: entry.main(['--mode','images','--output',str(out/'must-not-exist')])
    except ValueError as exc: check('image guard rejects before creating outputs', 'Rendering is disabled' in str(exc) and not (out/'must-not-exist').exists())
    else: raise AssertionError('Missing render guard')
    register()
    editor=EditorSession()
    base_perf=timed_pose(editor)
    doc=editor.store.document
    doc['defects']=[new_defect(editor.surface,'fine_crack'),new_defect(editor.surface,'open_crack')]
    started=time.perf_counter();editor.commit_document(doc);commit_s=time.perf_counter()-started
    damaged_perf=timed_pose(editor)
    editor.set_time(100.)
    cameras=[cam.matrix_world.copy() for cam in editor.rig['cameras']]
    original=editor.store.document
    original_payload=editor.payload
    d=editor.begin(0)
    d['anchor']['s_m']=43.;d['revision']+=1
    editor.preview_draft()
    check('preview isolated from committed configuration',editor.store.document==original)
    editor.scene.frame_set(1)
    check('native scrub stays frozen while editing', abs(editor.scene['defect_time_s']-100.)<1e-9)
    editor.finish(False)
    check('cancel restores whole committed payload',editor.payload is original_payload and editor.geometry.active is original_payload and editor.store.document==original)
    d=editor.begin(0);d['anchor']['s_m']=43.;d['revision']+=1
    editor.finish(True)
    check('edit commits stable id with revision',editor.store.document['defects'][0]['id']==original['defects'][0]['id'] and editor.store.document['defects'][0]['revision']==2)
    editor.store.undo()
    check('undo restores entire document',editor.store.document==original)
    editor.store.redo()
    check('redo restores edit',editor.store.document['defects'][0]['anchor']['s_m']==43.)
    editor.store.undo()
    index=1
    editor.mutate('TOGGLE',index)
    check('disable one keeps other support',{v['defect_id'] for v in editor.payload['supports']}=={original['defects'][0]['id']})
    editor.store.undo()
    editor.mutate('COPY',0)
    copied=editor.store.document['defects'][-1]
    check('copy uses new stable id and disabled state',copied['id']!=original['defects'][0]['id'] and not copied['enabled'])
    editor.mutate('DELETE',2)
    check('delete removes only target',len(editor.store.document['defects'])==2)
    editor.save(out/'saved-defects.json')
    saved=editor.store.document
    editor.mutate('TOGGLE',0)
    editor.load(out/'saved-defects.json')
    check('load snapshot restores saved ids revisions config',editor.store.document==saved)
    point=editor.surface.point(saved['defects'][0]['anchor'])
    frame=editor.surface.frame(saved['defects'][0]['anchor'])
    blade=editor.rig['blades'][0]
    world=blade.matrix_world@Vector(point)
    normal=(blade.matrix_world.to_3x3()@Vector(frame['normal'])).normalized()
    anchor=editor.locate_ray(world+normal, -normal, 1)
    check('healthy click maps back to reference surface',(Vector(editor.surface.point(anchor))-Vector(point)).length<1e-5)
    active=editor.payload;state=editor.store.document;version=editor.store.version
    with patch.object(editor.geometry,'_cut',side_effect=RuntimeError('deliberate transaction failure')):
        candidate=editor.store.document;candidate['defects'][1]['revision']+=1;candidate['defects'][1]['anchor']['s_m']=46.
        try: editor.commit_document(candidate)
        except RuntimeError: pass
        else: raise AssertionError('Expected build failure')
    check('failed rebuild retains mesh material config and cache identities',editor.payload is active and editor.geometry.active is active and editor.store.document==state and editor.store.version==version)
    mapping=FrameMapping(100.,.1,30,30)
    run=create_run(editor,out/'g-run',mapping=mapping)
    records=save_analysis(editor,run)
    check('per-support three-camera G records',len(records)==len(editor.payload['supports'])*3 and all(r['time_s']==100. for r in records))
    editor.set_time(101.)
    check('time change invalidates prior analysis',editor.records is None and editor.scene['defect_analysis_status']=='NOT_EVALUATED')
    editor.set_time(100.)
    check('appearance status never inferred from geometry',all(r['appearance_review']['status']=='NOT_REVIEWED' for r in records))
    count=[0]
    result=scan(editor.rig,editor.payload['supports'],mapping,cancel=lambda:count[0]>=1,progress=lambda *_:count.__setitem__(0,count[0]+1))
    check('cancel records partial range and stable resume index',not result['completed'] and result['completed_frames']==1 and result['next_frame_index']==1)
    check('cancel restores timestamp',abs(editor.scene['defect_time_s']-100)<1e-9)
    full=scan(editor.rig,editor.payload['supports'],mapping)
    check('all three frames use absolute timeline',full['completed'] and sorted({r['time_s'] for r in full['records']})==[mapping.sample(i)['time_s'] for i in range(3)])
    write_json(out/'scan.json', {k:v for k,v in full.items() if k!='records'})
    check('fixed cameras unchanged by editing scan and navigation',all(cam.matrix_world==m for cam,m in zip(editor.rig['cameras'],cameras)))
    # Exercise lossless crop with a tiny known RGB fixture; not a scene render.
    def chunk(k,d): return struct.pack('>I',len(d))+k+d+struct.pack('>I',zlib.crc32(k+d)&0xffffffff)
    pixels=bytes(range(18))
    png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',3,2,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\0'+pixels[:9]+b'\0'+pixels[9:]))+chunk(b'IEND',b'')
    (out/'crop-fixture.png').write_bytes(png)
    crop_png(out/'crop-fixture.png',out/'crop-result.png',[1,0,3,2])
    raw=(out/'crop-result.png').read_bytes();pos=8;data=b''
    while pos<len(raw):
        n=struct.unpack('>I',raw[pos:pos+4])[0]
        if raw[pos+4:pos+8]==b'IDAT':data+=raw[pos+8:pos+8+n]
        pos+=n+12
    check('crop exactly preserves original RGB pixels',zlib.decompress(data)==b'\0'+pixels[3:9]+b'\0'+pixels[12:18])
    # Save/rebuild configuration identity. No UI or renderer claim is inferred.
    reopened=EditorSession(json.loads((out/'saved-defects.json').read_text()))
    check('reopen deterministic support geometry',reopened.store.document==saved and reopened.payload['supports']==editor.payload['supports'])
    editor.mutate('TOGGLE',0)
    try: save_analysis(editor,run)
    except ValueError as exc: check('stale run refuses new geometry', 'STALE_STATE' in str(exc))
    else: raise AssertionError('Stale run accepted')
    check('no scene images generated',not list((out/'g-run').rglob('*.png')))
    assert renderer.call_count == 0, 'No output render may be attempted'
    result=dict(result='PASS',checks=checks,rendered=False,blender=bpy.app.version_string,
                render_calls=renderer.call_count,render_guard_scope=GUARD_SCOPE,
                healthy_pose=base_perf,damaged_pose=damaged_perf,edit_commit_s=commit_s,
                peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                performance_targets=dict(pose_p95_ms=33.333333,edit_commit_s=5),
                performance_pass=damaged_perf['p95_ms']<=33.333333 and commit_s<=5,
                limitations=['No render or appearance review','Native operator/API validation; separate visible-window interaction check required'])
    write_json(out/'result.json',result)
    print(json.dumps(result,ensure_ascii=False))
