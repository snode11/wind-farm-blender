from copy import deepcopy
import json
from pathlib import Path
import pytest

from wfrl.lidar.molas_cl import DPDecoder, SPEC, manual_clearance
from wfrl.lidar.dual_beam import reconstruct


def packet(index=0,bus=0):
    return dict(Data_Index=index,BUS_Index=bus,Data_Valid=7,System_Status=1,Lidar_fault=0,
        Laser_Distance_1=6000,Laser_Distance_2=6100,Laser_Distance_3=6200,
        Laser_Intensity_1=200,Laser_Intensity_2=200,Laser_Intensity_3=200,Laser_Distance_4=450)


def test_manual_units_and_no_invented_clearance_accuracy():
    assert SPEC['beam_relative_angles_deg']==[0,2.05,4.09]
    assert SPEC['final_clearance_accuracy_m'] is None
    assert SPEC['end_to_end_alarm_latency_s'] is None
    assert manual_clearance(10,30,2,1)==pytest.approx(6)
    r=DPDecoder().decode(packet(),1.)
    assert r['observations']['S1']['slant_range_m']==60.
    assert r['device_clearance_m']==4.5
    assert not r['device_clearance_is_independent_truth']


def test_s1_independent_of_s2_s3_and_unknown_blade_identity():
    p=packet();p.update(Data_Valid=1,Laser_Distance_2=65535,Laser_Distance_3=65535)
    r=DPDecoder().decode(p,1.)
    assert r['s1_state']=='triggered'
    assert r['s1_available_at_receiver_s']==1.
    assert r['observations']['S1']['blade_id'] is None
    assert not r['pairing_allowed']
    # A packet timestamp cannot make independently averaged ranges simultaneous.
    a=deepcopy(r['observations']['S1']);a.update(time_s=1.,blade_id=1,valid=True,first_object='blade')
    b=deepcopy(a)
    estimate=reconstruct(a,b,(0,0,90),63,lambda p:1,time_s=1.)
    assert estimate['reason']=='not_instantaneous_observations'
    assert estimate['clearance_estimate'] is None


@pytest.mark.parametrize('updates,reason',[
    ({'Laser_Distance_1':65535},'invalid_sentinel'),
    ({'Data_Valid':15},'visibility_invalid'),
    ({'System_Status':0},'system_unavailable'),
    ({'System_Status':2},'system_unavailable'),
    ({'System_Status':3},'system_unavailable'),
    ({'Lidar_fault':1<<15},'system_unavailable'),
    ({'Laser_Intensity_1':9},'inconsistent_low_intensity'),
])
def test_faults_and_sentinel_never_turn_into_valid_clearance(updates,reason):
    p=packet();p.update(updates)
    r=DPDecoder().decode(p,1.)
    assert r['s1_state']=='unknown'
    assert r['observations']['S1']['reason']==reason
    assert r['observations']['S1']['slant_range_m'] is None


def test_low_clearance_warning_does_not_suppress_s1():
    p=packet();p['Lidar_fault']=1<<2
    assert DPDecoder().decode(p,1.)['s1_state']=='triggered'


@pytest.mark.parametrize('raw',[10000,65535])
def test_clearance_outside_detection_is_not_a_large_safe_clearance(raw):
    p=packet();p['Laser_Distance_4']=raw
    r=DPDecoder().decode(p,1.)
    assert r['device_clearance_m'] is None
    assert r['device_clearance_raw_code']==raw


def test_data_and_bus_heartbeats_are_independent_and_wrap():
    d=DPDecoder()
    assert d.decode(packet(255,1),1.)['fresh']
    duplicate=d.decode(packet(255,2),1.02)
    assert duplicate['bus_progressed'] and not duplicate['fresh']
    assert duplicate['s1_state']=='unknown'
    wrapped=d.decode(packet(0,3),1.04)
    assert wrapped['fresh'] and wrapped['data_index_delta']==1
    assert not d.decode(packet(1,4),10.)['fresh']
    with pytest.raises(ValueError):d.decode(packet(),9.)


def test_ground_is_not_blade_and_bad_units_are_rejected():
    p=packet();p['Data_Valid']=0
    r=DPDecoder().decode(p,1.)
    assert r['s1_state']=='not_triggered'
    assert r['observations']['S1']['slant_range_m']==60
    p['Laser_Distance_1']=60.2
    with pytest.raises(ValueError):DPDecoder().decode(p,2.)


def test_manual_angles_never_replace_user_confirmed_installation():
    root=Path(__file__).resolve().parents[2]
    original=json.loads((root/'configs/lidar/dual-beam-inner.json').read_text())
    active=json.loads((root/'configs/lidar/dual-beam-inner-manual-contract.json').read_text())
    assert active['angles_deg']==original['angles_deg']==[10.,12.,14.]
    assert active['origins_m']==original['origins_m']
    assert active['manual_profile']['manual_nominal_relative_angles_deg']==[0.,2.05,4.09]
    assert active['manual_profile']['manual_angles_are_not_active_layout']
