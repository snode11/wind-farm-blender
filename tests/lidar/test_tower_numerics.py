import json
import numpy as np
import pytest
from scripts.lidar import compare_tower_numerics as audit


def test_metrics_known_difference_and_nonfinite():
    result = audit.metrics([1., 2.], [1.03, 2.04])
    assert result['rms_difference'] == pytest.approx(np.sqrt(.00125))
    assert result['max_absolute_difference'] == pytest.approx(.04)
    with pytest.raises(ValueError, match='Invalid'):
        audit.metrics([1., 2.], [1., np.nan])


def test_vector_tower_limit_and_input_identity(tmp_path, monkeypatch):
    names = ['YawBrTDxt', 'YawBrTDyt', 'YawBrTDzt', 'YawBrRDxt', 'YawBrRDyt', 'YawBrRDzt',
             'TTDspFA', 'TTDspSS', 'RotSpeed', 'GenPwr', 'YawPzn', 'Azimuth']
    names += [f'{p}{i}' for p in ('OoPDefl', 'IPDefl', 'BlPitch') for i in (1, 2, 3)]
    probes = []
    for label in ('base', 'refined'):
        case = tmp_path/label; (case/'FarmInputs').mkdir(parents=True)
        for tid in ('T1', 'T2', 'T3'):
            (case/'FarmInputs'/f'Case.{tid}.out').write_text('fixture')
        report = dict(status='CONTROL_PROBE_COMPLETE', tower='flexible', case_dir=str(case),
                      wind_sha256='same', controller_sha256='same', control_period_s=3,
                      policy={'sha256':'same'}, records=[{'bounded_delta_deg':[0,0,0]}])
        path = case/'probe.json'; path.write_text(json.dumps(report)); probes.append(path)

    def source(path):
        data = {key: np.ones(2401) for key in names}
        data['Time'] = np.linspace(117,177,2401)
        if 'refined' in path.parts:
            # Each axis is below 1 cm, but the 3D norm exceeds the RMS limit.
            for key in ('YawBrTDxt', 'YawBrTDyt', 'YawBrTDzt'):
                data[key] += .009
        return data

    monkeypatch.setattr(audit, 'read_fast_out', source)
    result = audit.compare_runs(*probes)
    assert not result['passed']
    assert result['turbines'][0]['checks']['tower_max']
    assert not result['turbines'][0]['checks']['tower_rms']
    report['wind_sha256'] = 'different'
    probes[1].write_text(json.dumps(report))
    with pytest.raises(ValueError, match='inputs'):
        audit.compare_runs(*probes)
