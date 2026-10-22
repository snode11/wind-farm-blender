"""Run with Blender --background --factory-startup --python-exit-code 1."""
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from wfrl_blender.materials import PALETTE, get_material


def emission(material):
    return next(node for node in material.node_tree.nodes if node.type == 'EMISSION')


def main():
    cinematic = get_material('cinematic_line')
    scientific = get_material('wake_line')
    pulse = get_material('wake_pulse')
    for actual, expected in zip(emission(cinematic).inputs['Color'].default_value, PALETTE['cinematic_line']):
        assert math.isclose(actual, expected, abs_tol=1e-6)
    assert math.isclose(emission(cinematic).inputs['Strength'].default_value, .28, abs_tol=1e-6)
    assert math.isclose(cinematic.node_tree.nodes['WFRL.DownstreamFade'].inputs['To Min'].default_value, .72, abs_tol=1e-6)
    assert tuple(emission(cinematic).inputs['Color'].default_value) != tuple(emission(scientific).inputs['Color'].default_value)
    assert math.isclose(emission(scientific).inputs['Strength'].default_value, 1.1, abs_tol=1e-6)
    assert emission(pulse).inputs['Strength'].default_value == 4
    for name in ('tower', 'blade', 'nacelle', 'hub', 'foundation', 'cinematic_line', 'wake_line', 'wake_pulse'):
        material = get_material(name)
        counts = (len(material.node_tree.nodes), len(material.node_tree.links))
        for _ in range(20):
            assert get_material(name) == material
            assert (len(material.node_tree.nodes), len(material.node_tree.links)) == counts, name
        if name in ('tower', 'blade', 'nacelle', 'hub', 'foundation'):
            principled = next(node for node in material.node_tree.nodes if node.type == 'BSDF_PRINCIPLED')
            assert principled.inputs['Normal'].links[0].from_node == material.node_tree.nodes['WFRL.MicroBump']
    print('WFRL_MATERIAL_REUSE_SMOKE=PASS')


if __name__ == '__main__':
    main()
