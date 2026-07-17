import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from classify import (parse_speed_mph, parse_lanes, categorize, score_segment,
                      score_components, transit_points, classification_margin,
                      margin_label, haversine_m, COMPONENT_KEYS)


class TestParsing(unittest.TestCase):
    def test_speed_mph_tag(self):
        self.assertEqual(round(parse_speed_mph("30 mph")), 30)

    def test_speed_bare_number_is_kmh(self):
        self.assertEqual(round(parse_speed_mph("50")), 31)

    def test_speed_multi_value_uses_first(self):
        self.assertEqual(round(parse_speed_mph("30 mph;40 mph")), 30)

    def test_speed_walk(self):
        self.assertEqual(parse_speed_mph("walk"), 6.0)

    def test_speed_garbage(self):
        self.assertIsNone(parse_speed_mph("RU:urban"))
        self.assertIsNone(parse_speed_mph(None))

    def test_invalid_lanes(self):
        self.assertIsNone(parse_lanes("0"))
        self.assertIsNone(parse_lanes("-2"))
        self.assertIsNone(parse_lanes("unknown"))
        self.assertIsNone(parse_lanes(None))

    def test_valid_lanes(self):
        self.assertEqual(parse_lanes("3"), 3)
        self.assertEqual(parse_lanes("2;3"), 2)


class TestScoring(unittest.TestCase):
    def test_score_is_sum_of_components(self):
        comps = score_components("primary", 3, 40, True, 2, 5, True, False)
        score = score_segment("primary", 3, 40, True, 2, 5, True, False)
        self.assertEqual(score, round(sum(comps.values()), 1))
        self.assertEqual(list(comps.keys()), COMPONENT_KEYS)

    def test_unpaved_penalty(self):
        paved = score_segment("residential", 1, 25, False, 0, 0, False, False)
        unpaved = score_segment("residential", 1, 25, False, 0, 0, False, True)
        self.assertEqual(round(paved - unpaved, 1), 6.0)

    def test_oneway_only_counts_for_arterials(self):
        arterial = score_components("primary", 2, 30, True, 0, 0, False, False)
        local = score_components("residential", 2, 30, True, 0, 0, False, False)
        self.assertEqual(arterial["oneway_pts"], 2)
        self.assertEqual(local["oneway_pts"], 0)

    def test_speed_hinge_at_25mph(self):
        slow = score_components("residential", 1, 20, False, 0, 0, False, False)
        fast = score_components("residential", 1, 35, False, 0, 0, False, False)
        self.assertEqual(slow["speed_pts"], 0)
        self.assertEqual(fast["speed_pts"], 4.0)

    def test_transit_tiers(self):
        self.assertEqual(transit_points(0), 0)
        self.assertEqual(transit_points(1), 3)
        self.assertEqual(transit_points(2), 5)
        self.assertEqual(transit_points(3), 5)
        self.assertEqual(transit_points(4), 7)
        self.assertEqual(transit_points(12), 7)


class TestCategories(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(categorize(20), "Low")
        self.assertEqual(categorize(20.1), "Moderate")
        self.assertEqual(categorize(38), "Moderate")
        self.assertEqual(categorize(38.1), "High")

    def test_margin(self):
        self.assertEqual(classification_margin(38.1), 0.1)
        self.assertEqual(classification_margin(29), 9.0)
        self.assertEqual(margin_label(0.5), "borderline")
        self.assertEqual(margin_label(3), "moderate")
        self.assertEqual(margin_label(9), "stable")


class TestGeometry(unittest.TestCase):
    def test_haversine_known_distance(self):
        # one degree of latitude is about 111.2 km
        d = haversine_m([42.0, -71.0], [43.0, -71.0])
        self.assertAlmostEqual(d, 111195, delta=200)


if __name__ == "__main__":
    unittest.main()
