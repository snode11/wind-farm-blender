import math
import unittest

from wfrl_blender.wake import (WakeFrameBuffer, WakePayloadError, grid_mesh,
                               proxy_ring_points, validate_wake_payload)


def channel(value, *, fidelity="SYNTH", provenance=None):
    return {
        "value": value, "unit": "m/s", "validity": "valid", "error": None,
        "fidelity": fidelity,
        "provenance": provenance or {"formula": "deterministic test"},
        "source_age_seconds": 0.0, "stale_after_seconds": 5.0,
    }


class WakePayloadTests(unittest.TestCase):
    def test_proxy_payload_preserves_synth_semantics(self):
        payload = {
            "encoding": "json", "turbine_ids": ["T1"],
            "timestamp": {"value": 2.0, "timebase": "simulation_seconds"},
            "data": channel({"kind": "floris_proxy", "shape": [2, 2],
                              "values": [8.0, 7.5, 7.0, 8.0], "x": [0, 10], "y": [0, 10], "z": 90.0}),
        }
        frame = validate_wake_payload(payload, sequence=4)
        self.assertEqual((frame.kind, frame.fidelity, frame.sequence), ("floris_proxy", "SYNTH", 4))
        self.assertTrue(frame.is_renderable)

    def test_disxy_grid_order_and_topology(self):
        payload = {
            "encoding": "json", "turbine_ids": ["T1", "T2"],
            "data": channel({"kind": "disxy", "fidelity": "EXPORTED",
                              "provenance": {"file": "case.Low.DisXY01.001.vtk"},
                              "shape": [2, 3], "values": [1, 2, 3, 4, 5, 6],
                              "x": [0, 10, 20], "y": [4, 14], "z": 90}),
        }
        frame = validate_wake_payload(payload)
        vertices, faces = grid_mesh(frame)
        self.assertEqual(vertices[0], (0.0, 4.0, 90.0))
        self.assertEqual(vertices[-1], (20.0, 14.0, 90.0))
        self.assertEqual(faces, ((0, 1, 4, 3), (1, 2, 5, 4)))

    def test_rejects_shape_mismatch_and_coalesces_old_frames(self):
        bad = {"encoding": "json", "turbine_ids": ["T1"], "data": channel({
            "kind": "disxy", "shape": [2, 2], "values": [1, 2, 3]})}
        with self.assertRaises(WakePayloadError):
            validate_wake_payload(bad)
        buf = WakeFrameBuffer()
        frame = validate_wake_payload({"encoding": "json", "turbine_ids": ["T1"],
            "data": channel({"shape": [1], "values": [8]})}, sequence=2)
        newer = validate_wake_payload({"encoding": "json", "turbine_ids": ["T1"],
            "data": channel({"shape": [1], "values": [7]})}, sequence=3)
        self.assertTrue(buf.push(frame)); self.assertTrue(buf.push(newer))
        self.assertEqual(buf.pop_latest().sequence, 3)
        self.assertEqual(buf.pending, 0)

    def test_proxy_points_are_finite_and_ring_count_is_stable(self):
        points = proxy_ring_points((0, 0, 90), 63, 270, 0.25, rings=4, segments=8)
        self.assertEqual(len(points), 32)
        self.assertTrue(all(math.isfinite(value) for point in points for value in point))


if __name__ == "__main__":
    unittest.main()



class WakePresentationMotionTests(__import__('unittest').TestCase):
    def test_ring_advects_downstream_and_retains_circular_cross_section(self):
        from wfrl_blender.wake import proxy_ring_points_local, wake_section
        a = list(proxy_ring_points_local(3, 96, 0))
        b = list(proxy_ring_points_local(3, 96, .5))
        self.assertGreater(b[0][0], a[0][0])
        cy, cz, radius = wake_section(3.5 / 22, .5)
        self.assertAlmostEqual(sum(p[2] for p in b) / len(b), cz, places=7)
        self.assertAlmostEqual(sum(p[1] for p in b) / len(b), cy, places=7)
        for p in b:
            self.assertAlmostEqual(p[0], b[0][0])
            self.assertAlmostEqual((p[1]-cy)**2+(p[2]-cz)**2, radius**2)

    def test_no_phase_boundary_jump_and_bounded_meander(self):
        from wfrl_blender.wake import proxy_ring_points_local, wake_section, proxy_pulse_points
        a = list(proxy_ring_points_local(4, 96, .999))
        b = list(proxy_ring_points_local(4, 96, 1.001))
        self.assertGreater(b[0][0],a[0][0])
        self.assertLess(b[0][0]-a[0][0],.1)
        for phase in (0,1,10,22,100):
            for u in (0,.25,.5,.75,1):
                cy,cz,r=wake_section(u,phase)
                self.assertLessEqual(abs(cy),4)
                self.assertLessEqual(abs(cz),2)
                self.assertLess(r,72)
            p=list(proxy_pulse_points(2,10,phase))
            self.assertTrue(all(a[0]<=b[0] for a,b in zip(p,p[1:])))
