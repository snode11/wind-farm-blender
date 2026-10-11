import numpy as np
import pytest
from scipy import ndimage

from wfrl.nrel_reconstruction.boundary_metrics import boundary_metrics


def scene():
    labels=np.zeros((40,50),dtype=np.uint8)
    labels[10:30,15:35]=1
    return labels


def test_exact_visible_boundary_matches_and_shift_is_detected():
    labels=scene();prediction=labels==1
    exact=boundary_metrics(labels,prediction)
    assert exact["observed_to_candidate"]["mean_px"]==0
    assert exact["candidate_to_observed"]["mean_px"]==0
    shifted=np.roll(prediction,3,axis=1)
    changed=boundary_metrics(labels,shifted)
    assert changed["observed_to_candidate"]["p95_px"]==pytest.approx(3)
    assert changed["candidate_to_observed"]["mean_px"]>0


def test_legacy_uncertainty_ring_does_not_invent_a_boundary():
    labels=scene();fg=labels==1
    inner=ndimage.binary_erosion(fg,iterations=2)
    outer=ndimage.binary_dilation(fg,iterations=3)
    labels[outer&~inner]=2;labels[inner]=1
    result=boundary_metrics(labels,fg)
    assert result["direct_f_b_boundary_count"]==0
    assert result["status"]=="NULL_NO_COMPARABLE_TRUSTED_BOUNDARY"
    assert result["observed_to_candidate"]["mean_px"] is None
    assert result["candidate_to_observed"]["mean_px"] is None


def test_occlusion_edge_is_excluded_not_treated_as_shortening():
    labels=scene();prediction=labels==1
    labels[:,:20]=2
    result=boundary_metrics(labels,prediction)
    assert result["excluded_candidate_points"]>0
    assert result["observed_to_candidate"]["mean_px"]==0
    assert result["candidate_to_observed"]["mean_px"]==0


def test_shortening_inside_visible_foreground_is_detected():
    labels=scene();prediction=labels==1
    prediction[:,30:]=False
    result=boundary_metrics(labels,prediction)
    assert result["observed_to_candidate"]["p95_px"]>0
    assert result["candidate_to_observed"]["p95_px"]>0


def test_image_border_is_not_a_measured_contour():
    labels=np.ones((30,30),dtype=np.uint8)
    result=boundary_metrics(labels,np.ones_like(labels))
    assert result["observed_to_candidate"]["mean_px"] is None
    assert result["candidate_to_observed"]["mean_px"] is None


def test_explicit_rgb_contour_needs_a_separate_occlusion_domain():
    labels=np.full((20,20),2,dtype=np.uint8)
    observed=np.array([[5,5],[6,5],[7,5]],dtype=float)
    with pytest.raises(ValueError,match="candidate_valid_mask"):
        boundary_metrics(labels,observed_boundary_points=observed,predicted_boundary_points=observed)
    valid=np.ones_like(labels,dtype=bool)
    result=boundary_metrics(labels,observed_boundary_points=observed,predicted_boundary_points=observed,candidate_valid_mask=valid)
    assert result["observed_to_candidate"]["mean_px"]==0
    assert result["direct_f_b_boundary_count"]==0
    assert result["explicit_domain_used"]


def test_explicit_domains_filter_occlusion_and_keep_pixel_units():
    labels=np.zeros((20,20),dtype=np.uint8);valid=np.ones_like(labels,dtype=bool);valid[:,10:]=False
    observed=np.array([[5,5],[15,5]],dtype=float)
    predicted=np.array([[6,5],[15,5]],dtype=float)
    result=boundary_metrics(labels,observed_boundary_points=observed,predicted_boundary_points=predicted,candidate_valid_mask=valid,pixel_size_px=(4,4))
    assert result["excluded_observed_points"]==1
    assert result["excluded_candidate_points"]==1
    assert result["observed_to_candidate"]["mean_px"]==pytest.approx(4)


def test_empty_candidate_is_null_not_zero():
    result=boundary_metrics(scene(),np.zeros((40,50),dtype=bool))
    assert result["observed_to_candidate"]["mean_px"] is None
    assert result["observed_to_candidate"]["null_reason"]=="NO_VALID_CANDIDATE_BOUNDARY"


@pytest.mark.parametrize("value",[np.array([[float("nan"),2]]),np.array([1,2]),np.array([[1,2,3]])])
def test_invalid_explicit_contour_is_rejected(value):
    with pytest.raises(ValueError):
        boundary_metrics(scene(),observed_boundary_points=value,predicted_boundary_points=np.array([[1,2]]),candidate_valid_mask=np.ones((40,50),dtype=bool))


def test_invalid_candidate_domain_is_not_coerced_to_true():
    valid=np.ones((40,50),dtype=float);valid[10,15]=np.nan
    with pytest.raises(ValueError,match="finite HxW binary"):
        boundary_metrics(scene(),scene()==1,candidate_valid_mask=valid)


def rgb_fixture(tmp_path, *, explicit=True, uncertain_ring=False):
    from PIL import Image
    from wfrl.nrel_reconstruction.boundary_metrics import _binary_boundary
    vertices=np.array([[-1,-1,4],[1,-1,4],[1,1,4],[-1,1,4]],dtype=float)
    model=tmp_path/'frozen.ply'
    model.write_text('ply\nformat ascii 1.0\nelement vertex 4\nproperty float x\nproperty float y\nproperty float z\nelement face 2\nproperty list uchar int vertex_indices\nend_header\n'+
                     '\n'.join(' '.join(map(str,vertex)) for vertex in vertices)+'\n3 0 1 2\n3 0 2 3\n')
    raw=np.zeros((32,32),dtype=bool);raw[8:24,8:24]=True
    labels=raw.astype(np.uint8)
    if uncertain_ring:
        inner=ndimage.binary_erosion(raw,iterations=2)
        outer=ndimage.binary_dilation(raw,iterations=3)
        labels[outer&~inner]=2;labels[inner]=1
    label_path=tmp_path/'labels.png';Image.fromarray(labels).save(label_path)
    observation={'camera_id':'C2','frame_id':44,'sim_time_s':2.2,'split':'heldout',
                 'labels_path':str(label_path),'K':[[32,0,15.5],[0,32,15.5],[0,0,1]],
                 'T_camera_cv_from_world':np.eye(4).tolist(),'T_world_from_blade_root':np.eye(4).tolist()}
    if explicit:
        y,x=np.nonzero(_binary_boundary(raw))
        point_path=tmp_path/'rgb_contour.npy';np.save(point_path,np.column_stack((x,y)))
        valid_path=tmp_path/'contour_valid.png';Image.fromarray(np.full(raw.shape,255,dtype=np.uint8)).save(valid_path)
        observation.update(contour_points_path=str(point_path),contour_valid_path=str(valid_path))
    return model,observation


def test_frozen_rgb_wrapper_real_projection_scaling_and_nonblind_status(tmp_path):
    from wfrl.nrel_reconstruction.compare_rgb import compare_rgb
    model,observation=rgb_fixture(tmp_path)
    report=compare_rgb({'initial':model,'v2':model},[observation],tmp_path/'out',image_size=(16,16),reporting_size=(32,32))
    row=report['per_frame'][0]
    assert row['foreground_coverage_fraction']==1
    assert row['background_spill_fraction']==0
    assert row['masked_iou']==1
    assert row['validation_status']=='NONBLIND_TEMPORAL_VALIDATION'
    assert row['split']=='nonblind_temporal_validation'
    assert not report['validation_policy']['blind_claim']
    assert row['boundary']['explicit_domain_used']
    assert row['boundary']['pixel_size_px']==[2,2]
    assert 0<row['boundary']['observed_to_candidate']['mean_px']<1
    assert report['per_frame'][1]['boundary']==row['boundary']
    assert report['observation_asset_signatures']
    assert report['model_signatures']['v2']['sha256']==report['model_signatures']['initial']['sha256']
    aggregate=report['split_aggregates']['v2']['nonblind_temporal_validation']['per_camera']['C2']
    assert aggregate['metrics']['masked_iou']['mean']==1
    assert aggregate['metrics']['masked_iou']['missing_frame_count']==0
    doubled=compare_rgb({'v2':model},[observation],tmp_path/'out2',image_size=(16,16),reporting_size=(64,64))
    assert doubled['per_frame'][0]['boundary']['observed_to_candidate']['mean_px']==pytest.approx(2*row['boundary']['observed_to_candidate']['mean_px'])


def test_rgb_wrapper_keeps_legacy_boundary_null_and_reports_missing(tmp_path):
    from wfrl.nrel_reconstruction.compare_rgb import compare_rgb
    model,observation=rgb_fixture(tmp_path,explicit=False,uncertain_ring=True)
    report=compare_rgb({'initial':model},[observation],tmp_path/'out',image_size=(32,32),reporting_size=(32,32))
    row=report['per_frame'][0]
    assert row['boundary']['observed_to_candidate']['mean_px'] is None
    assert 'observed_to_candidate:NO_TRUSTED_OBSERVED_BOUNDARY' in row['missing_terms']
    metric=report['split_aggregates']['initial']['nonblind_temporal_validation']['all_cameras']['metrics']['boundary_observed_to_candidate_mean_px']
    assert metric['mean'] is None
    assert metric['valid_frame_count']==0
    assert metric['missing_frame_count']==1


def test_rgb_wrapper_requires_valid_contour_domain(tmp_path):
    from wfrl.nrel_reconstruction.compare_rgb import compare_rgb
    model,observation=rgb_fixture(tmp_path)
    observation.pop('contour_valid_path')
    with pytest.raises(ValueError,match='contour_valid_path'):
        compare_rgb({'v2':model},[observation],tmp_path/'out')


def test_rgb_wrapper_rejects_truth_paths_before_opening(tmp_path):
    from wfrl.nrel_reconstruction.compare_rgb import compare_rgb
    model,observation=rgb_fixture(tmp_path)
    with pytest.raises(ValueError,match='evaluation truth'):
        compare_rgb({'v2':tmp_path/'evaluation-only'/'not_created.ply'},[observation],tmp_path/'out')
    observation['labels_path']=str(tmp_path/'evaluation-only'/'not_created.png')
    with pytest.raises(ValueError,match='evaluation truth'):
        compare_rgb({'v2':model},[observation],tmp_path/'out')


def test_rgb_wrapper_missing_foreground_and_seen_training_frames(tmp_path):
    from PIL import Image
    from wfrl.nrel_reconstruction.compare_rgb import compare_rgb
    model,observation=rgb_fixture(tmp_path,explicit=False)
    Image.fromarray(np.zeros((32,32),dtype=np.uint8)).save(observation['labels_path'])
    observation['split']='fit'
    report=compare_rgb({'v2':model},[observation],tmp_path/'out',image_size=(16,16),reporting_size=(32,32))
    row=report['per_frame'][0]
    assert row['foreground_coverage_fraction'] is None
    assert 'NO_FOREGROUND_PIXELS' in row['missing_terms']
    assert row['masked_iou']==0
    assert row['validation_status']=='SEEN_TRAINING_FRAME'
    assert 'FRAME44_45_USED_IN_FIT_NOT_VALIDATION' in row['missing_terms']
