"""Same-frame analysis contexts, reference curves and blade clearance inference."""
import time
import numpy as np
from .core import schema
from . import clearance, display


def pair_context(self, frame):
    self.context = self.analysis_result = self.side_query = None
    self.blade_result = self.blade_key = None
    self.blade_retry_at = 0.
    self.blade_error = ''
    self.reference_curves = ()
    self.reference_key = None
    self.analysis_key = None
    self.analysis_retry_key = None
    self.analysis_retry_at = 0.
    self.analysis_error = ''
    self.reference_error = ''
    if frame is None or self.mode != 'SIMULATION':
        self.analysis_error = '无可靠同帧分析上下文'
        return
    identity = (self.generation, self.source_session, frame.identity, frame.source_kind,
        frame.time.clock_domain, frame.time.source_time_s, self.frame_index,
        self.sampler.source_id, self.sampler.geometry_id, self.sampler.calibration_id)
    try:
        self.context = self.sampler.analysis_context(frame, identity)
        self.sampler.latest_sample = None
    except Exception as exc:
        self.analysis_error = str(exc)


def prepare_reference(self, requested):
    if not requested or self.context is None:
        self.reference_error = ''
        self.reference_curves = ()
        self.reference_key = None
        return
    if self.reference_key == self.context.identity:
        return
    self.reference_key = self.context.identity
    self.reference_curves = ()
    try:
        from .core import analysis
        from .. import farm_flex, prebend
        from ..deflection import interpolated_beamdyn_root_frame
        from ..turbine_geometry import geometry_data
        sampler, flex = self.sampler, self.sampler.flex
        sampler.validate()
        source_key = (id(flex), schema.digest(flex.reference), sampler.source_id,
                      sampler.geometry_id, str(farm_flex.saved_package(self.scene)))
        if source_key != self.reference_source_key:
            source, _ = farm_flex.dual_beam_api().resolve_package(farm_flex.saved_package(self.scene))
            reference = prebend.read_reference(source, flex.manifest)
            if reference is None or schema.digest(reference) != schema.digest(flex.reference):
                self.reference_source_key = self.reference_source = None
                raise ValueError('当前来源无核对通过的独立零载荷参考')
            from copy import deepcopy
            self.reference_source = deepcopy(reference)
            self.reference_source_key = source_key
        reference = self.reference_source
        t = self.frame.time.source_time_s
        i = int(np.clip(np.searchsorted(flex.times,t,side='right')-1,0,len(flex.times)-2))
        alpha = (t-flex.times[i])/(flex.times[i+1]-flex.times[i])
        curve = np.array(reference['curve'],float)[:,:3]
        curve[:,2] += geometry_data()['scalars']['HubRad']
        curves=[]
        for b in range(3):
            hub, axes = interpolated_beamdyn_root_frame(geometry_data()['scalars'],b+1,
                flex.transforms[i,sampler.k,b,0],flex.transforms[i+1,sampler.k,b,0],alpha)
            curves.append(analysis.readonly(curve @ axes.T+hub+sampler.root_world[:3,3]))
        self.reference_curves = tuple(curves)
        self.reference_error = '独立零载荷49点参考曲线，非观测'
    except Exception as exc:
        self.reference_error = str(exc)


def derive_analysis(self, roi):
    if self.context is None:
        return
    key = self.context.identity, tuple(map(tuple, roi))
    if key == self.analysis_key:
        return
    if (key == self.analysis_retry_key
            and time.monotonic() < self.analysis_retry_at):
        return
    self.analysis_key = None
    self.analysis_result = None
    try:
        from .scene import SideWallQuery
        if self.side_query is None:
            self.side_query = SideWallQuery(self.context)
        self.analysis_result = self.side_query.query(self.frame.xyz, roi, frame_key=self.frame.identity)
        self.analysis_key = key
        self.analysis_retry_key = None
        self.analysis_error = ''
    except Exception as exc:
        self.side_query = None
        self.analysis_retry_key = key
        self.analysis_retry_at = time.monotonic() + .25
        self.analysis_error = str(exc)


def _reset_blades(self):
    self.blade_result = self.blade_key = None
    self.blade_retry_at = 0.
    self.blade_error = ''
    self.blade_tracker.reset()
    if getattr(self, 'clearance_tracker', None) is not None:
        self.clearance_tracker.reset()


def derive_blades(self):
    """Optional inference errors leave the original observation and recording intact."""
    if not getattr(self.scene, 'wfrl_x100_blade_tracking', False):
        self._reset_blades()
        return
    if self.frame is not None and display.observation_mode(self.frame) == 'DETECTIONS':
        self.derive_clearance()
        return
    if self.mode != 'SIMULATION' or self.context is None or self.frame is None or not self.display_valid:
        self._reset_blades()
        self.blade_error = '需要同帧塔面、根部姿态和已对齐点云'
        return
    if self.blade_key == self.context.identity or time.monotonic() < self.blade_retry_at:
        return
    self.blade_result = None
    try:
        from .core import analysis
        from .scene import SideWallQuery
        if self.side_query is None:
            self.side_query = SideWallQuery(self.context)
        background = self.side_query.query(self.frame.xyz, ((-1e6,)*3, (1e6,)*3),
                                           frame_key=self.frame.identity)
        world = analysis.transform(self.frame.xyz, self.context.world_from_sensor)
        priors = self.sampler.blade_priors(self.frame.time.source_time_s)
        result = self.blade_tracker.update(world, priors, background['distance'],
            time_s=self.frame.time.source_time_s,
            identity=(self.generation, self.source_session, self.sampler.calibration_id),
            frame_key=self.frame.identity)
        for row in result['blades']:
            row['estimated_clearance_m'] = None
            if row['tip_world'] is not None:
                point = analysis.local([row['tip_world']], self.context.world_from_sensor)
                distance = self.side_query.query(point, ((-1e6,)*3, (1e6,)*3))['distance'][0]
                if np.isfinite(distance):
                    row['estimated_clearance_m'] = float(distance)
        self.blade_result, self.blade_error = result, ''
        self.blade_key = self.context.identity
        self.blade_retry_at = 0.
    except Exception as exc:
        self.blade_tracker.reset()
        self.side_query = None
        self.blade_retry_at = time.monotonic() + .25
        self.blade_error = str(exc)


def _clearance_frame_key(self, frame):
    return (getattr(self, 'generation', None), frame.identity, frame.source_kind, frame.time.clock_domain,
            frame.time.source_time_s, self.frame_index)


def derive_clearance(self):
    """Infer from allowed same-frame inputs, or display a verified recorded result."""
    frame = self.frame
    key = self._clearance_frame_key(frame)
    if not self.display_valid:
        self._reset_blades()
        self.blade_error = '当前检测帧无效'
        return
    if self.blade_key == key or time.monotonic() < self.blade_retry_at:
        return
    self.blade_result = None
    try:
        if self.mode == 'REPLAY' and self.replay is not None and self.frame_index is not None:
            self.blade_result, self.blade_error = clearance.read_recorded(self.replay, self.frame_index, frame)
        elif self.mode == 'SIMULATION' and self.context is not None:
            from .core import analysis
            from .scene import SideWallQuery
            source = (id(self.sampler.flex), self.sampler.geometry_id)
            if getattr(self, 'clearance_source', None) != source:
                self.clearance_tracker = clearance.estimator.ClearanceTracker(clearance.unloaded_shapes(self.sampler))
                self.clearance_source = source
            if self.side_query is None:
                self.side_query = SideWallQuery(self.context)
            def tower_query(world):
                result = self.side_query.query(analysis.local(world, self.context.world_from_sensor),
                                              ((-1e6,)*3, (1e6,)*3))
                return result['distance'], result['nearest']
            result = self.clearance_tracker.update(
                analysis.transform(frame.xyz, self.context.world_from_sensor), frame.radial_velocity_m_s,
                self.sampler.blade_priors(frame.time.source_time_s), self.context.vertices, self.context.triangles,
                time_s=frame.time.source_time_s,
                identity=(self.generation, frame.identity[:3], frame.source_kind, frame.time.clock_domain,
                          self.sampler.source_id, self.sampler.geometry_id, self.sampler.calibration_id),
                frame_key=frame.identity, tower_query=tower_query, **clearance.observation_inputs(frame))
            self.blade_result, self.blade_error = clearance.display_result(result), ''
        else:
            self._reset_blades()
            self.blade_error = '缺少同帧塔面、根部姿态与未加载叶片形状先验'
        self.blade_key, self.blade_retry_at = key, 0.
    except Exception as exc:
        if getattr(self, 'clearance_tracker', None) is not None:
            self.clearance_tracker.reset()
        self.side_query = None
        self.blade_retry_at = time.monotonic() + .25
        self.blade_error = str(exc)


def classification(self):
    result = getattr(self, 'blade_result', None)
    return (result['labels'] if self.frame is not None and self.display_valid
            and display.observation_mode(self.frame) == 'DETECTIONS' and result is not None else None)
