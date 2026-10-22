"""Exact job frame/time mapping and cancellable rigid-scene visibility scans.

Ordinary samples use a half-open interval. Blender frame coordinates are kept
separate from job indices, including their subframe component.
"""
from dataclasses import dataclass
import math


def _finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name} must be finite')
    return float(value)


@dataclass(frozen=True)
class FrameMapping:
    start_s: float
    duration_s: float
    export_fps: float
    blender_fps: float = 30.0

    def __post_init__(self):
        for name in ('start_s', 'duration_s', 'export_fps', 'blender_fps'):
            _finite(getattr(self, name), name)
        if self.start_s < 0 or min(self.duration_s, self.export_fps, self.blender_fps) <= 0:
            raise ValueError('Invalid frame timing')
        if abs(self.duration_s * self.export_fps - round(self.duration_s * self.export_fps)) > 1e-7:
            raise ValueError('Duration must contain an integer number of output frames')
        if self.frame_count < 1:
            raise ValueError('The job must have at least one output frame')

    @property
    def frame_count(self):
        return round(self.duration_s * self.export_fps)

    def _row(self, time_s, index, role):
        coordinate = 1.0 + self.blender_fps * time_s
        nearest = round(coordinate)
        if abs(coordinate - nearest) <= 1e-9:
            coordinate = float(nearest)
        integer = math.floor(coordinate)
        return dict(frame_index=index, time_s=float(time_s),
                    relative_time_s=(None if index is None else index / self.export_fps),
                    blender_frame_coordinate=coordinate, blender_frame=integer,
                    blender_subframe=coordinate - integer, sample_role=role,
                    included_in_video=index is not None)

    def sample(self, index):
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < self.frame_count:
            raise ValueError('Output frame index is outside this job')
        return self._row(self.start_s + index / self.export_fps, index, 'ordinary')

    def current(self, time_s):
        time_s = _finite(time_s, 'time_s')
        if time_s < 0:
            raise ValueError('time_s must be nonnegative')
        index = (time_s - self.start_s) * self.export_fps
        nearest = round(index)
        if abs(index - nearest) <= 1e-7 and 0 <= nearest < self.frame_count:
            return self.sample(nearest)
        endpoint = abs(time_s - (self.start_s + self.duration_s)) <= 1e-9
        return self._row(time_s, None, 'endpoint_check' if endpoint else 'current_check')

    def to_dict(self):
        return dict(schema='wfrl.blade_defects.frame_mapping.v1', start_s=self.start_s,
                    duration_s=self.duration_s, export_fps=self.export_fps,
                    frame_count=self.frame_count, export_index_start=0,
                    blender_fps=self.blender_fps, blender_start_frame=1,
                    blender_start_time_s=0.0, ordinary_interval='[start_s,start_s+duration_s)',
                    first_sample=self.sample(0), last_sample=self.sample(self.frame_count - 1),
                    endpoint=self.current(self.start_s + self.duration_s))


def summarize_intervals(records, mapping, complete=True):
    """Group only tested adjacent output indices, never interpolate between them."""
    groups = {}
    for row in records:
        if row.get('frame_index') is not None:
            key = (row['defect_id'], row['revision'], row['support_id'], row['camera_id'])
            groups.setdefault(key, {})[row['frame_index']] = row
    summaries = []
    for key, indexed in sorted(groups.items()):
        rows = [indexed[i] for i in sorted(indexed)]
        intervals, active = [], []

        def flush():
            if not active:
                return
            best = max(active, key=lambda r: (r.get('visible_area_px2') or 0, -r['frame_index']))
            intervals.append(dict(first_frame=active[0]['frame_index'], last_frame=active[-1]['frame_index'],
                                  first_time_s=active[0]['time_s'], last_time_s=active[-1]['time_s'],
                                  sample_interval_s=1.0 / mapping.export_fps,
                                  full_visible_frames=sum(r['status'] == 'VISIBLE' for r in active),
                                  partial_visible_frames=sum(r['status'] == 'PARTIAL' for r in active),
                                  candidate_best_frame=best['frame_index'], candidate_best_time_s=best['time_s'],
                                  selection_metric='maximum estimated visible_area_px2',
                                  candidate_role='geometric_candidate; appearance not reviewed'))
            active.clear()

        previous = None
        for row in rows:
            if previous is not None and row['frame_index'] != previous + 1:
                flush()
            if row['status'] in ('VISIBLE', 'PARTIAL'):
                active.append(row)
            else:
                flush()
            previous = row['frame_index']
        flush()
        summaries.append(dict(defect_id=key[0], revision=key[1], support_id=key[2], camera_id=key[3],
                              status=('COMPLETE' if complete else 'INCOMPLETE'),
                              evaluated_frames=len(rows), visible_intervals=intervals,
                              no_visible_frames=not intervals,
                              limitations='Intervals describe discrete evaluated frames only; no continuous visibility or discernibility claim.'))
    return summaries


def scan(rig, supports, mapping, run_id='', state_version=0, cancel=None,
         progress=None, sampling_step_px=0.75, start_index=0, is_current=None):
    """Scan ordinary job samples; resume indices retain the original mapping.

    ``cancel()`` and ``is_current(version)`` are checked before each frame and
    during the ray quadrature. A partial current frame is discarded. Both the
    exact actual pose and Blender frame are restored even on error/cancellation.
    """
    from .defect_visibility import analyze, AnalysisCancelled
    from .model import boundaries
    if not isinstance(mapping, FrameMapping):
        raise TypeError('mapping must be a FrameMapping')
    if mapping.start_s + mapping.duration_s > boundaries(rig['motion'])[-1] + 1e-9:
        raise ValueError('Scan interval exceeds the actual motion timeline')
    if not isinstance(start_index, int) or isinstance(start_index, bool) or not 0 <= start_index < mapping.frame_count:
        raise ValueError('Invalid resume index')
    scene = rig['scene']
    frame, subframe = scene.frame_current, scene.frame_subframe
    objects = [rig['rotor'], *rig['pitch_roots']]
    transforms = [obj.matrix_basis.copy() for obj in objects]
    time_keys = {key: scene.get(key) for key in ('gw184_time_s', 'gw184_defect_time_s', 'defect_time_s')}
    rows, done, cancelled, stale = [], [], False, False

    def stop():
        nonlocal stale
        stale = bool(is_current and not is_current(state_version))
        return stale or bool(cancel and cancel())

    try:
        for index in range(start_index, mapping.frame_count):
            if stop():
                cancelled = True
                break
            sample = mapping.sample(index)
            try:
                frame_rows = analyze(rig, supports, sample['time_s'], index, run_id,
                                     state_version, sampling_step_px=sampling_step_px, cancel=stop)
            except AnalysisCancelled:
                cancelled = True
                break
            for row in frame_rows:
                row['sample'] = sample
            rows.extend(frame_rows)
            done.append(index)
            if progress:
                progress(index + 1, mapping.frame_count)
    finally:
        scene.frame_set(frame, subframe=subframe)
        for obj, matrix in zip(objects, transforms):
            obj.matrix_basis = matrix
        for key, value in time_keys.items():
            if value is None:
                if key in scene:
                    del scene[key]
            else:
                scene[key] = value
        import bpy
        bpy.context.view_layer.update()
    complete = not cancelled and len(done) == mapping.frame_count - start_index
    return dict(schema='wfrl.blade_defects.visibility_scan.v1', run_id=run_id,
                state_version=state_version, mode='G', frame_mapping=mapping.to_dict(),
                completed=complete, cancelled=cancelled, stale_state=stale,
                coverage='all_target_frames' if start_index == 0 and complete else 'partial_target_range',
                requested_start_index=start_index, completed_frames=len(done),
                completed_range=([done[0], done[-1]] if done else None),
                next_frame_index=(None if complete else (done[-1] + 1 if done else start_index)),
                records=rows, intervals=summarize_intervals(rows, mapping, complete and start_index == 0),
                limitations='No images rendered. Discrete geometry samples; appearance remains NOT_REVIEWED.')
