"""Blender scene adapter, reusing the frontend's mesh and pinhole helpers."""
import math
import bpy
from mathutils import Matrix, Vector
from wfrl_blender.camera_projection import rotation, intrinsics, cv_from_world
from wfrl_blender.scene_builder import _mesh_object, _rounded_nacelle_mesh, _cone
from .model import blade_mesh, boundaries, motion_at
from .defects import cut_defects


def plain_material(name, color, roughness=.45):
    mat = bpy.data.materials.new('GW184.'+name)
    mat.use_nodes = True
    mat.diffuse_color = (*color, 1)
    node = mat.node_tree.nodes.get('Principled BSDF')
    node.inputs['Base Color'].default_value = (*color, 1)
    node.inputs['Roughness'].default_value = roughness
    return mat


def build(g, m, c, width=3840, samples=16, *, apply_legacy_defects=True):
    # An independent scene and collection; no deletion or registration of WFRL runtime.
    scene = bpy.data.scenes.new('GW184.Single.Synthetic')
    bpy.context.window.scene = scene
    collection = bpy.data.collections.new('GW184.Single')
    scene.collection.children.link(collection)
    materials = {k: plain_material(k, color) for k, color in {
        'blade': (.82,.83,.80), 'nacelle': (.67,.70,.72),
        'hub': (.72,.73,.73), 'tower': (.66,.69,.71),
        'housing': (.07,.085,.10), 'ground': (.15,.22,.12)}.items()}

    def empty(name, parent=None, location=(0,0,0)):
        obj = bpy.data.objects.new('GW184.'+name, None)
        collection.objects.link(obj)
        obj.parent, obj.location = parent, location
        return obj

    def cube(name, location, size, material):
        from wfrl_blender.scene_builder import _primitive
        return _primitive(collection, 'cube', 'GW184.'+name, location,
                          tuple(x/2 for x in size), materials[material])

    verts, faces = _rounded_nacelle_mesh(g['nacelle_length_m'],g['nacelle_width_m'],g['nacelle_height_m'])
    nacelle = _mesh_object(collection,'GW184.Nacelle',verts,faces,materials['nacelle'])
    nacelle.location = (g['nacelle_front_x_m']+g['nacelle_length_m']/2,0,g['hub_height_m'])
    tower = _cone(collection,'GW184.Tower',3.5,2.1,g['hub_height_m'],materials['tower'])
    tower.location=(g['tower_x_m'],0,g['hub_height_m']/2)
    from wfrl_blender.scene_builder import _primitive
    hub = _primitive(collection,'uv_sphere','GW184.Hub',(0,0,g['hub_height_m']),
                     (2.6,g['root_radius_m'],g['root_radius_m']),materials['hub'])
    cube('Ground',(0,0,-.2),(1600,1600,.4),'ground')
    rotor=empty('Rotor',location=(0,0,g['hub_height_m']))
    verts, faces, stations=blade_mesh(g)
    blades, pitch_roots=[],[]
    for i in range(3):
        azimuth=empty(f'Blade{i+1}.Azimuth',rotor)
        azimuth.rotation_euler.x=2*math.pi*i/3
        collar=_cone(collection,f'GW184.Hub.RootCollar{i+1}',g['root_diameter_m']/2,g['root_diameter_m']/2,1.,materials['hub'])
        collar.parent=azimuth
        collar.location=(0,0,g['root_radius_m']-.5)
        pitch=empty(f'Blade{i+1}.Pitch',azimuth,(0,0,g['root_radius_m']))
        blade=_mesh_object(collection,f'GW184.Blade{i+1}',verts,faces,materials['blade'])
        blade.parent=pitch
        for poly in blade.data.polygons:
            poly.use_smooth = len(poly.vertices)<=4
        blades.append(blade)
        pitch_roots.append(pitch)
    defects=(cut_defects(blades,collection,plain_material('DefectInterior',(.095,.072,.052),.9))
             if apply_legacy_defects else [])
    centre=Vector(c['center_relative_hub_m'])+Vector((0,0,g['hub_height_m']))
    cameras=[]
    for i, row in enumerate(c['cameras']):
        data=bpy.data.cameras.new('GW184.'+row['name'])
        data.type='PERSP'
        data.lens=c['lens_mm']
        data.sensor_width,data.sensor_height=c['sensor_mm']
        data.sensor_fit='HORIZONTAL'
        data.clip_start,data.clip_end=.01,3000
        data.dof.use_dof=False
        camera=bpy.data.objects.new(row['name'],data)
        collection.objects.link(camera)
        camera.location=centre+Vector((0,0,(i-1)*c['vertical_spacing_m']))
        camera.rotation_euler=Matrix(rotation(row['yaw_deg'],row['pitch_deg'],row['roll_deg'])).to_euler()
        cameras.append(camera)
    # Shared enclosure with explicitly open optical front/top. Solid back, sides,
    # bottom and support are real occluders in rendering and coverage ray tests.
    depth,bw,bh=c['housing_size_m']
    x,y,z=centre
    cube('Housing.Back',(x+depth,y,z),(.015,bw,bh),'housing')
    for sign in (-1,1):
        cube('Housing.Side'+str(sign),(x+depth/2,y+sign*bw/2,z),(depth,.01,bh),'housing')
    cube('Housing.Bottom',(x+depth/2,y,z-bh/2),(depth,bw,.01),'housing')
    roof=g['hub_height_m']+g['nacelle_height_m']/2
    cube('Housing.Support',(x+depth*.7,y,(roof+z-bh/2)/2),(.055,.055,z-bh/2-roof),'housing')
    world=bpy.data.worlds.new('GW184.FixedSky')
    world.use_nodes=True
    world.node_tree.nodes['Background'].inputs['Color'].default_value=(.36,.54,.78,1)
    world.node_tree.nodes['Background'].inputs['Strength'].default_value=.7
    scene.world=world
    light=bpy.data.lights.new('GW184.Sun','SUN')
    light.energy=2.3
    light.angle=math.radians(4)
    sun=bpy.data.objects.new('GW184.Sun',light)
    collection.objects.link(sun)
    sun.rotation_euler=(math.radians(28),math.radians(-38),math.radians(25))
    scene.render.engine='CYCLES'
    scene.cycles.samples=samples
    if hasattr(scene,'eevee'):
        scene.eevee.taa_render_samples=64
    scene.cycles.use_denoising=True
    scene.cycles.device='CPU'
    scene.render.resolution_x=width
    scene.render.resolution_y=round(width*9/16)
    scene.render.resolution_percentage=100
    scene.render.fps=m['fps']
    scene.render.fps_base=1
    scene.render.image_settings.file_format='PNG'
    scene.render.image_settings.color_mode='RGB'
    scene.render.image_settings.color_depth='8'
    scene.render.film_transparent=False
    scene.render.use_motion_blur=False
    scene.view_settings.view_transform='Standard'
    scene.view_settings.look='None'
    scene.view_settings.exposure=0
    scene.view_settings.gamma=1
    scene.frame_start=1
    scene.frame_end=round(boundaries(m)[-1]*m['fps'])
    scene.camera=cameras[0]
    scene['evidence']='GW184-size synthetic rigid geometry and prescribed animation, not real turbine footage or FAST.Farm'
    scene['time_mapping']='frame 1 -> 0 seconds; t=(frame-1)/fps; instantaneous shutter, motion blur OFF'
    scene['installation_status']='ASSUMED: mechanical mounting, 11 m datum and common aperture unconfirmed'
    bpy.context.view_layer.update()
    return dict(scene=scene,collection=collection,rotor=rotor,pitch_roots=pitch_roots,blades=blades,
                cameras=cameras,stations=stations,geometry=g,motion=m,layout=c,defects=defects)


def pose(rig, azimuth_deg, pitch_deg):
    rig['rotor'].rotation_euler.x=math.radians(azimuth_deg)
    for p in rig['pitch_roots']:
        p.rotation_euler.z=math.radians(pitch_deg)
    bpy.context.view_layer.update()


def at_time(rig,t):
    state=motion_at(t,rig['motion'])
    pose(rig,state['azimuth_deg'],state['pitch_deg'])
    return state


def bake(rig):
    scene,m=rig['scene'],rig['motion']
    # Bake every frame, including endpoint. Playback can seek/reverse without handlers.
    for frame in range(1,scene.frame_end+2):
        state=motion_at((frame-1)/m['fps'],m)
        rig['rotor'].rotation_euler.x=math.radians(state['azimuth_deg'])
        for p in rig['pitch_roots']:
            p.rotation_euler.z=math.radians(state['pitch_deg'])
        rig['rotor'].keyframe_insert('rotation_euler',index=0,frame=frame)
        for p in rig['pitch_roots']:
            p.keyframe_insert('rotation_euler',index=2,frame=frame)
    scene.frame_set(1)


def camera_metadata(rig):
    g,c=rig['geometry'],rig['layout']
    w,h=rig['scene'].render.resolution_x,rig['scene'].render.resolution_y
    hfov,vfov=[math.degrees(2*math.atan(s/(2*c['lens_mm']))) for s in c['sensor_mm']]
    records=[]
    for cam in rig['cameras']:
        records.append(dict(name=cam.name,location_m=list(cam.location),
            relative_hub_m=list(cam.location-Vector((0,0,g['hub_height_m']))),
            world_from_blender_camera=[list(row) for row in cam.matrix_world],
            opencv_camera_from_world=cv_from_world(cam.matrix_world.inverted()),
            K=intrinsics(hfov,vfov,w,h)))
    return dict(schema='gw184.calibration.v1',cameras=records,resolution=[w,h],
                sensor_mm=c['sensor_mm'],lens_mm=c['lens_mm'],fov_deg=[hfov,vfov],
                pixel_convention='centres at integer pixels; borders -0.5 .. size-0.5',
                model='ideal pinhole, no distortion/noise/rolling shutter',
                motion_blur=False,exposure_s=0,shutter='instantaneous synthetic sample',
                color_view='Standard, fixed exposure=0 gamma=1',
                render_engine=rig['scene'].render.engine,
                render_samples=(rig['scene'].eevee.taa_render_samples if rig['scene'].render.engine.startswith('BLENDER_EEVEE') else rig['scene'].cycles.samples),
                assumptions=c['assumptions'])
