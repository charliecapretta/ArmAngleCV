import math
import unittest
from armangle.releaseTiming import flightSeconds, timeAtY, releaseFrameFromArrival, speedOnlyFlightSeconds

class TimingTest(unittest.TestCase):
    def test_constant_speed_matches_distance(self):
        self.assertAlmostEqual(flightSeconds({'release_pos_y':54,'vy0':-132,'ay':0},0),54/132)
    def test_acceleration_solution_reconstructs_each_plane(self):
        for y in [54,50,17/12,0]:
            t=timeAtY(y,-130,28)
            self.assertAlmostEqual(50-130*t+14*t*t,y)
    def test_extension_fallback_and_plate_reference(self):
        r={'release_extension':6.5,'vy0':-130,'ay':28}
        self.assertGreater(flightSeconds(r,0),flightSeconds(r))
        self.assertAlmostEqual(flightSeconds(r),flightSeconds(dict(r,release_pos_y=54)))
    def test_invalid_trajectories_abstain_with_error(self):
        for r in [{},{'release_pos_y':54,'vy0':130,'ay':28},{'release_pos_y':54,'vy0':-1,'ay':28},{'release_pos_y':math.nan,'vy0':-130,'ay':28}]:
            with self.assertRaises(ValueError):flightSeconds(r)
    def test_timestamp_mapping_is_not_average_fps(self):
        self.assertEqual(releaseFrameFromArrival(.45,.35,[0,.033,.069,.1,.135]),3)
        self.assertIsNone(releaseFrameFromArrival(.1,.35,[0,.033,.069]))
        with self.assertRaises(ValueError):releaseFrameFromArrival(.45,.35,[0,.1,.09])
    def test_legacy_slowdown_increases_flight_duration(self):
        self.assertGreater(speedOnlyFlightSeconds(90,6,.92,0),speedOnlyFlightSeconds(90,6,.96))
