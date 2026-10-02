"""Nominal mounting diagram: radial geometry, honest status, no calibration edits."""
from copy import deepcopy
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from scripts.lidar.postprocess_dual_beam import installation

CONFIG = Path(__file__).resolve().parents[2] / 'configs/lidar/dual-beam-diagnostic.json'


@pytest.fixture
def config():
    return json.loads(CONFIG.read_text())


def run_installation(tmp_path, config):
    before = deepcopy(config)
    result = installation(tmp_path, config)
    assert config == before  # Diagnostics must never adjust origins/angles to fit the target.
    assert sorted(p.name for p in tmp_path.iterdir()) == ['installation-audit.json', 'installation.svg']
    assert json.loads((tmp_path / 'installation-audit.json').read_text()) == result
    return result


def test_centerline_preserves_old_side_projection_gap(tmp_path, config):
    result = run_installation(tmp_path, config)
    old_gap = (87.6 - 27.2) * math.tan(math.radians(2)) + 2.0 - 2.67
    assert result['nominal_gap_m'] == pytest.approx(old_gap, abs=1e-12)
    assert result['lateral_offset_m'] == 0
    assert result['simulation_mount_selected'] is False
    assert result['candidate_approved'] is False


@pytest.mark.parametrize('lateral', (-3., 3.))
def test_lateral_gap_uses_radial_distance(tmp_path, config, lateral):
    # Put the S1 intersection at (forward=4, lateral=3): radius is exactly 5 m.
    ray_shift = (87.6 - 27.2) * math.tan(math.radians(2))
    config['origins_m'] = [[-4. + ray_shift, lateral, 87.6] for _ in range(3)]
    result = run_installation(tmp_path, config)
    assert result['forward_projection_at_tip_m'] == pytest.approx(4.)
    assert result['nominal_gap_m'] == pytest.approx(5. - 2.67)
    assert result['nominal_gap_m'] != pytest.approx(4. - 2.67)
    # The reported target candidate must satisfy the circle equation as well.
    candidate_forward = result['candidate_offset_for_target_m'] + ray_shift
    assert candidate_forward**2 + lateral**2 == pytest.approx((4.5 + 2.67)**2)


def test_unreachable_target_is_serialized_as_null(tmp_path, config):
    config['origins_m'] = [[-2., 8., 87.6] for _ in range(3)]
    result = run_installation(tmp_path, config)
    assert result['candidate_offset_for_target_m'] is None
    assert result['nominal_gap_m'] > config['installation']['target_gap_m']
    assert math.isfinite(result['nominal_gap_m'])


def test_selected_diagram_describes_simulation_and_reference_target(tmp_path, config):
    config['status'] = 'SIMULATION_SELECTED'
    config['origins_m'] = [[-5.2, -3., 86.6] for _ in range(3)]
    result = run_installation(tmp_path, config)
    text = ' '.join(ET.parse(tmp_path / 'installation.svg').getroot().itertext())
    assert result['simulation_mount_selected'] is True
    assert result['candidate_approved'] is False
    assert 'Simulation mount selected' in text
    assert 'Old mount' not in text
    assert 'Reference target = 4.50 m' in text
    assert 'S1 radial wall gap' in text
    assert 'includes lateral offset' in text
    assert 'physical installation not certified' in text
    assert result['nominal_gap_m'] != pytest.approx(4.5)
