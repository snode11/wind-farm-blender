import bpy, addon_utils, json
from pathlib import Path
module='bl_ext.user_default.wfrl_blender'
status=addon_utils.check(module)
assert status==(True,True),status
import bl_ext.user_default.wfrl_blender as addon
Path('/var/folders/9w/yltr8zcx3gxb3rs6lr0kxwth0000gn/T/wfrl-install-v3-dl5f09l8/restored-load.json').write_text(json.dumps({'enabled':status[0],'loaded':status[1],'module':addon.__file__,'blender':bpy.app.version_string},indent=2))
print('RESTORED_INSTALL_LOAD_PASS')
