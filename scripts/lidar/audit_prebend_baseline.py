"""Register same-sample signed baseline errors and key-passage tip definitions.

Does not change measurements or choose compensation from truth. Surface-point
diagnostics below are on the solver's discrete terminal contour (not full blade).
"""
from pathlib import Path
import argparse
import hashlib
import json
import numpy as np

from wfrl.lidar.physics import read_surface, tip_reference
from wfrl.lidar.moving_tower import horizontal_clearance


def metrics(rows):
    expected = [r for r in rows if r['expected']]
    valid = [r for r in expected if r['beams']['B2']['valid']]
    e = np.array([r['beams']['B2']['estimate_m'] - r['truth_m'] for r in valid])
    passages = {r['passage_id'] for r in expected}
    hit = {r['passage_id'] for r in valid}
    return dict(expected=len(expected), valid=len(valid),
                valid_fraction=len(valid)/len(expected) if expected else None,
                passages=len(passages), missed_passages=len(passages-hit),
                mae_m=float(np.abs(e).mean()) if len(e) else None,
                p95_m=float(np.percentile(np.abs(e),95)) if len(e) else None,
                max_abs_m=float(np.abs(e).max()) if len(e) else None,
                max_overestimate_m=float(max(0,e.max())) if len(e) else None,
                signed_min_m=float(e.min()) if len(e) else None,
                signed_max_m=float(e.max()) if len(e) else None)


def audit(package, output):
    package, output = Path(package), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((package/'manifest.json').read_text())
    for name, digest in manifest['files'].items():
        if hashlib.sha256((package/name).read_bytes()).hexdigest() != digest:
            raise ValueError('Hash mismatch: '+name)
    source = json.loads((package/'source-run.json').read_text())
    farm = Path(source['case_dir'])/'FarmInputs'
    data = json.loads((package/'data.json').read_text())
    hashes = json.loads((package/'source-surfaces.json').read_text())
    table, samples = {}, []
    for k, (tid, payload) in enumerate(data.items()):
        rows = payload['measurements']
        table[tid] = dict(all=metrics(rows), by_blade={str(b): metrics([r for r in rows if r['blade_id']==b]) for b in (1,2,3)})
        # Lowest recorded truth in EACH blade passage covers all blades and
        # low-clearance poses without pretending this is a continuous minimum.
        passages = sorted({r['passage_id'] for r in rows})
        for passage in passages:
            row = min((r for r in rows if r['passage_id']==passage), key=lambda r:r['truth_m'])
            frame = round(row['time_s']*manifest['source_fps'])
            def surface(kind):
                key = f'vtk/Case.{tid}.{kind}Surface.{frame:05d}.vtp'
                p = farm/key
                if hashlib.sha256(p.read_bytes()).hexdigest() != hashes[key]:
                    raise ValueError('Source mismatch: '+key)
                xyz, triangles = read_surface(p)
                return xyz - np.array(manifest['layout_m'][k]), triangles
            blade, _ = surface(f'Blade{row["blade_id"]}')
            tower, triangles = surface('Tower')
            center = tip_reference(blade, 19)
            center_clearance, _ = horizontal_clearance(center, tower, triangles)
            if abs(center_clearance-row['truth_m']) > 1e-6:
                raise ValueError('Existing truth cannot be reproduced')
            ring = blade.reshape(19,-1,3)[-1]
            distances = np.array([horizontal_clearance(p,tower,triangles)[0] for p in ring])
            index = int(distances.argmin())
            samples.append(dict(turbine_id=tid, blade_id=row['blade_id'], passage_id=passage,
                time_s=row['time_s'], center_m=center_clearance, surface_vertex_m=float(distances[index]),
                difference_m=float(center_clearance-distances[index]),
                threshold_class_changed=bool((center_clearance<=7)!=(distances[index]<=7)),
                nearest_vertex=ring[index].tolist(),
                contour_max_edge_m=float(np.linalg.norm(np.diff(ring,axis=0),axis=1).max())))
        print(tid, table[tid]['all'], flush=True)
    diff = np.array([r['difference_m'] for r in samples])
    summary = dict(errors=table, tip_points=dict(count=len(samples),
        max_m=float(diff.max()), p95_m=float(np.percentile(diff,95)),
        threshold_changes=sum(r['threshold_class_changed'] for r in samples),
        lowest_center=min(samples,key=lambda r:r['center_m']),
        lowest_surface_vertex=min(samples,key=lambda r:r['surface_vertex_m'])),
        window='Existing +/-3 degree samples only; extended diagnostic window still required',
        scope='Discrete contour vertices at minimum recorded truth in each passage; edge-interior and cosmetic apex not yet included',
        acceptance='Diagnostic only; no automatic equivalence or compensation decision')
    (output/'baseline-diagnostics.json').write_text(json.dumps(summary,indent=2))
    (output/'tip-point-samples.json').write_text(json.dumps(samples,indent=2))
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('package',type=Path); p.add_argument('output',type=Path)
    args=p.parse_args(); audit(args.package,args.output)
