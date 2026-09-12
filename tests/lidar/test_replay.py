import unittest
from wfrl.lidar.replay import ReplayPackage, ReplayReader, precompute
from wfrl.lidar.evidence import CONTRACT, ASSESSMENT


def numerical_evidence():
    """Small explicit numerical fixture, not a claim about solver results."""
    comparison = dict(paired_samples=1, max_truth_difference_m=.01,
        mean_truth_difference_m=.005, max_paired_slant_difference_m=.02,
        baseline_dt_s=.01, refined_dt_s=.01, baseline_fps=10, refined_fps=10)
    space = dict(comparison, kind='span_only_19_to_37', base_run='unit-test-only',
        refined_run='fixture-space', baseline_span_sections=19, refined_span_sections=37)
    time = dict(comparison, kind='integration_and_output_timestep_halved', base_run='fixture-space',
        refined_run='fixture-time', baseline_span_sections=37, refined_span_sections=37,
        refined_dt_s=.005, refined_fps=20)
    from wfrl.lidar.sampling import compare_grids
    base = dict(motion=[{'time_s':0}, {'time_s':10}], measurements=[dict(
        time_s=1, expected=True, passage_id='p1', beams={'B2':dict(valid=True,error_m=.1)})])
    sampling = compare_grids(base, base, 10, 20)
    sampling.update(base_run='fixture-space', refined_run='fixture-time')
    return dict(run_id='unit-test-only', analytic=dict(analytic_truth_error_m=0,
        rigid_formula_error_m=0,circle_polygon_convergence=[dict(vertices=32,distance_m=2.01,error_m=.01),
        dict(vertices=64,distance_m=2.001,error_m=.001)]), spatial=space, temporal=time,
        sampling=sampling, truth_numerical_error_m=.01)

class ReplayTests(unittest.TestCase):
    def fixture(self):
        motion=[dict(time_s=t,azimuth_deg=359+2*t,yaw_deg=0,pitch_deg=[0]*3,rotor_speed_rpm=0) for t in (0,10)]
        config=dict(threshold_m=5,hysteresis_m=.2,max_hold_s=2,passage_margin=1.2)
        rows=[]
        for t,v in [(1,4.9),(2,5.1),(3,5.3),(4,None),(8,5.1)]:
            rows.append(dict(time_s=t,blade_id=1,expected=True,passage_id=str(t),truth_m=5,
                beams={'B2':dict(valid=v is not None,estimate_m=v,error_m=v-5 if v else None)}))
        cumulative,stats=precompute(rows,motion,config)
        return ReplayReader(ReplayPackage({'segment':{'start_s':0,'end_s':10}},motion,rows,cumulative,stats))
    def test_seek_expiry_hysteresis(self):
        r=self.fixture()
        self.assertIsNone(r.at(0)['measurement'])
        self.assertEqual(r.at(2.5)['status'],'near_threshold')
        self.assertEqual(r.at(3)['status'],'above_threshold')
        self.assertEqual(r.at(4)['measurement']['time_s'],3)
        self.assertEqual(r.at(5.1)['status'],'waiting')
        self.assertEqual(r.at(8)['status'],'above_threshold')
        self.assertEqual(r.at(2.5)['statistics']['valid_samples'],2)
        self.assertEqual(r.at(.5)['motion']['azimuth_deg'],360)
        self.assertEqual(r.at(10)['statistics']['valid_ratio'],.8)
        self.assertEqual(r.at(10)['statistics']['missed_passage_count'],1)
    def test_freeze_and_rewind(self):
        r=self.fixture(); initial=r.at(2)
        r.at(9); r.at(0)
        self.assertEqual(initial,r.at(2))
    def test_missing_package(self):
        with self.assertRaisesRegex(ValueError,'未就绪'):
            ReplayPackage.load('/this/path/does/not/exist')
    def test_empty_stats(self):
        r=self.fixture(); stats=r.at(0)['statistics']
        self.assertIsNone(stats['valid_ratio']); self.assertIsNone(stats['mae_m'])

class PackageTests(unittest.TestCase):
    def fixture(self):
        r=ReplayTests().fixture(); rows=r.package.measurements
        for row in rows:
            for name in ('B1','B3'):
                row['beams'][name]=dict(valid=False,reason='not evaluated',estimate_m=None,error_m=None)
            beam=row['beams']['B2']; beam.update(slant_range_m=20,reason='miss' if not beam['valid'] else '',hit_point_m=[0,0,0])
            row.update(truth_tip_point_m=[5,0,0],truth_wall_point_m=[0,0,0])
        for motion in r.package.motion:
            motion.update(nacelle_position_m=[0,0,90],nacelle_orientation_deg=[0,0,0])
        m=dict(schema_version='1.0',status='READY',source='FAST.Farm',run_id='unit-test-only',turbine_id='T1',
            units=dict(time='s',distance='m',angle='deg'),coordinate_system='fixture',model={'fixture':True},
            flexibility=dict(blade_enabled=True,reconstruction_validated=True,tower_assumption='rigid'),
            controller={'fixture':True},calibration={'fixture':True},algorithm={'version':'fixture'},postprocess_version='fixture',
            raw_sources=[dict(path='test-only',sha256='a'*64)],original_time_range_s=[0,10],motion_azimuth='unwrapped_deg',
            segment=dict(id='normal',start_s=0,end_s=10,selection_basis='unit test'),
            validation=dict(evidence_contract=CONTRACT,analytic_verified=True,spatial_comparison_completed=True,
                temporal_comparison_completed=True,convergence_assessment=ASSESSMENT,
                numerical_evidence=numerical_evidence(),collision_excluded=True,truth_numerical_error_m=.01),
            replay=dict(threshold_m=5,hysteresis_m=.2,max_hold_s=2,passage_margin=1.2))
        return m,r.package.motion,rows
    def test_publish_and_corruption(self):
        import tempfile
        from pathlib import Path
        from wfrl.lidar.replay import publish_package
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'result'; m,motion,rows=self.fixture()
            publish_package(path,m,motion,rows,'unit test fixture only')
            self.assertEqual(ReplayPackage.load(path).statistics['valid_samples'],4)
            (path/'motion.json').write_text('[]')
            with self.assertRaisesRegex(ValueError,'integrity'):
                ReplayPackage.load(path)
    def test_reject_incomplete(self):
        from wfrl.lidar.replay import _validate
        m,motion,rows=self.fixture()
        m['validation']['temporal_comparison_completed']=False
        with self.assertRaisesRegex(ValueError,'temporal'):
            _validate(m,motion,rows)
        m['validation']['temporal_comparison_completed']=True
        with self.assertRaisesRegex(ValueError,'no valid B2'):
            _validate(m,motion,[])
        rows[0]['beams']['B2']['error_m']=99
        with self.assertRaisesRegex(ValueError,'signed error'):
            _validate(m,motion,rows)
    def test_incremental_stats_matches_reference(self):
        from wfrl.lidar.replay import _stats
        m,motion,rows=self.fixture()
        cumulative,_=precompute(rows,motion,m['replay'])
        for i,row in enumerate(cumulative):
            self.assertEqual(row['statistics'],_stats(rows[:i+1]))

    def test_wrong_version_and_nonfinite_rejected(self):
        from wfrl.lidar.replay import _validate
        m,motion,rows=self.fixture()
        m['schema_version']='2.0'
        with self.assertRaisesRegex(ValueError,'version'):
            _validate(m,motion,rows)
        m['schema_version']='1.0'; motion[0]['yaw_deg']=float('nan')
        with self.assertRaisesRegex(ValueError,'non-finite'):
            _validate(m,motion,rows)
    def test_yaw_short_path(self):
        m,motion,rows=self.fixture()
        motion[0]['yaw_deg']=359; motion[1]['yaw_deg']=1
        cumulative,stats=precompute(rows,motion,m['replay'])
        reader=ReplayReader(ReplayPackage(m,motion,rows,cumulative,stats))
        self.assertEqual(reader.at(5)['motion']['yaw_deg'],360)
    def test_reject_rehashed_wrong_statistics(self):
        import tempfile, json, hashlib
        from pathlib import Path
        from wfrl.lidar.replay import publish_package
        with tempfile.TemporaryDirectory() as root:
            m,motion,rows=self.fixture(); path=Path(root)/'result'
            publish_package(path,m,motion,rows,'fixture only')
            raw=json.dumps({'valid_ratio':1}).encode()
            (path/'statistics.json').write_bytes(raw)
            manifest=json.loads((path/'manifest.json').read_text())
            manifest['files']['statistics.json']=hashlib.sha256(raw).hexdigest()
            (path/'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError,'precomputed'):
                ReplayPackage.load(path)

    def test_malformed_json_types_give_valueerror(self):
        import tempfile, json, hashlib
        from pathlib import Path
        from wfrl.lidar.replay import publish_package
        variants=[('manifest.json',None),('manifest.json',[]),('manifest.json','bad'),
                  ('motion.json',None),('motion.json',[None]),('measurements.json',[None]),
                  ('measurements.json',[{'time_s':1,'blade_id':[]}])]
        for name,value in variants:
            with self.subTest(name=name,value=value), tempfile.TemporaryDirectory() as root:
                m,motion,rows=self.fixture(); path=Path(root)/'result'
                publish_package(path,m,motion,rows,'fixture only')
                raw=json.dumps(value).encode(); (path/name).write_bytes(raw)
                if name!='manifest.json':
                    manifest=json.loads((path/'manifest.json').read_text())
                    manifest['files'][name]=hashlib.sha256(raw).hexdigest()
                    (path/'manifest.json').write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):
                    ReplayPackage.load(path)

    def test_caller_mutation_cannot_change_replay(self):
        reader=ReplayTests().fixture()
        before=reader.at(10); snapshot=reader.at(10)
        snapshot['motion']['pitch_deg'][0]=999
        snapshot['statistics']['valid_samples']=999
        snapshot['motion']['yaw_deg']=999
        self.assertEqual(reader.at(10),before)
        before=reader.at(3); snapshot=reader.at(3)
        snapshot['measurement']['truth_m']=999
        self.assertEqual(reader.at(3),before)
