"""Deterministic dimensions, loft and analytically integrated animation (stdlib)."""
from bisect import bisect_right
import json
import math
from pathlib import Path
import re
from functools import lru_cache

PROJECT = Path(__file__).resolve().parents[1]


def read_config():
    return tuple(json.loads((PROJECT / p).read_text()) for p in
                 ('geometry/target.json', 'motion/timeline.json', 'cameras/layout.json'))


def smooth(u):
    return u*u*(3 - 2*u)


def integral(u):
    return u**3 - .5*u**4


def boundaries(m):
    result = [0.0]
    for key in ('stationary_start_s', 'acceleration_s', 'hold_s',
                'deceleration_s', 'stationary_end_s'):
        duration = m[key]
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError(f'{key} must be finite and positive')
        result.append(result[-1] + duration)
    return result


def motion_at(t, m):
    if not math.isfinite(t):
        raise ValueError('Time must be finite')
    _, a, b, c, d, end = boundaries(m)
    if not 0 <= t <= end:
        raise ValueError(f'Time must be in [0, {end}]')
    speed, stop, run = m['running_rpm'], m['stopped_pitch_deg'], m['running_pitch_deg']
    if not 7 <= speed <= 9:
        raise ValueError('Running speed must be in 7–9 rpm')
    accel, decel = b-a, d-c
    if t < a:
        rpm, pitch, turns, phase = 0., stop, 0., 'stationary_start'
    elif t < b:
        u = (t-a)/accel
        rpm, pitch = speed*smooth(u), stop+(run-stop)*smooth(u)
        turns, phase = speed*accel*integral(u)/60, 'acceleration'
    elif t < c:
        rpm, pitch = speed, run
        turns, phase = speed*(accel/2+t-b)/60, 'hold'
    elif t < d:
        u = (t-c)/decel
        rpm, pitch = speed*(1-smooth(u)), run+(stop-run)*smooth(u)
        turns = speed*(accel/2+c-b+decel*(u-integral(u)))/60
        phase = 'deceleration'
    else:
        rpm, pitch = 0., stop
        turns, phase = speed*(accel/2+c-b+decel/2)/60, 'stationary_end'
    return dict(time_s=t, rpm=rpm, pitch_deg=pitch,
                azimuth_deg=m['initial_azimuth_deg']+360*turns, phase=phase)


def frame_times(start, duration, fps):
    if (not all(math.isfinite(v) for v in (start, duration, fps)) or start < 0
            or duration <= 0 or isinstance(fps, bool) or int(fps) != fps or fps <= 0):
        raise ValueError('Invalid frame timing')
    count = duration*fps
    if abs(round(count)-count) > 1e-7:
        raise ValueError('Duration must contain an integer number of frames')
    # Half-open interval; no duplicated terminal frame. First frame is exactly start.
    return [start+i/fps for i in range(round(count))]


def interpolate(rows, x, col):
    i = max(0, min(len(rows)-2, bisect_right([r[0] for r in rows], x)-1))
    a, b = rows[i:i+2]
    u = min(1., max(0., (x-a[0])/(b[0]-a[0])))
    return a[col]*(1-u)+b[col]*u


def source_stations():
    aero = []
    for line in (PROJECT/'sources/DTU_10MW_RWT_ae.dat').read_text().splitlines()[2:]:
        aero.append([float(v) for v in line.split(';')[0].split()[:3]])
    text = (PROJECT/'sources/DTU_10MW_RWT.htc').read_text()
    blade = re.search(r'name\s+blade1\s*;(.*?)end c2_def', text, re.S).group(1)
    twist = []
    for match in re.finditer(r'\bsec\s+\d+\s+([^;]+);', blade):
        x, y, z, angle = map(float, match.group(1).split())
        twist.append([z, -angle])
    if len(aero) != 40 or len(twist) != 27:
        raise ValueError('Unexpected DTU source station count')
    return aero, twist


def smooth_interpolate(rows, x, col):
    """Shape-preserving cubic Hermite interpolation; exact at each source station."""
    xs=tuple(r[0] for r in rows)
    ys=tuple(r[col] for r in rows)
    slopes=_slopes(xs,ys)
    i=max(0,min(len(rows)-2,bisect_right(xs,x)-1))
    h=xs[i+1]-xs[i]
    t=min(1.,max(0.,(x-xs[i])/h))
    return ((2*t**3-3*t*t+1)*ys[i]+(t**3-2*t*t+t)*h*slopes[i]
            +(-2*t**3+3*t*t)*ys[i+1]+(t**3-t*t)*h*slopes[i+1])


@lru_cache(maxsize=32)
def _slopes(xs,ys):
    h=[b-a for a,b in zip(xs,xs[1:])]
    d=[(b-a)/w for a,b,w in zip(ys,ys[1:],h)]
    out=[d[0]]
    for i in range(1,len(ys)-1):
        if d[i-1]*d[i]<=0:
            out.append(0.)
        else:
            w1,w2=2*h[i]+h[i-1],h[i]+2*h[i-1]
            out.append((w1+w2)/(w1/d[i-1]+w2/d[i]))
    return tuple(out+[d[-1]])


@lru_cache(maxsize=1)
def source_prebend():
    text=(PROJECT/'sources/DTU_10MW_RWT.htc').read_text()
    block=re.search(r'name\s+blade1\s*;(.*?)end c2_def',text,re.S).group(1)
    rows=[]
    for match in re.finditer(r'\bsec\s+\d+\s+([^;]+);',block):
        _,y,z,_=map(float,match.group(1).split())
        rows.append([z,y])
    return rows


def centreline_at(span,g):
    """C2-def Y curve is mapped to local axial X, root offset removed.

    Normalized DTU curvature with an explicitly chosen target tip offset.
    Positive amplitude bends upwind (-X), away from the nacelle, at zero pitch.
    This is a geometric assumption, not a GW184 measured unloaded shape.
    """
    rows=source_prebend()
    t=span/g['blade_span_m']
    fraction=(smooth_interpolate(rows,t*rows[-1][0],1)-rows[0][1])/(rows[-1][1]-rows[0][1])
    # Blend zero near root to maintain the straight cylindrical mounting collar.
    root_blend=smooth(min(1.,max(0.,span/3)))
    return (-g['prebend_m']*fraction*root_blend,0.,span)


@lru_cache(maxsize=4)
def reference_rings(n):
    data=json.loads((PROJECT/'sources/ffa_reference.json').read_text())
    rings=[]
    for profile in data['profiles']:
        points=profile['coordinates']
        leading=min(range(len(points)),key=lambda i:points[i][0])
        surfaces=(list(reversed(points[:leading+1])),points[leading:])
        ring=[]
        for j in range(n):
            x=.5*(1+math.cos(math.tau*j/n))
            surface=surfaces[0 if j<=n//2 else 1]
            y=interpolate(surface,x,1)
            ring.append((x,y))
        # One closed seam at the trailing-edge midpoint; adjacent samples retain
        # the source's finite trailing-edge thickness. No duplicated seam vertices.
        ring[0]=(1.,.5*(points[0][1]+points[-1][1]))
        rings.append((profile['relative_thickness'],ring))
    return rings


def section_ring(thickness,n):
    profiles=reference_rings(n)
    if thickness<=profiles[0][0]:
        t,ring=profiles[0]
        return [(x,y*thickness/t) for x,y in ring]
    if thickness>=profiles[-1][0]:
        t,ring=profiles[-1]
        # Thick reference extensions are geometric scaling, not new named airfoils.
        return [(x,y*thickness/t) for x,y in ring]
    for (a,first),(b,second) in zip(profiles,profiles[1:]):
        if a<=thickness<=b:
            u=(thickness-a)/(b-a)
            return [(p[0],(1-u)*p[1]+u*q[1]) for p,q in zip(first,second)]
    raise ValueError('Unsupported thickness')


def validate_geometry(g):
    for key in ('blade_span_m','max_chord_m','root_radius_m','root_diameter_m',
                'hub_height_m','span_step_m','nacelle_length_m','nacelle_width_m','nacelle_height_m'):
        if not math.isfinite(g[key]) or g[key]<=0:
            raise ValueError(f'{key} must be positive and finite')
    if not math.isfinite(g['prebend_m']) or not 0<=g['prebend_m']<g['blade_span_m']/5:
        raise ValueError('Invalid prebend amplitude')
    if g['shaft_tilt_deg']!=0 or g['precone_deg']!=0:
        raise ValueError('This isolated scene supports zero tilt/cone only; do not silently ignore nonzero values')
    if not isinstance(g['ring_points'],int) or g['ring_points']<32 or g['ring_points']%2:
        raise ValueError('ring_points must be an even integer >= 32')


def blade_mesh(g):
    """DTU scalar distributions, documented FFA contours and rigid static prebend.

    Local X=axial/downstream, Y=chord, Z=radial pitch-axis coordinate. Circular
    root, independently adapted maximum chord, and explicitly synthetic tip cap.
    """
    validate_geometry(g)
    aero,twists=source_stations()
    length,n=g['blade_span_m'],g['ring_points']
    samples=[i*length/math.ceil(length/g['span_step_m']) for i in range(math.ceil(length/g['span_step_m'])+1)]
    samples += [r[0]/aero[-1][0]*length for r in aero]
    samples += [length-.6+.6*math.sin(math.pi*i/48) for i in range(25)]
    # Merge stations closer than a micron to avoid sliver faces from source rounding.
    spans=[]
    for value in sorted(samples):
        if not spans or value-spans[-1]>1e-6:
            spans.append(value)
    vertices,stations=[],[]
    chord_scale=g['max_chord_m']/max(row[1] for row in aero)
    for span in spans[:-1]:
        source_s=span/length*aero[-1][0]
        chord=smooth_interpolate(aero,source_s,1)*chord_scale
        root_mix=smooth(min(1.,span/10))
        chord=g['root_diameter_m']*(1-root_mix)+chord*root_mix
        if span>length-.6:
            chord*=math.sqrt(max(0.,1-((span-(length-.6))/.6)**3))
        thickness=smooth_interpolate(aero,source_s,2)/100
        beta=math.radians(smooth_interpolate(twists,source_s,1))
        mix=smooth(min(1.,max(0.,(span-2)/16)))
        # The entire cross-section moves to the curved centreline before collective
        # pitch/rotor object transforms; no world-fixed bend or runtime displacement.
        cx,cy,_=centreline_at(span,g)
        stations.append(dict(span_m=span,chord_m=chord,thickness_ratio=thickness,
            twist_deg=math.degrees(beta),centreline_axial_m=cx,centreline_chord_m=cy))
        for j,(x,y) in enumerate(section_ring(thickness,n)):
            elliptic=.5*thickness*math.sin(math.tau*j/n)
            v=chord*((1-mix)*elliptic+mix*y)
            u=(x-.5)*chord
            vertices.append((cx-u*math.sin(beta)+v*math.cos(beta),
                             cy+u*math.cos(beta)+v*math.sin(beta),span))
    faces=[]
    for k in range(len(stations)-1):
        for j in range(n):
            a,b=k*n+j,k*n+(j+1)%n
            faces.append((a,a+n,b+n,b))
    faces.append(tuple(range(n)))
    tip=len(vertices)
    vertices.append(centreline_at(length,g))
    last=(len(stations)-1)*n
    faces.extend((last+j,tip,last+(j+1)%n) for j in range(n))
    return vertices,faces,stations
