"""Manual pose contracts, presets and angle adjustments."""

import json
import math
import unittest

from wfrl_blender.manual_pose import (
    POSE_FIELDS, POSE_SCHEMA, adjusted_angle, decode_poses, encode_poses,
    pose_values, preset_changes,
)


def sample_pose(**changes):
    values = {
        'yaw_deg': 12.0, 'pitch_deg': 7.0, 'rpm': 9.0,
        'azimuth_deg': 123.0, 'individual_pitch': True,
        'pitch_b1_deg': 1.0, 'pitch_b2_deg': 2.0, 'pitch_b3_deg': 3.0,
    }
    values.update(changes)
    return values


class ManualPoseTests(unittest.TestCase):
    def test_round_trip_preserves_each_turbine_and_inactive_individual_pitch(self):
        poses = {
            'T3': sample_pose(yaw_deg=-90.0, individual_pitch=False),
            'T1': sample_pose(),
        }
        encoded = encode_poses(poses)
        self.assertEqual(json.loads(encoded)['schema'], POSE_SCHEMA)
        decoded = decode_poses(encoded)
        self.assertEqual(decoded, poses)
        self.assertEqual(list(decoded), ['T1', 'T3'])
        self.assertEqual(tuple(decoded['T1']), POSE_FIELDS)
        decoded['T1']['pitch_b1_deg'] = 10.0
        self.assertEqual(poses['T1']['pitch_b1_deg'], 1.0)

    def test_complete_pose_requires_exact_fields(self):
        invalid = [None, [], {}, sample_pose(unrecognized=0.0)]
        missing = sample_pose()
        missing.pop('pitch_b3_deg')
        invalid.append(missing)
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                pose_values(values)

    def test_numbers_and_booleans_are_strict(self):
        for field in POSE_FIELDS:
            if field == 'individual_pitch':
                invalid = (0, 1, 'true', None)
            else:
                invalid = (True, False, '1', None, math.nan, math.inf, -math.inf)
            for value in invalid:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    pose_values(sample_pose(**{field: value}))
        self.assertEqual(pose_values(sample_pose(rpm=0))['rpm'], 0.0)

    def test_pose_limits_are_inclusive_and_never_clamped(self):
        ranges = {
            'yaw_deg': (-180.0, 180.0), 'pitch_deg': (-30.0, 90.0),
            'rpm': (0.0, 30.0), 'azimuth_deg': (0.0, 360.0),
            'pitch_b1_deg': (-30.0, 90.0), 'pitch_b2_deg': (-30.0, 90.0),
            'pitch_b3_deg': (-30.0, 90.0),
        }
        for field, (lower, upper) in ranges.items():
            for value in (lower, upper):
                with self.subTest(field=field, value=value):
                    self.assertEqual(pose_values(sample_pose(**{field: value}))[field], value)
            for value in (lower - 0.01, upper + 0.01):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    pose_values(sample_pose(**{field: value}))

    def test_turbine_selection_requires_supported_nonempty_mapping(self):
        for poses in (None, [], {}, {'T4': sample_pose()}, {'T1': sample_pose(), 'T4': sample_pose()}):
            with self.subTest(poses=poses), self.assertRaises(ValueError):
                encode_poses(poses)
        for poses in ({'T2': sample_pose()}, {key: sample_pose() for key in ('T1', 'T2', 'T3')}):
            self.assertEqual(decode_poses(encode_poses(poses)), poses)

    def test_document_requires_exact_fields_and_schema(self):
        valid = json.loads(encode_poses({'T1': sample_pose()}))
        invalid = [None, [], {}, {'schema': POSE_SCHEMA}, {'turbines': valid['turbines']}]
        invalid.extend((dict(valid, extra=1), dict(valid, schema='wfrl.manual-pose.v2')))
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                decode_poses(json.dumps(payload))
        for turbines in (None, [], {}, {'T4': sample_pose()}):
            with self.subTest(turbines=turbines), self.assertRaises(ValueError):
                decode_poses(json.dumps(dict(valid, turbines=turbines)))

    def test_json_rejects_nonfinite_numbers_and_duplicate_keys_at_every_level(self):
        valid = encode_poses({'T1': sample_pose()})
        duplicate_documents = (
            '{"schema":"wfrl.manual-pose.v1",' + valid[1:],
            valid.replace('"T1": {', '"T1": {}, "T1": {'),
            valid.replace('"yaw_deg": 12.0', '"yaw_deg": 12.0, "yaw_deg": 13.0'),
        )
        for text in duplicate_documents:
            with self.subTest(text=text), self.assertRaises(ValueError):
                decode_poses(text)
        for number in ('NaN', 'Infinity', '-Infinity', '1e9999'):
            with self.subTest(number=number), self.assertRaises(ValueError):
                decode_poses(valid.replace('"yaw_deg": 12.0', f'"yaw_deg": {number}'))
        for text in ('{', '', b'{}', None):
            with self.subTest(text=text), self.assertRaises(ValueError):
                decode_poses(text)

    def test_presets_change_only_their_expected_fields(self):
        expected = {
            'BLADE_DOWN': {'azimuth_deg': 180.0},
            'BLADE_SIDE': {'azimuth_deg': 90.0},
            'FEATHER': {
                'pitch_deg': 90.0, 'individual_pitch': False,
                'pitch_b1_deg': 90.0, 'pitch_b2_deg': 90.0, 'pitch_b3_deg': 90.0,
            },
        }
        for kind, changes in expected.items():
            with self.subTest(kind=kind):
                self.assertEqual(preset_changes(kind), changes)
                merged = sample_pose()
                merged.update(preset_changes(kind))
                self.assertEqual(merged['yaw_deg'], 12.0)
                self.assertEqual(merged['rpm'], 9.0)
                pose_values(merged)
        first = preset_changes('FEATHER')
        first['pitch_deg'] = 0.0
        self.assertEqual(preset_changes('FEATHER')['pitch_deg'], 90.0)
        with self.assertRaises(ValueError):
            preset_changes('UNKNOWN')

    def test_angle_adjustments_clamp_pitch_and_yaw_and_wrap_phase(self):
        self.assertEqual(adjusted_angle('yaw_deg', 179.0, 5.0), 180.0)
        self.assertEqual(adjusted_angle('yaw_deg', -179.0, -5.0), -180.0)
        for field in ('pitch_deg', 'pitch_b1_deg', 'pitch_b2_deg', 'pitch_b3_deg'):
            with self.subTest(field=field):
                self.assertEqual(adjusted_angle(field, 88.0, 5.0), 90.0)
                self.assertEqual(adjusted_angle(field, -29.0, -5.0), -30.0)
                self.assertEqual(adjusted_angle(field, 10.0, 0.1), 10.1)
        self.assertEqual(adjusted_angle('azimuth_deg', 359.0, 5.0), 4.0)
        self.assertEqual(adjusted_angle('azimuth_deg', 0.0, -5.0), 355.0)
        self.assertEqual(adjusted_angle('azimuth_deg', 360.0, 0.0), 0.0)
        self.assertEqual(adjusted_angle('azimuth_deg', 20.0, 720.0), 20.0)
        self.assertTrue(math.isfinite(adjusted_angle('azimuth_deg', 1e308, 1e308)))

    def test_angle_adjustments_reject_nonangles_and_invalid_numbers(self):
        for field in ('rpm', 'individual_pitch', 'missing'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                adjusted_angle(field, 1.0, 1.0)
        for value in (True, '1', math.nan, math.inf, -math.inf):
            with self.subTest(value=value), self.assertRaises(ValueError):
                adjusted_angle('yaw_deg', value, 1.0)
            with self.subTest(delta=value), self.assertRaises(ValueError):
                adjusted_angle('azimuth_deg', 1.0, value)


if __name__ == '__main__':
    unittest.main()
