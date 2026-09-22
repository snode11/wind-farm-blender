import bpy, addon_utils, json, hashlib
from pathlib import Path
module='bl_ext.user_default.wfrl_blender'
assert addon_utils.check(module)==(True,True),addon_utils.check(module)
import bl_ext.user_default.wfrl_blender as addon
from bl_ext.user_default.wfrl_blender.panels import custom_cameras as panel
assert hasattr(bpy.types.Scene,'wfrl_capture_mode')
assert bpy.types.WindowManager.bl_rna.properties['wfrl_custom_slot'].hard_max==4
assert panel.WFRL_PT_CustomCameras.is_registered
root=Path('/Users/eason/Desktop/wfcrl/wind farm RL')
inventory=json.loads((root/'dist/wfrl_blender-0.3.6.inventory.json').read_text())
installed=Path(addon.__file__).parent
assert all(hashlib.sha256((installed/f['path']).read_bytes()).hexdigest()==f['sha256'] for f in inventory['files'])
Path('/var/folders/9w/yltr8zcx3gxb3rs6lr0kxwth0000gn/T/wfrl-install-v3-fixed-yhd3_dau/restart-result.json').write_text(json.dumps({'status':'PASS','enabled':True,'loaded':True,'slots':4,'capture_settings_registered':True,'module':addon.__file__,'verified_files':len(inventory['files'])},indent=2))
print('INSTALLED_036_V3_RESTART_PASS',flush=True)
