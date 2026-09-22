import sys,bpy
sys.path.insert(0,'/Users/eason/Library/Application Support/Blender/5.2/extensions/user_default')
import wfrl_blender
from wfrl_blender import farm_flex
wfrl_blender.register()
wfrl_blender.load_demo_scene(farm_flex.default_package())
preview=farm_flex._ACTIVE
print('INSTALLED_MODULE',wfrl_blender.__file__)
