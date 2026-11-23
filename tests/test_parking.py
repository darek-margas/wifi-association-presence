"""Parking logic tests: stdlib only, no Home Assistant needed.

Same tests as tesla-ble-fleet tests/test_parking.py (parking.py is shared).
"""

import importlib.util
import sys
import unittest
from pathlib import Path

# Load parking.py on its own: importing the package would pull in Home Assistant.
_PATH = Path(__file__).parent.parent / "custom_components" / "wifi_association_presence" / "parking.py"
_spec = importlib.util.spec_from_file_location("parking", _PATH)
parking = importlib.util.module_from_spec(_spec)
sys.modules["parking"] = parking
_spec.loader.exec_module(parking)

SpotKind = parking.SpotKind
Location = parking.Location
SpotTracker = parking.SpotTracker
locate_cars = parking.locate_cars
summary = parking.summary

BLUEY, SZARIK = "S8389a4cc897fdc45C", "Sf7915e6da7f90655C"
YL_ROOF = 113.0          # Model Y L under the garage-parking-1 sensor, floor 280
LADDER = 70.0


class SpotTests(unittest.TestCase):
    def test_empty_and_unavailable(self):
        t = SpotTracker()
        self.assertIs(t.update(False, 280, {}).kind, SpotKind.EMPTY)
        self.assertIs(t.update(None, None, {}).kind, SpotKind.UNKNOWN)

    def test_identifies_strongest_car(self):
        s = SpotTracker().update(True, YL_ROOF, {BLUEY: -60, SZARIK: -80})
        self.assertIs(s.kind, SpotKind.CAR)
        self.assertEqual(s.car_id, BLUEY)
        self.assertAlmostEqual(s.height_cm, 167)

    def test_ladder_is_not_a_car(self):
        s = SpotTracker().update(True, LADDER, {SZARIK: -60})
        self.assertIs(s.kind, SpotKind.OTHER)

    def test_guest_car_no_tesla_near(self):
        s = SpotTracker().update(True, YL_ROOF, {BLUEY: -95, SZARIK: None})
        self.assertIs(s.kind, SpotKind.OTHER)

    def test_two_close_is_unsure_then_decides(self):
        t = SpotTracker()
        self.assertIs(t.update(True, YL_ROOF, {BLUEY: -70, SZARIK: -73}).kind, SpotKind.UNSURE)
        s = t.update(True, YL_ROOF, {BLUEY: -62, SZARIK: -75})
        self.assertEqual(s.car_id, BLUEY)

    def test_latched_until_empty(self):
        t = SpotTracker()
        t.update(True, YL_ROOF, {BLUEY: -60})
        # Advert drops out, Szarik drives past: still Bluey
        self.assertEqual(t.update(True, YL_ROOF, {BLUEY: None, SZARIK: -65}).car_id, BLUEY)
        # Tailgate open: height out of band, still Bluey
        self.assertEqual(t.update(True, 60, {BLUEY: -60}).car_id, BLUEY)
        t.update(False, 280, {})
        self.assertEqual(t.update(True, YL_ROOF, {SZARIK: -60}).car_id, SZARIK)

    def test_bonnet_first_then_roof(self):
        t = SpotTracker()
        self.assertIs(t.update(True, 200, {BLUEY: -60}).kind, SpotKind.OTHER)
        self.assertEqual(t.update(True, YL_ROOF, {BLUEY: -60}).car_id, BLUEY)

    def test_no_distance_sensor_trusts_occupancy(self):
        self.assertEqual(SpotTracker().update(True, None, {BLUEY: -60}).car_id, BLUEY)


class LocateTests(unittest.TestCase):
    def spot(self, occupied, distance, rssi):
        return {"Garage": SpotTracker().update(occupied, distance, rssi)}

    def test_one_in_one_out(self):
        p = locate_cars({BLUEY: True, SZARIK: True}, self.spot(True, YL_ROOF, {BLUEY: -60, SZARIK: -88}))
        self.assertEqual(p[BLUEY].location, Location.IN_SPOT)
        self.assertEqual(p[BLUEY].spot, "Garage")
        self.assertEqual(p[SZARIK].location, Location.OUTSIDE)
        self.assertEqual(summary(p), "1 in, 1 out")

    def test_both_out_spot_empty(self):
        p = locate_cars({BLUEY: True, SZARIK: True}, self.spot(False, 280, {}))
        self.assertEqual(summary(p), "2 out")

    def test_one_home_ladder_in_spot_is_outside(self):
        p = locate_cars({BLUEY: False, SZARIK: True}, self.spot(True, LADDER, {SZARIK: -60}))
        self.assertEqual(p[SZARIK].location, Location.OUTSIDE)
        self.assertEqual(summary(p), "1 out, 1 away")

    def test_one_home_spot_empty_is_outside(self):
        p = locate_cars({BLUEY: True, SZARIK: False}, self.spot(False, 280, {}))
        self.assertEqual(p[BLUEY].location, Location.OUTSIDE)

    def test_sensor_unavailable_is_home(self):
        p = locate_cars({BLUEY: True, SZARIK: False}, self.spot(None, None, {}))
        self.assertEqual(p[BLUEY].location, Location.HOME)
        self.assertEqual(summary(p), "1 home, 1 away")

    def test_unsure_spot_is_home_for_both(self):
        p = locate_cars({BLUEY: True, SZARIK: True}, self.spot(True, YL_ROOF, {BLUEY: -70, SZARIK: -72}))
        self.assertEqual({c.location for c in p.values()}, {Location.HOME})

    def test_in_spot_wins_over_lagging_present(self):
        p = locate_cars({BLUEY: False}, self.spot(True, YL_ROOF, {BLUEY: -60}))
        self.assertEqual(p[BLUEY].location, Location.IN_SPOT)

    def test_no_data(self):
        p = locate_cars({BLUEY: None, SZARIK: True}, self.spot(False, 280, {}))
        self.assertIsNone(p[BLUEY].location)
        self.assertIsNone(summary(p))

    def test_both_in_two_spots(self):
        spots = {
            "Left": SpotTracker().update(True, YL_ROOF, {BLUEY: -58, SZARIK: -80}),
            "Right": SpotTracker().update(True, 118, {BLUEY: -79, SZARIK: -59}),
        }
        p = locate_cars({BLUEY: True, SZARIK: True}, spots)
        self.assertEqual((p[BLUEY].spot, p[SZARIK].spot), ("Left", "Right"))
        self.assertEqual(summary(p), "2 in")


if __name__ == "__main__":
    unittest.main()
