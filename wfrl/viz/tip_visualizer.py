"""Standalone tip preview. Geometry/tracking live in dependency-light modules.

python -m wfrl.viz.tip_visualizer --fastfarm-package results/lidar/packages/normal-v1.1
python -m wfrl.viz.tip_visualizer --video camera.mp4 --dets tips.csv
Without a data source the preview explicitly labels its illustrative synthetic input.
"""
import argparse
import csv
import math
import time
from pathlib import Path
import numpy as np
from .tip_geometry import TurbineGeometry, CameraModel, finite_scalar, finite_vector, visible_arc
from .tip_tracking import TipTracker
from .tip_dependencies import opencv

class _LazyCV:
    def __getattr__(self, key):
        return getattr(opencv(), key)
cv2 = _LazyCV()
# BGR: orange, blue, red. Green is reserved for the existing wind overlay.
BLADE_COLORS = [(40, 150, 255), (255, 150, 50), (65, 70, 240)]
DIM = .38

def _dim(c, k=DIM):
    return tuple(int(x*k) for x in c)

class Scene3D:
    def __init__(self, geom, w=640, h=392, dist=None, az_deg=62.0, el_deg=16.0):
        self.geom = geom
        self.w, self.h = w, h
        span = geom.H + geom.R
        vfov = math.radians(42.0 * h / w)
        self.dist = dist or (span / (2.0 * math.tan(vfov / 2.0)) * 1.3)
        self.az = math.radians(az_deg)
        self.el = math.radians(el_deg)
        self.target = geom.origin + np.array([0.0, 0.0, span * 0.5], float)
        self._rebuild()

    def _rebuild(self):
        ca, sa, ce, se = (math.cos(self.az), math.sin(self.az),
                          math.cos(self.el), math.sin(self.el))
        pos = self.target + self.dist * np.array([ce * ca, ce * sa, se])
        self.cam = CameraModel.look_at(self.w, self.h, 42.0, 42.0 * self.h / self.w,
                                       pos, self.target, np.array([-sa, ca, 0.0]))

    def orbit(self, d_az_deg=0.0, d_el_deg=0.0):
        self.az += math.radians(d_az_deg)
        self.el = max(math.radians(-80.0),
                      min(math.radians(80.0), self.el + math.radians(d_el_deg)))
        self._rebuild()

    def p(self, P):
        u, v, _ = self.cam.project(P)
        if u is None or abs(u) > 1e4 or abs(v) > 1e4:
            return None
        return (int(round(u)), int(round(v)))

    def line(self, img, A, B, color, thick=1):
        a, b = self.p(A), self.p(B)
        if a and b:
            cv2.line(img, a, b, color, thick, cv2.LINE_AA)

    def polyline(self, img, pts, color, thick=1):
        """pts 中的 None 表示断点：叶尖出视野造成的间断不连线。"""
        prev = None
        for P in pts:
            if P is None:
                prev = None
                continue
            cur = self.p(P)
            if prev and cur:
                cv2.line(img, prev, cur, color, thick, cv2.LINE_AA)
            prev = cur

    # ── 静态机组：塔筒 + 机舱 + 旋转圆 ──
    def _draw_turbine(self, img, azim0=None):
        g = self.geom
        for c in range(-100, 101, 25):                       # 地面网格
            self.line(img, [c, -100, 0], [c, 100, 0], (52, 52, 52))
            self.line(img, [-100, c, 0], [100, c, 0], (52, 52, 52))

        def tower_line(_img, A, B, color, thick=1):
            self.line(img, g.origin+np.asarray(A), g.origin+np.asarray(B), color, thick)
        def tower_polyline(_img, points, color, thick=1):
            self.polyline(img, [g.origin+np.asarray(p) for p in points], color, thick)
        # 塔筒：锥形筒(两条边 + 若干圈)
        rad = lambda z: g.tower_radius(z + g.origin[2])
        for zz in np.linspace(0, g.tower_height, 9):
            tower_polyline(img, [[rad(zz) * math.cos(a), rad(zz) * math.sin(a), zz]
                                for a in np.linspace(0, 2 * math.pi, 25)],
                          (128, 128, 128), 1)
        for sgn in (-1, 1):
            for ax in (0, 1):
                tower_polyline(img, [[sgn * rad(z) if ax else 0.0,
                                     0.0 if ax else sgn * rad(z), z]
                                    for z in np.linspace(0, g.tower_height, 30)],
                              (205, 205, 205), 2)
        tower_polyline(img, [[g.r_tower * math.cos(a), g.r_tower * math.sin(a), 0.0]
                            for a in np.linspace(0, 2 * math.pi, 41)],
                      (120, 200, 255), 2)                    # 塔筒底面圆 = 世界系原点所在

        # 机舱：沿 −n̂ 从轮毂往下风侧伸出的短方箱
        back = g.hub - g.n_axis * 9.0
        for du in (-1.6, 1.6):
            for dz in (-1.4, 1.4):
                off = g.e_lat * du + g.e_up * dz
                self.line(img, g.hub + off, back + off, (185, 185, 185), 1)
        self.line(img, g.hub, back, (205, 205, 205), 3)
        hp = self.p(g.hub)
        if hp:
            cv2.circle(img, hp, 5, (235, 235, 235), -1, cv2.LINE_AA)

        # 标称旋转圆
        self.polyline(img, [g.tip_position(a) for a in np.linspace(0, 360, 181)],
                      (72, 72, 72), 1)

    def render(self, trajs, tips, arc_segs=None, azim0=None, *, sections=None, show_trails=True):
        """
        trajs : {blade_id: [P|None, ...]}  已积累的三维轨迹(含断点)
        tips  : {blade_id: (P, measured)}  当前帧叶尖
        """
        img = np.full((self.h, self.w, 3), 26, np.uint8)
        self._draw_turbine(img, azim0)
        for bid, points in (sections or {}).items():
            self.polyline(img, points, (220, 220, 220), 3)
        if not show_trails:
            trajs, tips = {}, {}

        if arc_segs:                                          # 相机真能看到的弧段
            for lo, hi in arc_segs:
                self.polyline(img, [self.geom.tip_position(a)
                                    for a in np.linspace(lo, hi, 60)], (190, 190, 55), 2)

        for bid, pts in sorted(trajs.items()):
            pts = list(pts)
            col = BLADE_COLORS[bid % 3]
            self.polyline(img, pts, col, 2)
            for P in pts[::3]:
                if P is None:
                    continue
                q = self.p(P)
                if q:
                    cv2.circle(img, q, 2, col, -1, cv2.LINE_AA)

        for bid, (P, meas) in sorted(tips.items()):
            q = self.p(P)
            if not q:
                continue
            col = BLADE_COLORS[bid % 3] if meas else _dim(BLADE_COLORS[bid % 3], 0.6)
            cv2.circle(img, q, 7, col, -1, cv2.LINE_AA)
            cv2.circle(img, q, 10, col, 2, cv2.LINE_AA)

        for vec, col, nm in (([28, 0, 0], (60, 60, 255), 'X'),
                             ([0, 28, 0], (60, 255, 60), 'Y'),
                             ([0, 0, 28], (255, 160, 60), 'Z')):
            self.line(img, [0, 0, 0], vec, col, 2)
            q = self.p(vec)
            if q:
                cv2.putText(img, nm, q, cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1, cv2.LINE_AA)

        cv2.putText(img, 'WORLD 3D   origin = tower base center,  Z up,  meters',
                    (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.44, (205, 205, 205), 1, cv2.LINE_AA)
        cv2.putText(img, 'bright = input tip   dim = short prediction; not field measurement',
                    (8, self.h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.38,
                    (165, 165, 165), 1, cv2.LINE_AA)
        return img



class TipVisualizer(TipTracker):
    """Rendering wrapper; feed advances time, render only redraws current state."""
    def __init__(self, geom, cam, trail=120, max_pts=6000, **kwargs):
        super().__init__(geom, cam, trail, max_pts, **kwargs)
        self.scene = Scene3D(geom, w=640, h=cam.H)
        self.arc = visible_arc(cam, geom)
        self.frame = None

    def feed(self, frame, dets, rpm=0., t=0., *, source='pixel_sphere_estimate'):
        self.update_pixels(dets, rpm, t, source=source)
        self.frame = None if frame is None else frame.copy()
        return self.render()

    def feed_world(self, frame, blades, rpm=0., t=0., *, source, provenance):
        self.update_world(blades, rpm, t, source=source, provenance=provenance)
        self.frame = None if frame is None else frame.copy()
        return self.render()

    def render(self):
        left = self._draw_frame(self.frame, self.detections, self._rpm)
        right = self.scene.render(self.trajs, self.tips, sections=self.sections,
                                  show_trails=self.show_trails)
        return np.hstack([left, right])

    def _draw_frame(self, frame, d, rpm):
        if frame is None:
            img = np.full((self.cam.H, self.cam.W, 3), 35, np.uint8)
        else:
            if frame.shape[:2] != (self.cam.H, self.cam.W):
                raise ValueError('Frame resolution differs from calibrated camera; resize image and detections together')
            img = frame.copy()
        def draw_path(points, color, width):
            segment = []
            for point in [*points, None]:
                u, v, ok = self.cam.project(point) if point is not None else (None,None,False)
                if ok:
                    segment.append((round(u), round(v)))
                else:
                    if len(segment) > 1:
                        cv2.polylines(img, [np.asarray(segment,np.int32)], False, color, width, cv2.LINE_AA)
                    segment = []
        for points in self.sections.values():
            draw_path(points, (225,225,225), 5)
        if self.show_trails:
            for bid, points in self.trail_px.items():
                draw_path(list(points), BLADE_COLORS[bid], 2)
            for bid,(point,valid) in self.tips.items():
                u,v,ok = self.cam.project(point)
                if ok:
                    cv2.circle(img,(round(u),round(v)),6,
                               BLADE_COLORS[bid] if valid else _dim(BLADE_COLORS[bid]),-1,cv2.LINE_AA)
        def number(value):
            return '--' if value is None else f'{value:.3f}'
        lines = [f'{self._source or "waiting"} | t={self._last_t} s | rpm={rpm:.2f}']
        for bid,r in sorted(self.results.items()):
            lines.append(f'B{bid+1} {r["validity"]} | clr {number(r["clearance_m"])} m'
                         f' | plane offset {number(r["out_of_plane_m"])} m')
        for bid,error in sorted(self.errors.items()):
            lines.append(f'B{bid+1}: {error}')
        if not self.sections:
            lines.append('Tip-only input: full blade shape unavailable')
        lines.append('SPACE pause | a/d/w/s view | t trails (starts empty) | q quit')
        for i,line in enumerate(lines):
            cv2.putText(img,line,(8,20+19*i),cv2.FONT_HERSHEY_SIMPLEX,.4,(235,235,235),1,cv2.LINE_AA)
        return img


def load_dets(path):
    """Strict CSV frame,u,v[,blade,rpm]. Blade uses 0,1,2; bad rows name their line."""
    out = {}
    with open(path, newline='', encoding='utf-8-sig') as f:
        for line,row in enumerate(csv.DictReader(f),2):
            k = {c.lower().strip():v for c,v in row.items() if c}
            try:
                frame = finite_scalar(k['frame']); bid = finite_scalar(k.get('blade') or 0)
                if frame < 0 or frame != int(frame) or bid not in (0,1,2):
                    raise ValueError('invalid frame/blade ID')
                u,v = finite_vector([k['u'],k['v']],2)
                rpm = finite_scalar(k['rpm']) if k.get('rpm') else None
                dets,old_rpm = out.setdefault(int(frame), ({},None))
                if int(bid) in dets:
                    raise ValueError('duplicate frame/blade')
                if rpm is not None and old_rpm is not None and rpm != old_rpm:
                    raise ValueError('conflicting RPM for one frame')
                dets[int(bid)] = (u,v)
                out[int(frame)] = (dets, old_rpm if rpm is None else rpm)
            except (KeyError, ValueError, TypeError) as exc:
                raise ValueError(f'{path}:{line}: {exc}') from exc
    return out


def synthetic_blades(geom, azimuth, amplitudes_m=(2.2,1.6,2.9)):
    """Illustrative cantilever curves for UI checks ONLY, not aeroelastic simulation."""
    blades = {}
    for bid,amplitude in enumerate(finite_vector(amplitudes_m)):
        phi = azimuth + 120*bid
        root_to_tip = geom.tip_position(phi)-geom.hub
        s = np.linspace(0,1,40)
        # Smooth root slope; varying shape is explicitly synthetic.
        offset = amplitude*math.cos(math.radians(phi-180))
        points = geom.hub+s[:,None]*root_to_tip - (s*s*(3-s)/2*offset)[:,None]*geom.n_axis
        blades[bid] = {'P':points[-1], 'sections':points}
    return blades


def synth_frame(cam, geom, azim0, defl_deg=(2.2,1.6,2.9), noise_px=1.2):
    """Legacy pixel fixture, explicitly synthetic. Seed RNG externally if needed."""
    img = np.full((cam.H,cam.W,3),40,np.uint8)
    dets = {}
    amplitudes = geom.R * np.sin(np.radians(finite_vector(defl_deg)))
    for bid,data in synthetic_blades(geom,azim0,amplitudes).items():
        points = []
        for p in data['sections']:
            u,v,ok = cam.project(p)
            if ok: points.append((round(u),round(v)))
        if len(points)>1:
            cv2.polylines(img,[np.array(points,np.int32)],False,(220,220,220),4,cv2.LINE_AA)
        u,v,ok = cam.project(data['P'])
        if ok: dets[bid] = (u+np.random.normal(0,noise_px),v+np.random.normal(0,noise_px))
    return img,dets


def play_frames(vis, next_frame, *, fps, no_show=False, writer=None, key_reader=None,
                on_canvas=None):
    """Pump UI independently of data. next_frame feeds exactly one new sample."""
    fps = finite_scalar(fps)
    if fps <= 0: raise ValueError('FPS must be positive')
    key_reader = key_reader or cv2.waitKey
    paused, advance, canvas = False, True, None
    while True:
        start = time.monotonic()
        if advance:
            canvas = next_frame()
            if canvas is None: break
            if writer is not None: writer.write(canvas)
            if on_canvas is not None: on_canvas(canvas)
        else:
            canvas = vis.render()
        if no_show:
            continue
        cv2.imshow('Tip tracking preview',canvas)
        delay = max(1, round(1000*(1/fps-(time.monotonic()-start))))
        key = key_reader(0 if paused else delay) & 0xff
        if key == ord('q'):break
        if key == ord(' '):paused = not paused
        if key in map(ord,'adws'):
            az = -6 if key==ord('a') else 6 if key==ord('d') else 0
            el = 4 if key==ord('w') else -4 if key==ord('s') else 0
            vis.scene.orbit(d_az_deg=az,d_el_deg=el)
        if key==ord('t'):vis.set_trails(not vis.show_trails)
        advance = not paused
    vis.flush()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--video');ap.add_argument('--dets')
    ap.add_argument('--fastfarm-package',type=Path)
    ap.add_argument('--bridge',help='Existing Bridge host:port, e.g. 127.0.0.1:8765')
    ap.add_argument('--scene',help='Backend scene YAML when starting Bridge playback')
    ap.add_argument('--out');ap.add_argument('--no-show',action='store_true')
    ap.add_argument('--frames',type=int,default=500);ap.add_argument('--fps',type=float,default=25)
    ap.add_argument('--rpm',type=float,default=8);ap.add_argument('--hub-height',type=float,default=110)
    ap.add_argument('--overhang',type=float,default=5);ap.add_argument('--tilt',type=float,default=5)
    ap.add_argument('--tip-radius',type=float,default=68);ap.add_argument('--tower-radius',type=float,default=2)
    ap.add_argument('--tower-top-radius',type=float);ap.add_argument('--fov',type=float,default=49.2)
    ap.add_argument('--vfov',type=float,default=31.36);ap.add_argument('--cam-back',type=float,default=8)
    ap.add_argument('--cam-down',type=float,default=3);ap.add_argument('--calibration',type=Path)
    ap.add_argument('--dump',help='PNG prefix');ap.add_argument('--dump-at',default='')
    a = ap.parse_args()
    if a.frames<1 or not math.isfinite(a.fps) or a.fps<=0:ap.error('frames/fps must be positive')
    if bool(a.video)!=bool(a.dets):ap.error('--video and --dets must be supplied together')
    if a.video and (a.fastfarm_package or a.bridge):ap.error('Choose video or FAST.Farm source')
    if a.bridge and not a.fastfarm_package:ap.error('--bridge requires --fastfarm-package')
    opencv()  # actionable error before opening resources
    cap, replay, bridge, writer = None,None,None,None
    try:
        geom = TurbineGeometry(a.hub_height,a.overhang,a.tilt,a.tip_radius,a.tower_radius,
                               tower_top_radius_m=a.tower_top_radius)
        W,H,n = 640,392,a.frames
        if a.fastfarm_package:
            from wfrl.blender_bridge.tip_replay import FastFarmTipReplay
            replay = FastFarmTipReplay(a.fastfarm_package)
            geom = replay.geometry
            n = min(n,len(replay.times))
            a.fps = replay.fps
            if a.bridge:
                from wfrl.blender_bridge.tip_client import TipBridgeClient
                bridge = TipBridgeClient(a.bridge,a.fastfarm_package,a.scene)
        if a.video:
            cap=cv2.VideoCapture(a.video)
            if not cap.isOpened():raise ValueError('Cannot open video: '+a.video)
            W,H=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps=cap.get(cv2.CAP_PROP_FPS)
            if math.isfinite(fps) and fps>0:a.fps=fps
            count=int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if count>0:n=min(n,count)
        cam=CameraModel.look_at(W,H,a.fov,a.vfov,geom.hub-a.cam_back*geom.n_axis+np.array([0,0,-a.cam_down]),
                                geom.tip_position(180),geom.e_lat,upright=True)
        if a.calibration:
            import json
            c=json.loads(a.calibration.read_text())
            cam=CameraModel.from_calibration(W,H,c['K'],c['distortion'],c['position_m'],c['R_cw'])
        local_camera_position = geom.world_rotation.T @ (cam.pos-geom.hub)
        local_camera_rotation = geom.world_rotation.T @ cam.R_cw
        dets=load_dets(a.dets) if a.dets else {}
        vis=TipVisualizer(geom,cam)
        vis.set_trails(False)
        if a.out:
            writer=cv2.VideoWriter(a.out,cv2.VideoWriter_fourcc(*'mp4v'),a.fps,(W+640,H))
            if not writer.isOpened():raise ValueError('Cannot open video writer: '+a.out)
        i=0
        def next_frame():
            nonlocal i
            if i>=n:return None
            index=i;i+=1
            if replay:
                sample=bridge.next_sample() if bridge else replay.at_index(index)
                if sample is None:return None
                geom.set_yaw(sample['yaw_deg']+180)
                cam.set_pose(geom.hub+geom.world_rotation @ local_camera_position,
                             geom.world_rotation @ local_camera_rotation)
                return vis.feed_world(None,sample['blades'],sample['rpm'],sample['time_s'],
                                      source='FAST.Farm',provenance=sample['provenance'])
            t=index/a.fps
            if cap is not None:
                ok,frame=cap.read()
                if not ok:return None
                points,rpm=dets.get(index,({},None))
                return vis.feed(frame,points,a.rpm if rpm is None else rpm,t)
            return vis.feed_world(None,synthetic_blades(geom,150+6*a.rpm*t),a.rpm,t,
                                  source='SYNTHETIC UI fixture',provenance={'formula':'illustrative cantilever curve'})
        dump_at={int(s) for s in a.dump_at.split(',') if s.strip()}
        def dump(canvas):
            if a.dump and i-1 in dump_at:
                path=f'{a.dump}_{i-1:03d}.png'
                if not cv2.imwrite(path,canvas):raise ValueError('Cannot write '+path)
        play_frames(vis,next_frame,fps=a.fps,no_show=a.no_show,writer=writer,on_canvas=dump)
    finally:
        if cap is not None:cap.release()
        if writer is not None:writer.release()
        if bridge is not None:bridge.close()
        if not a.no_show:cv2.destroyAllWindows()


if __name__=='__main__':main()
