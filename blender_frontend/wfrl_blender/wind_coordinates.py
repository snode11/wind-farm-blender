"""World positions for wind-aligned FAST.Farm display data."""
import json
import math


def world_vertices(vertices, scene):
    """Invert the rigid XY case transform; leave Z and scalar samples unchanged."""
    transform = json.loads(scene.get('wfrl_wind_coordinates', '{}'))
    if not transform:
        return vertices
    matrix = transform['world_to_solver_matrix']
    offset = transform['translation_m']
    values = [v for row in matrix for v in row] + list(offset)
    if len(matrix) != 2 or any(len(row) != 2 for row in matrix) or len(offset) != 2 or not all(math.isfinite(v) for v in values):
        raise ValueError('Invalid solver-to-world XY transform')
    return [((x-offset[0])*matrix[0][0] + (y-offset[1])*matrix[1][0],
             (x-offset[0])*matrix[0][1] + (y-offset[1])*matrix[1][1], z)
            for x, y, z in vertices]
