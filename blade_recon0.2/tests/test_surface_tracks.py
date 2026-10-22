import sys
from pathlib import Path
import unittest
import tempfile
from unittest.mock import patch

import cv2
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from surface_tracks import track_sequence, TrackConfig, declared_input_path
from rgb_masks import known_observation_domain


def image(shift=(0,0), duplicate=False):
    rgb=np.full((160,220,3),160,np.uint8)
    x,y=90+shift[0],70+shift[1]
    cv2.rectangle(rgb,(x,y),(x+22,y+28),(45,45,45),-1)
    cv2.rectangle(rgb,(x+22,y+10),(x+30,y+19),(45,45,45),-1)
    if duplicate:
        cv2.rectangle(rgb,(x+55,y),(x+77,y+28),(45,45,45),-1)
        cv2.rectangle(rgb,(x+77,y+10),(x+85,y+19),(45,45,45),-1)
    return rgb


def record(frame,rgb,known=None):
    return {'frame':frame,'t':float(frame),'camera':'Inspection','rgb':rgb,
        'rgb_sha256':str(frame)*64,'known':np.ones(rgb.shape[:2],bool) if known is None else known,
        'occupancy':np.ones(rgb.shape[:2],bool)}


class SurfaceTrackTests(unittest.TestCase):
    def test_fixed_rgb_points_track_known_translation_subpixel(self):
        tracks,summary=track_sequence([record(1,image()),record(2,image((3,2)))])
        observed=[t for t in tracks if all(o['accept'] for o in t['observations'])]
        self.assertGreaterEqual(len(observed),3)
        self.assertEqual(summary['planned_observations'],len(tracks)*2)
        errors=[np.linalg.norm(np.asarray(t['observations'][1]['uv'])-t['anchor']['uv']-[3,2]) for t in observed]
        self.assertLess(max(errors),.15)

    def test_duplicate_rgb_pattern_rejects_identity(self):
        config=TrackConfig(sensor_roi_fraction_xyxy=(.1,.1,.95,.9))
        tracks,_=track_sequence([record(1,image(duplicate=True)),record(2,image((3,2),True))],config)
        self.assertTrue(tracks)
        self.assertTrue(all(not t['observations'][0]['accept'] for t in tracks))
        self.assertTrue(all(t['observations'][0]['status'] in
            ('AMBIGUOUS_RGB_IDENTITY','COMPETING_MATCH_PEAK') for t in tracks))

    def test_unknown_domain_stops_track_without_reacquisition(self):
        known=np.zeros((160,220),bool)
        tracks,_=track_sequence([record(1,image()),record(2,image((3,2)),known),record(3,image())])
        valid_anchor=[t for t in tracks if t['observations'][0]['accept']]
        self.assertTrue(valid_anchor)
        for t in valid_anchor:
            self.assertFalse(t['observations'][1]['accept'])
            self.assertEqual(t['observations'][1]['status'],'UNKNOWN_OR_UNMEASURED_RGB_DOMAIN')
            self.assertEqual(t['observations'][2]['status'],'IDENTITY_TERMINATED')
            self.assertIsNone(t['observations'][2]['uv'])

    def test_affine_reference_descriptor_checks_original_anchor_support(self):
        original=image()
        center=np.asarray([110.,80.]);angle=np.deg2rad(45.)
        linear=np.asarray([[np.cos(angle),-np.sin(angle)],
                           [np.sin(angle),np.cos(angle)]])
        matrix=np.eye(3);matrix[:2]=np.column_stack([linear,center-linear@center])
        target=cv2.warpAffine(original,matrix[:2],(220,160),flags=cv2.INTER_LINEAR,
            borderValue=(160,160,160))
        # Original 25px anchor descriptors fit on the known side of x=105.
        # After rotation, the target-grid reference descriptor reaches farther
        # into unknown *anchor* pixels; target support is still fully known.
        anchor_support=np.ones((160,220),bool);anchor_support[:,105:]=False
        for source_field in ('known','occupancy'):
            with self.subTest(source_field=source_field):
                rows=[record(1,original),record(2,target),record(3,target)]
                rows[0][source_field]=anchor_support
                with patch('surface_tracks._affine_seed',return_value=(matrix,{'status':'FIXTURE_RGB_AFFINE'})):
                    tracks,_=track_sequence(rows)
                anchors=[t for t in tracks if t['observations'][0]['accept']]
                self.assertTrue(anchors)
                rejected=[t for t in anchors if t['observations'][1]['status']==
                          'UNKNOWN_OR_UNMEASURED_ANCHOR_RGB_DOMAIN']
                self.assertTrue(rejected)
                for track in rejected:
                    obs=track['observations'][1]
                    self.assertTrue(obs['known'])
                    self.assertFalse(obs['quality']['reference_descriptor_known'])
                    self.assertFalse(obs['accept'])
                    self.assertEqual(track['observations'][2]['status'],'IDENTITY_TERMINATED')

    def test_unknown_regions_do_not_modify_observed_occupancy(self):
        occupancy=np.ones((40,50),bool)
        before=occupancy.copy()
        known=known_observation_domain(occupancy.shape,[[[10,10],[20,10],[20,25],[10,25]]])
        self.assertTrue(np.array_equal(occupancy,before))
        self.assertFalse(known[15,15])
        self.assertTrue(known[5,5])
        with self.assertRaises(ValueError):
            known_observation_domain(occupancy.shape,[[[-1,0],[2,3],[4,4]]])

    def test_camera_names_do_not_create_correspondence(self):
        a=record(1,image());b=record(2,image());b['camera']='Other'
        with self.assertRaises(ValueError):track_sequence([a,b])

    def test_fixed_anchor_rotation_shear_does_not_rebind_identity(self):
        original=image()
        center=np.asarray([110.,80.])
        transforms=[]
        for angle in (12.,28.):
            rad=np.deg2rad(angle)
            linear=np.asarray([[np.cos(rad),-np.sin(rad)],[np.sin(rad),np.cos(rad)]])
            linear=linear@np.asarray([[1.02,.03],[0.,.98]])
            transforms.append(np.column_stack([linear,center-linear@center]))
        records=[record(1,original)]+[record(j+2,cv2.warpAffine(original,matrix,(220,160),
            flags=cv2.INTER_LINEAR,borderValue=(160,160,160))) for j,matrix in enumerate(transforms)]
        tracks,summary=track_sequence(records)
        accepted=[t for t in tracks if all(o['accept'] for o in t['observations'])]
        self.assertGreaterEqual(len(accepted),3)
        errors=[]
        for t in accepted:
            p=np.r_[t['anchor']['uv'],1.]
            for index,matrix in enumerate(transforms):
                errors.append(np.linalg.norm(np.asarray(t['observations'][index+1]['uv'])-matrix@p))
        self.assertLess(max(errors),.35)
        self.assertEqual(summary['planned_observations'],len(tracks)*3)

    def test_declared_observation_paths_reject_escape_truth_and_wrong_role(self):
        with tempfile.TemporaryDirectory() as directory:
            parent=Path(directory);root=parent/'bundle';root.mkdir()
            (root/'RGB.png').write_bytes(b'RGB observation placeholder')
            (parent/'external.png').write_bytes(b'must not be read')
            (root/'escape.png').symlink_to(parent/'external.png')
            (root/'evaluation').mkdir();(root/'evaluation'/'truth.png').write_bytes(b'must not be read')
            (root/'hidden.png').symlink_to(root/'evaluation'/'truth.png')
            (root/'object_id.png').write_bytes(b'must not be read')
            (root/'dynamic-source.png').write_bytes(b'must not be read')
            self.assertEqual(declared_input_path(root,'RGB.png','RGB'),(root/'RGB.png').resolve())
            for declaration in ['../external.png','escape.png','evaluation/truth.png','hidden.png',
                                'object_id.png','dynamic-source.png',str(parent/'external.png')]:
                with self.assertRaises(ValueError):declared_input_path(root,declaration,'RGB')
            with self.assertRaises(ValueError):declared_input_path(root,'RGB.png','source_geometry')
            with self.assertRaises(ValueError):declared_input_path(root,'RGB.png','declared_RGB_unknown_domains')

if __name__=='__main__':unittest.main()
