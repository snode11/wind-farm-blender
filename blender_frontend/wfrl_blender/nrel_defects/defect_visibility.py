"""Ideal projection and area-weighted visibility for declared defect supports.

Candidate projected area is an exact polygon union (within floating precision).
Visible area is a documented subpixel midpoint quadrature of that same union,
not a ray-count ratio. No pixel raster rounding erases subpixel defects.
"""
import math

OCCLUSION_TOLERANCE_M = 1e-4
FRACTION_TOLERANCE = 1e-6
MAX_AREA_SAMPLES = 200000
EXCLUDED_ROLES = {'editor_marker', 'cutter', 'healthy_reference', 'staging'}


class AnalysisCancelled(RuntimeError):
    pass


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _length(a):
    return math.sqrt(_dot(a, a))


def _cross2(a, b):
    return a[0] * b[1] - a[1] * b[0]


def clip_polygon(polygon, bounds):
    """Sutherland-Hodgman clipping to pixel borders (-.5 .. size-.5)."""
    result = [tuple(p) for p in polygon]
    for axis, boundary, lower in ((0, bounds[0], True), (0, bounds[2], False),
                                  (1, bounds[1], True), (1, bounds[3], False)):
        source, result = result, []
        if not source:
            break
        previous = source[-1]
        previous_in = previous[axis] >= boundary if lower else previous[axis] <= boundary
        for current in source:
            current_in = current[axis] >= boundary if lower else current[axis] <= boundary
            if current_in != previous_in:
                t = (boundary - previous[axis]) / (current[axis] - previous[axis])
                result.append(tuple(previous[j] + t * (current[j] - previous[j]) for j in range(2)))
            if current_in:
                result.append(current)
            previous, previous_in = current, current_in
    return result


def union_cells(polygons):
    """Partition a convex-polygon union into disjoint vertical trapezoids.

    Vertex and segment-intersection x coordinates partition changes in boundary
    ordering. Within a slab, each lower/upper union boundary is a linear edge.
    Inputs are support triangles or their convex frame-clipped polygons.
    Cells are (x0,x1,lower_slope,lower_intercept,upper_slope,upper_intercept).
    """
    polygons = [p for p in polygons if len(p) >= 3]
    edges, events, polygon_edges = [], [], []
    for polygon in polygons:
        local = []
        events.extend(p[0] for p in polygon)
        for p, q in zip(polygon, polygon[1:] + polygon[:1]):
            edge = (p, q)
            edges.append(edge)
            if abs(q[0] - p[0]) > 1e-13:
                slope = (q[1] - p[1]) / (q[0] - p[0])
                local.append((min(p[0], q[0]), max(p[0], q[0]), slope, p[1] - slope * p[0]))
        polygon_edges.append(local)
    # Sweep only overlapping edge bounds. This preserves all intersections
    # while avoiding millions of unrelated facet pairs in flexible supports.
    bounded = sorted((min(a[0],b[0]),max(a[0],b[0]),min(a[1],b[1]),max(a[1],b[1]),a,b)
                     for a,b in edges)
    for i, (_, xmax, ymin, ymax, a, b) in enumerate(bounded):
        ab = _sub(b, a)
        for xmin2, _, ymin2, ymax2, c, d in bounded[i + 1:]:
            if xmin2 > xmax: break
            if ymin2 > ymax or ymax2 < ymin: continue
            cd = _sub(d, c)
            denominator = _cross2(ab, cd)
            if abs(denominator) <= 1e-15:
                continue
            ca = _sub(c, a)
            t, u = _cross2(ca, cd) / denominator, _cross2(ca, ab) / denominator
            if 0 < t < 1 and 0 < u < 1:
                events.append(a[0] + t * ab[0])
    xs = []
    for x in sorted(events):
        if not xs or x - xs[-1] > 1e-10:
            xs.append(x)
    cells = []
    for x0, x1 in zip(xs, xs[1:]):
        xm = (x0 + x1) / 2
        intervals = []
        for edges_for_polygon in polygon_edges:
            hits = [(slope * xm + intercept, slope, intercept)
                    for lo, hi, slope, intercept in edges_for_polygon if lo < xm < hi]
            if len(hits) >= 2:
                hits.sort()
                intervals.append((hits[0], hits[-1]))
        intervals.sort(key=lambda pair: pair[0][0])
        merged = []
        for lo, hi in intervals:
            if hi[0] - lo[0] <= 1e-12:
                continue
            if merged and lo[0] <= merged[-1][1][0] + 1e-10:
                if hi[0] > merged[-1][1][0]:
                    merged[-1] = (merged[-1][0], hi)
            else:
                merged.append((lo, hi))
        cells.extend((x0, x1, lo[1], lo[2], hi[1], hi[2]) for lo, hi in merged)
    return cells


def cell_area(cell):
    x0, x1, lm, lb, um, ub = cell
    return max(0.0, (x1 - x0) * ((um - lm) * ((x0 + x1) / 2) + ub - lb))


def union_area(polygons):
    return sum(cell_area(cell) for cell in union_cells(polygons))


def _barycentric(point, triangle):
    a, b, c = triangle
    ab, ac, ap = _sub(b, a), _sub(c, a), _sub(point, a)
    d = _cross2(ab, ac)
    if abs(d) < 1e-15:
        return None
    v, w = _cross2(ap, ac) / d, _cross2(ab, ap) / d
    weights = (1 - v - w, v, w)
    return weights if min(weights) >= -1e-7 else None


def _bbox(points):
    if not points:
        return None
    return [min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points)]


def _project(point, camera):
    delta = _sub(point, camera['origin'])
    x, y, depth = [_dot(axis, delta) for axis in camera['axes']]
    if depth <= camera.get('near', 0.01):
        return None, depth
    return (camera['fx'] * x / depth + camera['cx'],
            camera['cy'] - camera['fy'] * y / depth), depth


def analyze_support(support, camera, ray_distance, *, sampling_step_px=0.75,
                    occlusion_tolerance_m=OCCLUSION_TOLERANCE_M, cancel=None):
    """Pure-Python area analysis using world coordinates.

    ``camera`` contains origin, orthonormal axes [right,up,forward], fx/fy/cx/cy,
    width/height, near/far. ``ray_distance(origin,unit_direction,max_distance)``
    returns first actual-geometry hit distance or None. Virtual support triangles
    must never be inserted in that occluder scene.
    """
    if not math.isfinite(sampling_step_px) or sampling_step_px <= 0:
        raise ValueError('sampling_step_px must be positive and finite')
    if not math.isfinite(occlusion_tolerance_m) or occlusion_tolerance_m <= 0:
        raise ValueError('Invalid occlusion tolerance')
    triangles = support.get('triangles_world', support.get('triangles', []))
    normals = support.get('normals_world', support.get('normals', []))
    row = {key: support.get(key) for key in ('defect_id', 'revision', 'blade_id', 'support_id',
                                           'support_kind', 'support_definition', 'primary')}
    row.update(status='INVALID', reasons=[], projected_bbox_px=None, visible_bbox_px=None,
               in_frame_fraction=None, visible_fraction_in_frame=None, visible_area_px2=None,
               image_fraction=None, length_px=None, width_px=None, view_angle_deg=None,
               camera_depth_m=None, sample_visible_fraction=None,
               appearance_review=dict(status='NOT_REVIEWED', reason='Geometry does not establish appearance discernibility.'),
               method='exact_candidate_polygon_union; area_weighted_subpixel_midpoint_ray_quadrature',
               sampling=dict(max_step_px=sampling_step_px, area_samples=0, ray_tests=0,
                             sample_budget=MAX_AREA_SAMPLES),
               tolerance=dict(occlusion_m=occlusion_tolerance_m, fraction=FRACTION_TOLERANCE,
                              projected_area_degenerate_px2=1e-14, union_event_merge_px=1e-10),
               limitations=['Visible area and visible bounds are quadrature estimates, not continuous occlusion proofs.',
                            'Occluders smaller than the quadrature cells can be missed; no guaranteed visibility error bound.',
                            'Ideal instantaneous geometry only; no image appearance or sensor model.'])
    if support.get('enabled') is False:
        row['status'] = 'NOT_EVALUATED'
        row['reasons'] = ['DEFECT_DISABLED']
        return row
    if not triangles or len(normals) != len(triangles):
        row['reasons'] = ['MISSING_OR_INCONSISTENT_SUPPORT']
        return row
    projected, depths, angles = [], [], []
    for triangle, normal in zip(triangles, normals):
        vertices, tri_depths = [], []
        for point in triangle:
            pixel, depth = _project(point, camera)
            if pixel is None:
                row['reasons'] = ['SUPPORT_BEHIND_OR_CROSSING_NEAR_PLANE']
                return row
            if depth >= camera.get('far', math.inf):
                row['reasons'] = ['SUPPORT_OUTSIDE_OR_CROSSING_FAR_PLANE']
                return row
            vertices.append(pixel)
            tri_depths.append(depth)
            to_camera = _sub(camera['origin'], point)
            magnitude = _length(to_camera) * _length(normal)
            if magnitude <= 1e-14:
                row['reasons'] = ['DEGENERATE_SUPPORT_NORMAL']
                return row
            angles.append(math.degrees(math.acos(max(-1.0, min(1.0, _dot(normal, to_camera) / magnitude)))))
        projected.append(vertices)
        depths.append(tri_depths)
    row['projected_bbox_px'] = _bbox([p for tri in projected for p in tri])
    row['view_angle_deg'] = dict(min=min(angles), max=max(angles))
    row['camera_depth_m'] = dict(min=min(d for tri in depths for d in tri), max=max(d for tri in depths for d in tri))
    full_area = union_area(projected)
    if full_area <= 1e-14:
        row['reasons'] = ['DEGENERATE_PROJECTED_SUPPORT']
        return row
    width, height = camera['width'], camera['height']
    clipped = [clip_polygon(tri, (-0.5, -0.5, width - 0.5, height - 0.5)) for tri in projected]
    cells = union_cells(clipped)
    framed_area = sum(cell_area(cell) for cell in cells)
    row['candidate_area_px2'] = full_area
    row['in_frame_candidate_area_px2'] = framed_area
    row['in_frame_fraction'] = min(1.0, max(0.0, framed_area / full_area))

    centerline = support.get('centreline_world', support.get('centerline', []))
    if centerline:
        line = [_project(point, camera)[0] for point in centerline]
        if all(p is not None for p in line):
            row['length_px'] = sum(math.dist(a, b) for a, b in zip(line, line[1:]))
    widths = support.get('width_sections_world', support.get('width_sections', []))
    measured = []
    for left, right in widths:
        a, b = _project(left, camera)[0], _project(right, camera)[0]
        if a is not None and b is not None:
            measured.append(math.dist(a, b))
    row['width_px'] = dict(min=min(measured) if measured else None, max=max(measured) if measured else None,
                           mean=sum(measured) / len(measured) if measured else None,
                           values=measured, valid_sections=len(measured), total_sections=len(widths),
                           valid_coverage=len(measured) / len(widths) if widths else None,
                           reason=None if measured else 'NO_VALID_WIDTH_SECTIONS')
    if framed_area <= 1e-14:
        row.update(status='OUT_OF_FRAME', visible_area_px2=0.0, image_fraction=0.0)
        row['reasons'] = ['ZERO_IN_FRAME_CANDIDATE_AREA']
        return row

    visible_area, visible_bounds, samples, visible_samples, rays, backface = 0.0, [], 0, 0, 0, 0
    for x0, x1, lm, lb, um, ub in cells:
        nx = max(1, math.ceil((x1 - x0) / sampling_step_px))
        for ix in range(nx):
            xa, xb = x0 + (x1 - x0) * ix / nx, x0 + (x1 - x0) * (ix + 1) / nx
            x = (xa + xb) / 2
            low, high = lm * x + lb, um * x + ub
            ny = max(1, math.ceil((high - low) / sampling_step_px))
            weight = (xb - xa) * (high - low) / ny
            for iy in range(ny):
                if samples % 128 == 0 and cancel and cancel():
                    raise AnalysisCancelled('Visibility calculation cancelled or state replaced')
                samples += 1
                if samples > MAX_AREA_SAMPLES:
                    row['reasons'] = ['AREA_SAMPLE_BUDGET_EXCEEDED; choose a smaller support or explicitly coarser sampling']
                    row['sampling'].update(area_samples=samples, ray_tests=rays)
                    return row
                y = low + (iy + 0.5) * (high - low) / ny
                visible, facing_any = False, False
                for index, tri in enumerate(projected):
                    bary = _barycentric((x, y), tri)
                    if bary is None:
                        continue
                    weights = [bary[j] / depths[index][j] for j in range(3)]
                    total = sum(weights)
                    point = tuple(sum(weights[j] * triangles[index][j][axis] for j in range(3)) / total for axis in range(3))
                    delta = _sub(point, camera['origin'])
                    distance = _length(delta)
                    if _dot(normals[index], delta) >= -1e-10:
                        continue
                    facing_any = True
                    direction = tuple(v / distance for v in delta)
                    # Applies to both actual surfaces and virtual opening footprints:
                    # hits at/beyond P do not block the path leading to P.
                    hit = ray_distance(camera['origin'], direction, max(0.0, distance - occlusion_tolerance_m))
                    rays += 1
                    if hit is None or hit >= distance - occlusion_tolerance_m:
                        visible = True
                        break
                if not facing_any:
                    backface += 1
                if visible:
                    visible_samples += 1
                    visible_area += weight
                    # Cell bounds for a positive sample are an estimated extent.
                    ya, yb = iy / ny, (iy + 1) / ny
                    for xx in (xa, xb):
                        lo, hi = lm * xx + lb, um * xx + ub
                        visible_bounds.extend(((xx, lo + ya * (hi - lo)), (xx, lo + yb * (hi - lo))))
    visible_area = min(framed_area, max(0.0, visible_area))
    row.update(visible_area_px2=visible_area, visible_fraction_in_frame=visible_area / framed_area,
               image_fraction=visible_area / (width * height), visible_bbox_px=_bbox(visible_bounds),
               sample_visible_fraction=visible_samples / samples if samples else None)
    row['sampling'].update(area_samples=samples, ray_tests=rays)
    if visible_area <= 1e-14:
        row['status'] = 'OCCLUDED'
        row['reasons'] = ['BACK_FACING_SUPPORT' if backface == samples else 'ACTUAL_GEOMETRY_BLOCKS_SUPPORT']
    elif row['in_frame_fraction'] >= 1 - FRACTION_TOLERANCE and row['visible_fraction_in_frame'] >= 1 - FRACTION_TOLERANCE:
        row['status'] = 'VISIBLE'
    else:
        row['status'] = 'PARTIAL'
        if row['in_frame_fraction'] < 1 - FRACTION_TOLERANCE:
            row['reasons'].append('PARTIALLY_OUT_OF_FRAME')
        if row['visible_fraction_in_frame'] < 1 - FRACTION_TOLERANCE:
            row['reasons'].append('PARTIALLY_OCCLUDED_OR_BACK_FACING')
    return row


def _renderable_objects(scene):
    objects, seen = [], set()

    def walk(collection, hidden=False):
        hidden = hidden or collection.hide_render
        if hidden:
            return
        for obj in collection.objects:
            if (obj.as_pointer() not in seen and obj.type == 'MESH' and not obj.hide_render
                    and obj.get('nrel_defect_role') not in EXCLUDED_ROLES):
                objects.append(obj)
                seen.add(obj.as_pointer())
        for child in collection.children:
            walk(child, hidden)
    walk(scene.collection)
    return objects


def scene_tree(rig):
    import bpy
    from mathutils.bvhtree import BVHTree
    deps = bpy.context.evaluated_depsgraph_get()
    vertices, faces = [], []
    for obj in _renderable_objects(rig['scene']):
        evaluated = obj.evaluated_get(deps)
        mesh = evaluated.to_mesh()
        try:
            offset = len(vertices)
            vertices.extend(evaluated.matrix_world @ v.co for v in mesh.vertices)
            mesh.calc_loop_triangles()
            faces.extend(tuple(offset + i for i in tri.vertices) for tri in mesh.loop_triangles)
        finally:
            evaluated.to_mesh_clear()
    return BVHTree.FromPolygons(vertices, faces, all_triangles=True) if faces else None


def _camera_descriptor(camera, scene):
    if camera.data.type != 'PERSP':
        raise ValueError('Only ideal perspective cameras are supported')
    width = round(scene.render.resolution_x * scene.render.resolution_percentage / 100)
    height = round(scene.render.resolution_y * scene.render.resolution_percentage / 100)
    frame = camera.data.view_frame(scene=scene)
    xs, ys = [v.x / -v.z for v in frame], [v.y / -v.z for v in frame]
    fx, fy = width / (max(xs) - min(xs)), height / (max(ys) - min(ys))
    rotation = camera.matrix_world.to_3x3()
    from mathutils import Vector
    axes = [tuple((rotation @ Vector(axis)).normalized()) for axis in ((1, 0, 0), (0, 1, 0), (0, 0, -1))]
    return dict(origin=tuple(camera.matrix_world.translation), axes=axes, fx=fx, fy=fy,
                cx=-0.5 - min(xs) * fx, cy=-0.5 + max(ys) * fy,
                width=width, height=height, near=camera.data.clip_start, far=camera.data.clip_end)
