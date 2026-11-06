"""Only source-bound settings require applying; display and next-sample settings stay independent."""
from types import SimpleNamespace
import numpy as np
import pytest
from wfrl_blender.radar_x100 import configuration


def scene(mode):
    values = dict(source=mode, observation_mode='GEOMETRY', mount='NACELLE', location=(1.,2.,3.), rotation=(0.,90.,0.),
        path='//radar.ply', ply_source='TEST', port=9877, stale_s=.5, stream='NEAR',
        candidates=512, color='RANGE', color_scale='FIXED', velocity_limit=10., fov=True,
        replay_s=0., record_path='/tmp/record')
    return SimpleNamespace(**{'wfrl_x100_'+key: value for key, value in values.items()})


def abspath(path):
    return path.replace('//', '/scene/')


@pytest.mark.parametrize('mode', configuration.SOURCE_LABELS)
def test_immediate_and_next_sample_settings_do_not_report_pending_configuration(mode):
    s = scene(mode)
    applied = configuration.capture(s, abspath=abspath, single_ply=mode == 'REPLAY')
    for key, value in dict(stream='FAR', candidates=4096, color='SNR', color_scale='FRAME',
            velocity_limit=20., fov=False, replay_s=1., record_path='/tmp/new-record').items():
        setattr(s, 'wfrl_x100_'+key, value)
        assert configuration.pending(s, applied, abspath=abspath) == ()
    assert applied['source'] == mode and configuration.summary(applied)


@pytest.mark.parametrize('mode,field,value,label', [
    ('SIMULATION','observation_mode','DETECTIONS','观测模式'),
    ('SIMULATION','mount','WORLD','挂载'), ('SIMULATION','location',(1.,2.,4.),'安装位置'),
    ('SIMULATION','rotation',(0.,91.,0.),'安装旋转'), ('REPLAY','path','//other.ply','回放路径'),
    ('REPLAY','ply_source','MEASURED_REPLAY','单帧 PLY 来源'), ('LIVE','port',9878,'本地端口'),
    ('LIVE','stale_s',1.,'过期阈值')])
def test_pending_relevant_source_settings_clear_when_reverted_or_applied(mode, field, value, label):
    s = scene(mode)
    applied = configuration.capture(s, abspath=abspath, single_ply=mode == 'REPLAY')
    before = getattr(s, 'wfrl_x100_'+field)
    setattr(s, 'wfrl_x100_'+field, value)
    assert configuration.pending(s, applied, abspath=abspath) == (label,)
    current = configuration.capture(s, abspath=abspath, single_ply=mode == 'REPLAY')
    assert configuration.pending(s, current, abspath=abspath) == ()
    setattr(s, 'wfrl_x100_'+field, before)
    assert configuration.pending(s, applied, abspath=abspath) == ()


def test_recording_directories_ignore_single_ply_declarations_and_other_source_fields():
    s = scene('REPLAY')
    applied = configuration.capture(s, abspath=abspath)
    s.wfrl_x100_ply_source = 'UNDECLARED'
    s.wfrl_x100_observation_mode = 'DETECTIONS'
    s.wfrl_x100_location = (9.,8.,7.)
    s.wfrl_x100_port = 12345
    assert configuration.pending(s, applied, abspath=abspath) == ()
    s.wfrl_x100_path = '/scene/radar.ply'
    assert configuration.pending(s, applied, abspath=abspath) == ()
    s.wfrl_x100_source = 'LIVE'
    assert configuration.pending(s, applied, abspath=abspath) == ('数据源',)


def test_installation_catalog_audits_original_before_rear_candidate_without_moving_source(monkeypatch):
    from wfrl_blender.radar_x100 import scene as scene_api
    from wfrl_blender import turbine_geometry
    from wfrl.radar_x100 import installation
    lower, upper = np.array([-4.767865, -1.65, -1.5]), np.array([3.262135, 1.65, 1.8])
    vertices = np.array([lower, upper])
    validated = []
    sampler = SimpleNamespace(shell_vertices=vertices, shell_local=np.eye(4), geometry_id='geometry',
                              validate=lambda: validated.append(True))
    def matrix(location, rotation):
        roll, pitch, yaw = rotation
        assert roll == 0
        pitch, yaw = np.radians([pitch, yaw])
        cp, sp, cy, sy = np.cos(pitch), np.sin(pitch), np.cos(yaw), np.sin(yaw)
        result = np.eye(4)
        result[:3, :3] = [[cy*cp, -sy, cy*sp], [sy*cp, cy, sy*sp], [-sp, 0, cp]]
        result[:3, 3] = location
        return result
    monkeypatch.setattr(scene_api, 'installation_matrix', matrix)
    monkeypatch.setattr(scene_api, 'nacelle_side_installation', lambda sampler: ((-1., -1.72, .15), (0., 0., -100.)))
    monkeypatch.setattr(turbine_geometry, 'hub_position', lambda: (-5., 0., 90.))
    result = scene_api.installation_catalog(sampler)
    assert result['original_assessment']['front_half']
    assert result['original_assessment']['angle_from_down_deg'] == pytest.approx(7.5)
    assert result['baselines'][0] == ('ORIGINAL', scene_api.ORIGINAL_LOCATION, scene_api.ORIGINAL_ROTATION)
    name, location, rotation = result['baselines'][-1]
    assert name == 'NACELLE_REAR_DOWN' and rotation == (0., 90., 180.)
    policy = installation.MechanicalPolicy()
    assert location[0] == pytest.approx(upper[0] - policy.rear_inset_m)
    assert location[2] == pytest.approx(lower[2] - policy.underside_drop_m)
    assert location[0] > result['original_assessment']['shell_midpoint_x_m']
    np.testing.assert_array_equal(vertices, [lower, upper])
    assert validated == [True]
