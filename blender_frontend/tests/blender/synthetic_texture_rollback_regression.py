"""Owned native texture allocations must roll back and remain retryable."""
from pathlib import Path
import os
import json
import sys
from types import SimpleNamespace as NS
from unittest.mock import patch
import bpy

ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(ROOT),str(ROOT/'blender_frontend')]
import wfrl_blender as addon
addon.register()
from wfrl_blender import split_surface_texture as synthetic, blade_recon_review as review
from wfrl_blender import blade_recon_surface as surface, split_mappo_texture as same_source
from wfrl_blender import split_texture_settings as settings
from wfrl_blender.blade_recon_data import detail_recon_path, detail_texture_path, ReconSequence, TexturePackage
from blade_recon import surface_io
OUTPUT=Path(os.environ['WFRL_TEST_OUTPUT']);OUTPUT.mkdir(parents=True,exist_ok=False)
GROUPS=('objects','meshes','materials','images','cameras','texts','node_groups','worlds','scenes')
def inventory(): return {k:{v.as_pointer() for v in getattr(bpy.data,k)} for k in GROUPS}
checks=[];scene=bpy.context.scene

def reject(call):
    try: call()
    except RuntimeError: return
    raise AssertionError('failure boundary did not run')

def after(function):
    def fail(*a,**kw): function(*a,**kw);raise RuntimeError('injected after native allocation')
    return fail

for name,target,attribute in [('material',review,'_texture_material'),('mesh',review,'_mesh'),('activation',synthetic,'_activate')]:
    before=inventory();keys=dict(scene.items());display=settings.capture(scene)
    with patch.object(target,attribute,after(getattr(target,attribute))): reject(lambda:synthetic.ensure(scene))
    assert before==inventory(),(name,{k:len(inventory()[k]-before[k]) for k in GROUPS})
    assert dict(scene.items())==keys and scene.as_pointer() not in synthetic._SESSIONS
    assert settings.capture(scene)==display
    checks.append('synthetic_'+name)

sequence=ReconSequence(detail_recon_path());textures=TexturePackage(detail_texture_path(),sequence.rotor)
metadata={'model_sha256':sequence.model_sha256,'recon_sha256':sequence.source_sha256,
          'appearance_scope':'native allocation fixture','textures':[{'path':str(p),'sha256':__import__('hashlib').sha256(p.read_bytes()).hexdigest()} for p in textures.images]}
def decoded(path,image_reader):
    for p in textures.images: image_reader(p)
    return dict(metadata=metadata,recon_data=sequence.data)
context=NS(scene=scene,window=NS(scene=scene),screen=None)
for kind in ['pack','mesh','apply']:
    before=inventory()
    register=same_source._ImportResources.register
    def record(resources,group,block,**kw):
        register(resources,group,block,**kw)
        if kind=='pack' and group=='images':
            block.buffers_free();block.filepath='/missing/native-pack-fault.png'
    with patch.object(surface_io,'load_surface_package',decoded),patch.object(same_source._ImportResources,'register',record):
        if kind=='mesh':
            with patch.object(surface,'_mesh',after(surface._mesh)): reject(lambda:surface.load_surface(context,detail_texture_path()/'surface.json'))
        elif kind=='apply':
            with patch.object(surface,'apply',side_effect=RuntimeError('apply')):reject(lambda:surface.load_surface(context,detail_texture_path()/'surface.json'))
        else:
            try: surface.load_surface(context,detail_texture_path()/'surface.json')
            except RuntimeError as exc: assert 'Unable to pack' in str(exc),str(exc)
            else: raise AssertionError('native pack failure was not reached')
    assert context.window.scene==scene
    assert inventory()==before,(kind,{k:len(inventory()[k]-before[k]) for k in GROUPS})
    checks.append('surface_'+kind)

# A transient cleanup error must retain the exact ledger until the next import.
before=inventory();rollback=same_source._ImportResources.rollback;calls=[]
def failed_cleanup(resources):calls.append(resources);return [RuntimeError('remove blocked')]
with patch.object(review,'_mesh',after(review._mesh)),patch.object(same_source._ImportResources,'rollback',failed_cleanup):
    reject(lambda:synthetic.ensure(scene))
assert scene.as_pointer() in synthetic._PENDING_IMPORTS
pending=synthetic._PENDING_IMPORTS[scene.as_pointer()]
assert pending is calls[0] and pending.records
with patch.object(review,'_mesh',after(review._mesh)):reject(lambda:synthetic.ensure(scene))
assert scene.as_pointer() not in synthetic._PENDING_IMPORTS and inventory()==before
checks.append('cleanup_retry')
before=inventory()
with patch.object(surface_io,'load_surface_package',decoded),patch.object(surface,'apply',side_effect=RuntimeError('apply')):
    with patch.object(same_source._ImportResources,'rollback',failed_cleanup):
        reject(lambda:surface.load_surface(context,detail_texture_path()/'surface.json'))
    assert surface._PENDING_IMPORTS
    reject(lambda:surface.load_surface(context,detail_texture_path()/'surface.json'))
assert not surface._PENDING_IMPORTS and inventory()==before
checks.append('surface_cleanup_retry')
value=synthetic.ensure(scene);assert value['index'] is not None
checks.append('synthetic_retry_success')
result=dict(status='PASS',checks=checks,blender=bpy.app.version_string,module=synthetic.__file__,scope='Native datablocks and predecoded RGB helper; no rendered pixel or scientific acceptance')
(OUTPUT/'result.json').write_text(json.dumps(result,indent=2));print('SYNTHETIC_TEXTURE_ROLLBACK_PASS')
