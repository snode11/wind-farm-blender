"""Value-only tip tracking shared by backend consumers and OpenCV preview.

Indices are 0,1,2 internally. Backend blade IDs are converted at the boundary.
No pixel estimate or prediction is labeled as a physical measurement.
"""
from collections import deque
import math
import numpy as np
from .tip_geometry import finite_scalar, finite_vector, RpmAzimuthIntegrator, tip_3d_from_pixel


class TipTracker:
    def __init__(self, geom, cam, trail=120, max_pts=6000, *, max_prediction_s=.25,
                 max_pass_samples=4096, imbalance_tracker=None):
        for n in (trail, max_pts, max_pass_samples):
            if type(n) is not int or n < 1:
                raise ValueError('Buffer limits must be positive integers')
        self.geom, self.cam = geom, cam
        self.trail, self.max_pts, self.max_pass_samples = trail, max_pts, max_pass_samples
        self.max_prediction_s = finite_scalar(max_prediction_s)
        if self.max_prediction_s < 0:
            raise ValueError('Prediction duration must be nonnegative')
        self.imb = imbalance_tracker
        self.show_trails = True
        self.reset()

    def reset(self):
        self.trajs, self.trail_px, self.integ, self.last = {}, {}, {}, {}
        self.results, self.tips, self.sections, self.detections, self.errors = {}, {}, {}, {}, {}
        self._gap, self._pass_buf, self._pass_overflow = {}, {}, set()
        self._last_t, self._source, self._rpm = None, None, 0.
        if self.imb is not None:
            self.imb.reset()

    def set_trails(self, enabled):
        enabled = bool(enabled)
        if enabled != self.show_trails:
            self.trajs.clear()
            self.trail_px.clear()
        self.show_trails = enabled

    @staticmethod
    def _norm_dets(dets):
        if dets is None:
            return {}
        if isinstance(dets, dict):
            data = dets
        else:
            arr = np.asarray(dets, dtype=float)
            data = {0: arr} if arr.shape == (2,) else dict(enumerate(dets))
        out = {}
        for bid, value in data.items():
            if isinstance(bid, bool) or int(bid) != bid or int(bid) not in (0, 1, 2):
                raise ValueError('Blade IDs must be integers 0,1,2')
            if value is not None:
                out[int(bid)] = value
        return out

    def _begin(self, t, rpm, source):
        t, rpm = finite_scalar(t), finite_scalar(rpm)
        if t < 0:
            raise ValueError('Time must be nonnegative')
        if self._last_t is not None:
            if source != self._source or t < self._last_t:
                self.reset()
            elif t == self._last_t:
                return False  # camera redraw is separate from data advancement
        self._last_t, self._source, self._rpm = t, source, rpm
        self.results, self.tips, self.sections, self.detections, self.errors = {}, {}, {}, {}, {}
        return True

    def _buffers(self, bid):
        self.trajs.setdefault(bid, deque(maxlen=self.max_pts))
        self.trail_px.setdefault(bid, deque(maxlen=self.trail))
        self._pass_buf.setdefault(bid, deque(maxlen=self.max_pass_samples))
        self._gap.setdefault(bid, True)

    def _accept(self, bid, result, t, uv=None):
        self._buffers(bid)
        result.update(blade_id=bid, time_s=t, measured=False)
        result['_pose'] = (self.geom.hub.copy(), self.geom.world_rotation.copy())
        self.results[bid] = self.last[bid] = result
        self.tips[bid] = (result['P'], True)
        self._gap[bid] = False
        if self.show_trails:
            self.trajs[bid].append(result['P'].copy())
            # World positions are projected using the CURRENT camera when drawn.
            self.trail_px[bid].append(result['P'].copy())
        if uv is not None:
            self.detections[bid] = tuple(uv)
        ig = self.integ.setdefault(bid, RpmAzimuthIntegrator(self.geom))
        ig.seed(result['azimuth_deg'], t)
        if self.imb is not None:
            buf = self._pass_buf[bid]
            # A completed revolution closes a pass even if detection never drops.
            if buf and abs(t-buf[0][0]) * abs(self._rpm) >= 60:
                self._finish_pass(bid)
            if len(buf) == self.max_pass_samples:
                self._pass_overflow.add(bid)
            buf.append((t, result['azimuth_deg'], result['out_of_plane_m']))

    def _finish_pass(self, bid):
        buf = self._pass_buf[bid]
        if self.imb is not None and bid not in self._pass_overflow and len(buf) >= 5:
            ts, phis, offsets = zip(*buf)
            self.imb.feed_pass(bid, ts, phis, offsets, self._rpm)
        buf.clear()
        self._pass_overflow.discard(bid)

    def flush(self):
        for bid in self._pass_buf:
            self._finish_pass(bid)

    def update_pixels(self, dets=None, rpm=0., t=0., *, source='pixel_sphere_estimate'):
        if not self._begin(t, rpm, source):
            return self.results
        d = self._norm_dets(dets)
        for bid in sorted(set(d) | set(self.last)):
            self._buffers(bid)
            result = None
            if bid in d:
                try:
                    uv = finite_vector(d[bid], 2)
                    result = tip_3d_from_pixel(self.cam, self.geom, *uv)
                except (ValueError, TypeError):
                    result = None
                if result is None:
                    self.errors[bid] = 'invalid detection or no sphere intersection'
            if result is not None:
                result['source'] = source
                self._accept(bid, result, t, uv)
                continue
            if not self._gap[bid]:
                if self.show_trails:
                    self.trajs[bid].append(None)
                    self.trail_px[bid].append(None)
                self._gap[bid] = True
                self._finish_pass(bid)
            previous = self.last.get(bid)
            if previous is None or t-previous['time_s'] > self.max_prediction_s:
                continue
            hub, rotation = previous['_pose']
            if not (np.allclose(hub, self.geom.hub) and np.allclose(rotation, self.geom.world_rotation)):
                # Without a motion model, a changed nacelle pose invalidates
                # the old tip prediction instead of becoming false deflection.
                continue
            phase = self.integ[bid].step(t, rpm)['azimuth_deg']
            delta = math.radians(phase-previous['azimuth_deg'])
            # Positive azimuth rotates toward e_lat, i.e. around -n_axis.
            axis = -self.geom.n_axis
            rel = previous['P']-self.geom.hub
            rel = (rel*math.cos(delta) + np.cross(axis, rel)*math.sin(delta)
                   + axis*np.dot(axis, rel)*(1-math.cos(delta)))
            point = self.geom.hub + rel
            result = self.geom.decompose(point)
            result.update(P=point, source='prediction', validity='predicted', measured=False,
                          time_s=t, blade_id=bid, clearance_m=None, out_of_plane_m=None,
                          elastic_out_of_plane_m=None)
            self.results[bid] = result
            self.tips[bid] = (point, False)
        return self.results

    def update_world(self, blades, rpm=0., t=0., *, source, provenance):
        """Consume actual world coordinates, never projecting through a sphere.

        blades={0:{'P':[x,y,z], 'sections':[[x,y,z],...], 'reference': optional xyz}}
        source/provenance are required to distinguish solver data from test curves.
        """
        if not isinstance(source, str) or not source.strip() or not isinstance(provenance, dict) or not provenance:
            raise ValueError('World input requires source and provenance')
        if not self._begin(t, rpm, source):
            return self.results
        if not isinstance(blades, dict) or any(type(b) is not int or b not in (0,1,2) for b in blades):
            raise ValueError('World blade IDs must be 0,1,2')
        for bid in sorted(set(blades) | set(self.last)):
            self._buffers(bid)
            data = blades.get(bid)
            try:
                if data is None:
                    raise ValueError('missing blade')
                point = finite_vector(data['P'])
                sections = np.asarray(data['sections'], dtype=float)
                if (sections.ndim != 2 or sections.shape[1] != 3 or not 2 <= len(sections) <= 1024
                        or not np.isfinite(sections).all() or not np.allclose(sections[-1], point, atol=1e-6)):
                    raise ValueError('Sections must be finite root-to-tip XYZ ending at P')
                result = self.geom.decompose(point, data.get('reference'))
                result.update(P=point, source=source, validity='valid', provenance=dict(provenance))
                self._accept(bid, result, t)
                self.sections[bid] = sections.copy()
            except (ValueError, TypeError, KeyError) as exc:
                self.errors[bid] = str(exc)
                if not self._gap[bid] and self.show_trails:
                    self.trajs[bid].append(None)
                    self.trail_px[bid].append(None)
                self._gap[bid] = True
                self._finish_pass(bid)
        return self.results
