"""Farm replay scene bindings, mesh ownership and handler lifecycle."""
import hashlib
import math

import numpy as np

from . import farm_flex as _farm
from . import performance


def active_for(scene):
    """Return the runtime bound to this scene, never another window's scene."""
    if scene is None:
        return None
    try:
        active = _farm._ACTIVES.get(scene.as_pointer())
        if active is not None and active.enabled and active.scene == scene:
            # Undo can preserve a pointer value while replacing the RNA wrapper.
            active.scene.as_pointer()
            return active
    except ReferenceError:
        pass
    return None


def ready_for(scene):
    """Return the scene's runtime when its replay reader is bound, else None."""
    from . import clearance_replay
    active = _farm.active_for(scene)
    if active is not None and clearance_replay.reader_for(scene) in active.readers.values():
        return active
    return None


def is_active(scene):
    return _farm.ready_for(scene) is not None


class _MeshBindings:
    """Own one replay binding's new meshes and reversible object assignments."""
    _OWNER = 'wfrl_farm_flex_owned'

    def __init__(self):
        self.created = []
        self.replaced = []

    def own(self, mesh):
        mesh[self._OWNER] = True
        self.created.append(mesh)
        return mesh

    def replace(self, obj, mesh):
        previous = obj.data
        obj.data = mesh
        self.replaced.append((obj, previous, mesh))

    def follow(self, obj):
        """Restore a managed sampling object's shared reference on rollback."""
        for target, previous, mesh in self.replaced:
            if obj != target and obj.data == mesh:
                self.replaced.append((obj, previous, mesh))
                return

    @classmethod
    def _release(cls, mesh):
        import bpy
        try:
            if (mesh.get(cls._OWNER) and mesh.library is None and mesh.users == 0
                    and not mesh.use_fake_user and mesh.shape_keys is None):
                bpy.data.meshes.remove(mesh)
        except ReferenceError:
            pass

    def commit(self):
        for _obj, previous, _mesh in self.replaced:
            self._release(previous)
        for mesh in self.created:
            self._release(mesh)
        self.created.clear()
        self.replaced.clear()

    def rollback(self):
        for obj, previous, mesh in reversed(self.replaced):
            try:
                if obj.data == mesh:
                    obj.data = previous
            except ReferenceError:
                pass
        for mesh in self.created:
            self._release(mesh)
        self.created.clear()
        self.replaced.clear()


class _SceneBinding:
    def __init__(self,scene,path,*,_package=None):
        self._meshes = _farm._MeshBindings()
        self.comparison = None
        self.export_turbines = None
        self.frame_error = ''
        try:
            self._bind(scene, path, _package=_package)
        except Exception:
            try:
                if self.comparison is not None:
                    self.comparison.close()
            finally:
                self._meshes.rollback()
            raise
        self._meshes.commit()

    def _bind(self,scene,path,*,_package=None):
        import bpy
        from .turbine_geometry import geometry_data,blade_mesh,MODELS,DEFAULT_MODEL
        from .materials import get_material,ensure_blade_tip_markings
        from . import clearance_replay
        self._world_matrices = None
        self._update_geometry = None
        self._deformation_batches = None
        package = _farm._load_package(path) if _package is None else _package
        self.manifest,self.times,self.transforms,self.poses,self.readers=package.playback
        result_path = package.result_path
        path, overlay = package.source_path, package.overlay
        self.tower_motion = package.tower_motion
        self.reference = package.reference
        self.towers = []
        self.fittings = []
        from .deflection import ComparisonView
        comparison_data = package.comparison
        verified_source = package.verified_source
        self.telemetry = _farm._read_telemetry(path, self.manifest, self.times, self.readers,
                                         _source=verified_source)
        from .imbalance import load_for_source
        self.imbalance, self.imbalance_error, self.imbalance_plots = None, '', {}
        try:
            self.imbalance = load_for_source(path, _source=verified_source)
        except (OSError, ValueError) as error:
            self.imbalance_error = str(error)
        self.scene=scene
        self.blades=[]
        self.groups={}
        self.cache={}
        self.enabled=True
        self.reference_matrices = {}
        self.visible_turbines=set(range(len(self.readers)))
        for tid in self.readers:
            scene.objects[f'WFRL.Turbine.{tid}.YawRoot'].rotation_euler=(0,0,0)
            scene.objects[f'WFRL.Turbine.{tid}.YawRoot'].location=(0,0,geometry_data()['scalars']['TowerHt'])
            scene.objects[f'WFRL.Turbine.{tid}.Rotor'].rotation_euler.x=0
            for b in (1,2,3):scene.objects[f'WFRL.Turbine.{tid}.Blade{b}'].rotation_euler.z=0
        phase_started = performance.begin()
        _farm._update_scene_transforms(scene)
        performance.end("flex.depsgraph_update", phase_started)
        self.model = scene.get('wfrl_blade_model', DEFAULT_MODEL)
        if self.model not in MODELS:
            self.model = DEFAULT_MODEL
        model_geometry = geometry_data(self.model)
        spans=np.array([s[0]+model_geometry['scalars']['HubRad'] for s in model_geometry['blade_stations']])
        # Retain the same 19-station loft, with fewer presentation samples.
        # Physical transforms, amplitude and the stable tip apex are unchanged.
        vertices,faces=blade_mesh(subdiv=4,ring_points=48,model=self.model)
        # Preserve the cosmetic apex at -1 for existing coloured trails. The
        # extra loose vertex follows exactly the same deformation as the mesh.
        if comparison_data is not None:
            vertices = list(vertices)
            apex = len(vertices) - 1
            vertices.insert(apex, (0., 0., comparison_data['scalars']['TipRad']))
            faces = [tuple(v + 1 if v == apex else v for v in face) for face in faces]
        if self.reference is not None:
            from .prebend import bend_vertices
            vertices = bend_vertices(vertices, self.reference).tolist()
        display_mesh=self._meshes.own(bpy.data.meshes.new('WFRL.FarmFlex.SourceLoft'))
        display_mesh.from_pydata(vertices,[],faces)
        display_mesh.materials.append(get_material('blade'))
        for polygon in display_mesh.polygons:polygon.use_smooth=True
        display_mesh.update()
        for turbine,tid in enumerate(self.readers):
            offset=np.array(self.manifest['layout_m'][turbine])
            for b in (1,2,3):
                obj=scene.objects[f'WFRL.Turbine.{tid}.Blade{b}']
                self._meshes.replace(obj, self._meshes.own(display_mesh.copy()))
                ensure_blade_tip_markings([obj])
                # The source loft already has smooth normals. Avoid rebuilding
                # a 12 mm cosmetic bevel on nine moving meshes every frame.
                for modifier in obj.modifiers:
                    if modifier.type=='BEVEL':
                        modifier.show_viewport=False
                        modifier.show_render=False
                rest=np.empty(len(obj.data.vertices)*3,np.float32)
                obj.data.vertices.foreach_get('co',rest)
                rest=rest.reshape(-1,3).copy()
                matrix=np.array(obj.matrix_world)
                self.reference_matrices[obj.name] = matrix.copy()
                world=(rest@matrix[:3,:3].T+matrix[:3,3]-offset).astype(np.float32)
                index=np.clip(np.searchsorted(spans,rest[:,2],side='right')-1,0,len(spans)-2)
                weight=np.clip((rest[:,2]-spans[index])/(spans[index+1]-spans[index]),0,1)[:,None]
                self.blades.append((obj,rest,world,index,weight.astype(np.float32),turbine,b-1,offset))
                self.groups[obj.name]=[(s,np.flatnonzero(index==s).copy()) for s in np.unique(index)]
                _farm._readonly_arrays((self.blades[-1][1:], self.groups[obj.name]))
        if self.tower_motion is not None:
            from .turbine_geometry import tower_mesh
            tower_vertices, tower_faces = tower_mesh()
            heights = self.tower_motion['heights']
            rest = np.asarray(tower_vertices, dtype=np.float32)
            index = np.clip(np.searchsorted(heights,rest[:,2],side='right')-1,0,len(heights)-2)
            weight = ((rest[:,2]-heights[index])/(heights[index+1]-heights[index]))[:,None].copy()
            for k,tid in enumerate(self.readers):
                prefix = f'WFRL.Turbine.{tid}'
                obj = scene.objects[prefix+'.Tower']
                # Rebuild from rest geometry so save/reload never compounds bending.
                mesh = self._meshes.own(bpy.data.meshes.new(prefix+'.FlexibleTower'))
                mesh.from_pydata(tower_vertices,[],tower_faces)
                mesh.materials.append(get_material('tower'))
                for polygon in mesh.polygons: polygon.use_smooth=True
                self._meshes.replace(obj, mesh)
                self.towers.append((obj,rest.copy(),index,weight,k))
                _farm._readonly_arrays(self.towers[-1][1:])
                # Primitive collars store height in object.location; decorative
                # rings store it in their vertices. Apply the same support
                # transform without adding the baked height a second time.
                for suffix,z,origin_z in (('.YawBearing',87.6,87.6),
                                          ('.TowerCollar87',87.52,87.52),
                                          ('.TowerCollar0',.28,.28),
                                          ('.YawSeal',87.6,0.),
                                          ('.TowerWeld28',28.,0.),
                                          ('.TowerWeld56',56.,0.)):
                    fitting=scene.objects.get(prefix+suffix)
                    if fitting:
                        if suffix == '.YawSeal':
                            from .mechanical_details import recess_yaw_seal
                            recess_yaw_seal(fitting, geometry_data()['scalars']['TowerHt'])
                        fitting.rotation_euler=(0,0,0)
                        rest_location=np.array([0.,0.,origin_z])
                        fitting.location=rest_location
                        self.fittings.append((fitting,k,z,rest_location))
        scene['wfrl_scene_kind']='clearance_replay'
        scene['wfrl_clearance_turbine']='T1'
        scene['wfrl_clearance_demo']='near_tower'
        scene['wfrl_clearance_timebase_fps']=60.
        scene['wfrl_clearance_status']='REVIEW_ONLY'
        scene['wfrl_flex_provisional']=True
        scene['wfrl_flex_active']=True
        scene['wfrl_flex_provenance']='三机 FAST.Farm · MAPPO 偏航 · 保守转速目标/变桨 · 随机阵风'
        if self.tower_motion is not None:
            scene['wfrl_flex_provenance'] += ' · 真实塔架柔性'
        if self.reference is not None:
            scene['wfrl_flex_provenance'] = self.manifest['provenance_label']
        scene['wfrl_tip_reference_note']=scene['wfrl_flex_provenance']
        scene['wfrl_farm_flex_path']=str(result_path)
        scene['wfrl_farm_manifest_sha256']=hashlib.sha256((result_path/'manifest.json').read_bytes()).hexdigest()
        from .clearance_visual import ensure_radar
        if overlay is not None:
            config = overlay['config']
            directions = [(-math.sin(math.radians(a)), 0., -math.cos(math.radians(a)))
                          for a in config['angles_deg']]
            origins = [[o[0], o[1], o[2]-geometry_data()['scalars']['TowerHt']]
                       for o in config['origins_m']]
            for tid in self.readers:
                ensure_radar(scene, tid, origins[0], directions, beam_origins=origins)
        else:
            calibration = self.manifest['calibration']
            origin = list(calibration['origin_m'])
            origin[2] -= geometry_data()['scalars']['TowerHt']
            for tid in self.readers:
                ensure_radar(scene, tid, origin, calibration['beam_directions'])
        scene.render.fps=60
        scene.render.fps_base=1
        scene.frame_start=1
        scene.frame_end=1+round((self.times[-1]-self.times[0])*60)
        scene.sync_mode='FRAME_DROP'
        scene.use_preview_range=False
        self.last_telemetry_frame=None
        scene['wfrl_fidelity']='FAST.Farm / REVIEW_ONLY'
        scene['wfrl_backend']='OpenFAST interface probe' if self.manifest.get('interface_only') else 'MAPPO recorded results'
        for key in ('wfrl_power_mw', 'wfrl_demo_phase', 'wfrl_demo_time_s'):
            if key in scene: del scene[key]
        scene['wfrl_power_available']=False
        clearance_replay._READERS[scene.as_pointer()]=self.readers['T1']
        if comparison_data is not None:
            self.comparison = ComparisonView(self, comparison_data, vertices, faces)
        from . import split_turbine_context
        split_turbine_context.rebind_meshes(scene, self._meshes)
        # Rebuild cosmetic anchors only after structural bindings succeed,
        # while the replacement loft is still at rest. Earlier binding failures
        # must leave the original attachment and sampling mesh intact.
        from . import repair_marks
        try:
            repair_marks.ensure(scene, force=True)
        finally:
            blade = scene.objects.get(repair_marks.TARGET_NAME)
            if blade is not None:
                for obj in scene.objects:
                    if obj.type == 'MESH' and repair_marks._owned(obj, blade):
                        self._meshes.follow(obj)


def update(scene,depsgraph=None):
    active = _farm.active_for(scene)
    if active is not None and not active.frame_error:
        active.update(scene)


def detach(scene=None, *, restore=True):
    """Release handlers and mesh references before scene destruction or unload."""
    import bpy
    from . import split_turbine_context
    split_turbine_context.clear_runtime(scene)
    if scene is None:
        entries = list(_farm._ACTIVES.items())
    else:
        try:
            key = scene.as_pointer()
            entries = [(key, _farm._ACTIVES[key])] if key in _farm._ACTIVES else []
        except ReferenceError:
            entries = [(key, active) for key, active in _farm._ACTIVES.items()
                       if active.scene is scene]
    actives = [active for _key, active in entries]
    from .nrel_defects import editor
    editor.detach(scene, restore=restore)
    # End camera drafts before invalidating their replay and parent references.
    import sys
    custom = sys.modules.get(__package__ + '.panels.custom_cameras')
    if custom is not None and (scene is None or
            getattr(getattr(custom, '_ACTIVE', None), 'scene', None) == scene):
        custom.cancel_on_load()
    from . import clearance_replay
    for key, active in entries:
        _farm._ACTIVES.pop(key, None)
        clearance_replay._READERS.pop(key, None)
        active.enabled = False
        if active.comparison is not None:
            active.comparison.close()
        if restore:
            for obj, rest, *_ in active.blades:
                try:
                    obj.data.vertices.foreach_set('co', rest.ravel())
                    obj.data.update()
                except ReferenceError:
                    pass
        try:
            active.scene['wfrl_flex_active'] = False
        except ReferenceError:
            pass
        if restore:
            active.restore_support()
        active.cache.clear()
        active._world_matrices = None
        active._update_geometry = None
        active._deformation_batches = None
    if _farm._ACTIVE in actives:
        _farm._ACTIVE = next(iter(_farm._ACTIVES.values()), None)
    if not _farm._ACTIVES:
        if _farm.update in bpy.app.handlers.frame_change_post:
            bpy.app.handlers.frame_change_post.remove(_farm.update)
        if _farm.on_load_pre in bpy.app.handlers.load_pre:
            bpy.app.handlers.load_pre.remove(_farm.on_load_pre)


def on_load_pre(_unused):
    # Detach removes this handler; clear trails here before handler-list mutation
    # can skip the following load_pre callback.
    from . import tip_tracking
    _farm.clear_package_cache()
    tip_tracking.on_load_pre(_unused)
    _farm.detach(restore=False)


def attach(scene,path,*,_package=None):
    import bpy
    from . import tip_tracking
    replacing = False
    try:
        # Validate before releasing the old scene runtime. In particular a
        # FULL_COPY scene contains renamed objects and must not detach an
        # already-restored scene when those required bindings are absent.
        package = _farm._load_package(path) if _package is None else _package
        if _package is not None:
            _farm.validate_candidate(path, package)
        manifest = package.manifest
        required = [f'WFRL.Turbine.{tid}.{suffix}'
                    for tid in manifest['turbine_ids']
                    for suffix in ('YawRoot', 'Rotor', 'Tower', 'Blade1', 'Blade2', 'Blade3')]
        missing = [name for name in required if scene.objects.get(name) is None]
        if missing:
            raise ValueError('Scene lacks required FarmFlex objects: ' + ', '.join(missing))
        # Linked scenes share the same object transforms/meshes; two separate
        # timeline clocks cannot safely drive them. Keep the first owner ready.
        for other in list(_farm._ACTIVES.values()):
            try:
                other.scene.as_pointer()
            except ReferenceError:
                _farm.detach(other.scene, restore=False)
                continue
            if other.scene != scene and any(obj in scene.objects.values() for obj, *_ in other.blades):
                raise ValueError('FarmFlex objects are already driven by scene: ' + other.scene.name)
        replacing = True
        _farm.detach(scene, restore=False)
        from . import charts
        charts.clear(scene=scene)
        active=_farm.FarmFlex(scene,path,_package=package)
        _farm._ACTIVES[scene.as_pointer()] = active
        _farm._ACTIVE = active
    except Exception:
        from . import clearance_replay
        if replacing:
            _farm.detach(scene, restore=False)
        if replacing or _farm.active_for(scene) is None:
            scene['wfrl_flex_active'] = False
            clearance_replay.clear(scene,'三机形变包未就绪：加载失败')
        raise
    # Tip sampling must follow mesh deformation at the same simulation time.
    if _farm.update in bpy.app.handlers.frame_change_post:bpy.app.handlers.frame_change_post.remove(_farm.update)
    if tip_tracking.update in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(tip_tracking.update)
    bpy.app.handlers.frame_change_post.append(_farm.update)
    from bpy.app.handlers import persistent
    persistent(_farm.on_load_pre)
    if _farm.on_load_pre not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_farm.on_load_pre)
    tip_tracking.register()
    from .nrel_defects import editor
    editor.restore_saved(scene)
    # Freeze editor drafts after the replay handler has sampled the new frame.
    if editor.frame_changed in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(editor.frame_changed)
    bpy.app.handlers.frame_change_post.append(editor.frame_changed)
    scene.frame_set(1)
    return active
