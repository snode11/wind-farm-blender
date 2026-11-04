import bpy,importlib,json,hashlib,os,sys,tomllib
from pathlib import Path
module='bl_ext.user_default.wfrl_blender'
expected=Path(os.environ['WFRL_EXPECTED_ADDON_ROOT']).resolve()
assert module in bpy.context.preferences.addons, 'Daily extension enablement not persisted'
addon=importlib.import_module(module)
assert Path(addon.__file__).resolve()==expected/'__init__.py'
assert 'wfrl_blender' not in sys.modules
manifest=tomllib.loads((expected/'blender_manifest.toml').read_text())
assert manifest['version']==os.environ['EXPECTED_EXTENSION_VERSION']
prefs=bpy.context.preferences.addons[module].preferences
fields={name:getattr(prefs,name) for name in ('project_dir','backend_python','mpi_path','fastfarm_path','default_scene','port','camera_video_file','video_ffmpeg','video_mediamtx','video_rtsp_port','video_rtsp_lan')}
digest=hashlib.sha256(json.dumps(fields,sort_keys=True).encode()).hexdigest()
loaded={}
for name,mod in tuple(sys.modules.items()):
 if name==module or name.startswith(module+'.'):
  file=getattr(mod,'__file__',None)
  if file:
   file=Path(file).resolve();assert file.is_relative_to(expected),(name,str(file));loaded[name]=str(file)
output=dict(status='PASS',module=module,module_file=str(expected/'__init__.py'),extension_version=manifest['version'],blender=bpy.app.version_string,loaded_module_files_checked=len(loaded),loaded_modules=loaded,preference_fields=len(fields),preferences_digest=digest,home_registered=hasattr(bpy.types.Scene,'wfrl_farm_panel_page'))
if os.environ.get('REQUIRE_X100_OUTPUT'):
 output['x100_output_registered']=importlib.import_module(module+'.radar_x100.output').WFRL_OT_X100Output.is_registered
 assert output['x100_output_registered']
Path(os.environ['WFRL_IDENTITY_OUTPUT']).write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
print('DAILY_RUNTIME_IDENTITY_PASS')
