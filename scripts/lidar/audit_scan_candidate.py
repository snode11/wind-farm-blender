"""Blender-only, isolated ideal snapshot scan diagnostic; never changes source data.

The grid is fixed before the held-out half is evaluated. All blade surfaces,
tower and ground participate in first-return occlusion. Selection uses only
range and calibrated ray direction, not truth, blade identity or actual tip.
This is an alternative sensor model, NOT a repair of fixed three-ray hardware.
"""
from pathlib import Path
import hashlib
import json
import math
import sys
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.lidar.audit_prebend_baseline import metrics
from wfrl.lidar.physics import first_hit

CONTRACT = dict(
    schema='wfrl.ideal-snapshot-scan.v1',
    axial_deg=[4., 12., .05], lateral_deg=[-6., 6., .1],
    origin_m=[-2., 0., 87.6], range_gate_m=[50., 70.],
    selection='Farthest first return within fixed range gate; stable grid-order tie break',
    estimator='range * -direction_x + 2.0 - 2.67, in calibrated nacelle frame',
    truth_input=False, blade_identity_input=False,
    design_interval_s=[117., 147.], held_out_interval_s=[147., 177.],
    boundary='Instantaneous ideal 19481-ray angular grid per timestamp; no sequential scan motion, optical return strength or hardware capability certification',
)


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def audit(package, output):
    package, output = Path(package), Path(output)
    output.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((package/'manifest.json').read_text())
    for name, digest in manifest['files'].items():
        if hashlib.sha256((package/name).read_bytes()).hexdigest() != digest:
            raise ValueError('Source integrity mismatch: '+name)
    save(output/'contract.json', CONTRACT)
    data = json.loads((package/'data.json').read_text())
    with np.load(package/'geometry.npz') as g:
        times, transforms = g['times'], g['transforms']
    with np.load(package/'tower-motion.npz') as q:
        nacelles, towers, heights = q['nacelle'], q['transforms'], q['heights']
    with np.load(package/'reference-surfaces.npz') as r:
        refs, tri, twref, twtri = r['blades'].reshape(3,19,-1,3), r['triangles'], r['tower'], r['tower_triangles']
    rings = np.array([np.flatnonzero(twref[:,2] == h) for h in heights])
    grid = np.array([(a,b) for a in np.linspace(4,12,161) for b in np.linspace(-6,6,121)])
    a,b = np.radians(grid).T
    directions = np.column_stack((-np.sin(a)*np.cos(b), np.sin(b), -np.cos(a)*np.cos(b)))
    budget = json.loads((ROOT/'evidence/prebend-20260918/acceptance-budget.json').read_text())
    rows, summaries = {}, {}
    for k, tid in enumerate(manifest['turbine_ids']):
        rows[tid] = []
        for count, original in enumerate(data[tid]['measurements']):
            i = int(np.searchsorted(times, original['time_s']))
            if times[i] != original['time_s']: raise ValueError('Timestamp mismatch')
            n = nacelles[i,k]; origin = n[:,:3]@CONTRACT['origin_m']+n[:,3]
            tr = transforms[i,k]
            surfaces = (np.einsum('bsij,bsnj->bsni',tr[:,:,:,:3],refs)+tr[:,:,:,3][:,:,None]).reshape(3,-1,3)
            tw = twref.copy(); tr = towers[i,k]
            tw[rings] = np.einsum('sij,snj->sni',tr[:,:,:3],twref[rings])+tr[:,:,3][:,None]
            points = np.concatenate([*surfaces,tw])
            triangles = np.concatenate([*(tri+j*len(surfaces[0]) for j in range(3)), twtri+3*len(surfaces[0])])
            tree = BVHTree.FromPolygons(points.tolist(),triangles.tolist(),all_triangles=True)
            selected = None
            dw = directions@n[:,:3].T
            origin_v = Vector(origin)
            for ray_id, direction in enumerate(dw):
                loc,_,face,distance = tree.ray_cast(origin_v,Vector(direction),100.)
                ground = -origin[2]/direction[2] if direction[2]<0 else math.inf
                if loc is None or ground < distance:
                    distance,face = ground, -1
                if 50 <= distance <= 70 and (selected is None or distance > selected[0]):
                    selected = (distance,ray_id,face)
            row = dict(original)
            row['beams'] = dict(original['beams'])
            # Recheck the chosen BVH hit against double precision all-surface
            # Moller-Trumbore, including occlusion; retain invalid outcomes.
            beam = dict(valid=False, estimate_m=None, error_m=None, slant_range_m=None,
                        reason='no_return_in_fixed_range_gate', ray_id=None)
            if selected is not None:
                _,ray_id,_ = selected
                hit = first_hit(origin,dw[ray_id],points,triangles)
                ground = -origin[2]/dw[ray_id,2]
                if hit is not None and hit[0] < ground:
                    distance,point,face = hit
                    bid = face//len(tri)+1 if face < 3*len(tri) else None
                    valid = bool(bid == original['blade_id'] and 50 <= distance <= 70)
                    estimate = float(-distance*directions[ray_id,0]+2.-2.67) if valid else None
                    beam = dict(valid=valid,slant_range_m=distance,estimate_m=estimate,
                        error_m=estimate-original['truth_m'] if valid else None,
                        reason='valid' if valid else 'occluded_or_outside_range_gate',
                        ray_id=int(ray_id),angles_deg=grid[ray_id].tolist(),
                        direction_nacelle=directions[ray_id].tolist(),
                        origin_m=(origin+manifest['layout_m'][k]).tolist(),
                        direction_world=dw[ray_id].tolist(),
                        hit_point_m=(np.array(point)+manifest['layout_m'][k]).tolist())
            row['beams']['B2'] = beam
            rows[tid].append(row)
            if count%40==0: print(tid,count,'of',len(data[tid]['measurements']),flush=True)
        summaries[tid] = {}
        for split, subset in [('design',[r for r in rows[tid] if r['time_s']<147]),
                              ('held_out',[r for r in rows[tid] if r['time_s']>=147]),
                              ('full',rows[tid])]:
            m = metrics(subset)
            m['passed'] = bool(m['p95_m'] is not None and m['p95_m']<=budget['E95_m'] and m['max_abs_m']<=budget['Emax_m']
                and m['valid_fraction']>=budget['Vmin_sample_fraction'] and 1-m['missed_passages']/m['passages']>=budget['Vmin_passage_fraction'])
            summaries[tid][split] = m
        print(tid,summaries[tid],flush=True)
        save(output/'summary.json',summaries); save(output/'rows.json',rows)
    save(output/'provenance.json',dict(source_manifest_sha256=hashlib.sha256((package/'manifest.json').read_bytes()).hexdigest(),
        source_package=str(package.resolve()),budget=budget,contract_sha256=hashlib.sha256((output/'contract.json').read_bytes()).hexdigest(),
        status='PASS_IDEAL_SCAN_ONLY' if all(v['full']['passed'] and v['held_out']['passed'] for v in summaries.values()) else 'FAILED',
        source_unchanged=all(hashlib.sha256((package/n).read_bytes()).hexdigest()==h for n,h in manifest['files'].items())))


if __name__ == '__main__':
    args = sys.argv[sys.argv.index('--')+1:]
    audit(*args)
