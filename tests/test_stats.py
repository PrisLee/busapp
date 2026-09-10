"""Tests for the numbers the app asks people to trust."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from busapp import config, stats  # noqa: E402


class TestQuantile(unittest.TestCase):
    def test_empty_is_none(self):
        self.assertIsNone(stats.quantile([], 0.9))

    def test_single_value(self):
        self.assertEqual(stats.quantile([42], 0.9), 42.0)

    def test_median_interpolates(self):
        self.assertEqual(stats.quantile([0, 10], 0.5), 5.0)


class TestWaitingTime(unittest.TestCase):
    """A passenger's wait is not half the headway unless buses are perfect."""

    def test_constant_headway_p90(self):
        # Buses exactly every 10 min: you wait under 9 min nine times in ten.
        w = stats.wait_quantile([600] * 20, 0.9)
        self.assertAlmostEqual(w, 540, delta=1)

    def test_constant_headway_mean(self):
        self.assertAlmostEqual(stats.mean_wait([600] * 20), 300, delta=0.5)

    def test_variability_makes_waits_worse(self):
        """The core claim of the app: same average, worse experience.

        Two timetables with an identical 10-minute mean. The bunched one is
        materially worse to stand at, and a mean-headway model would miss it.
        """
        steady = [600] * 10
        bunched = [60, 1140] * 5
        self.assertEqual(sum(steady), sum(bunched))
        self.assertGreater(stats.mean_wait(bunched), stats.mean_wait(steady) * 1.5)
        self.assertGreater(stats.wait_quantile(bunched, 0.9), stats.wait_quantile(steady, 0.9))

    def test_no_data_is_none_not_zero(self):
        self.assertIsNone(stats.wait_quantile([], 0.9))
        self.assertIsNone(stats.mean_wait([]))


class TestHeadways(unittest.TestCase):
    def test_service_breaks_are_not_headways(self):
        # Two buses 10 min apart, then an overnight gap, then two more.
        arrivals = [
            {"arrived_at": 0}, {"arrived_at": 600},
            {"arrived_at": 600 + config.MAX_HEADWAY_S + 60},
            {"arrived_at": 600 + config.MAX_HEADWAY_S + 660},
        ]
        self.assertEqual(stats.headways(arrivals), [600, 600])

    def test_duplicate_detections_dropped(self):
        arrivals = [{"arrived_at": 0}, {"arrived_at": 5}, {"arrived_at": 600}]
        self.assertEqual(stats.headways(arrivals), [595])


class TestEarlyMargin(unittest.TestCase):
    def test_only_early_arrivals_count(self):
        # Late buses (positive error) do not make you miss one; early buses do.
        arrivals = [{"error_s": e} for e in [300, 200, 100]]
        self.assertEqual(stats.early_margin(arrivals, 0.9), 0.0)

    def test_early_arrivals_drive_the_margin(self):
        arrivals = [{"error_s": -120}] * 9 + [{"error_s": -300}]
        m = stats.early_margin(arrivals, 0.9)
        self.assertGreater(m, 120)
        self.assertLessEqual(m, 300)


class TestSummarize(unittest.TestCase):
    def _rows(self, n, daytype="weekday", hour=8):
        return [{"arrived_at": i * 600, "error_s": -60, "daytype": daytype, "hour": hour}
                for i in range(n)]

    def test_thin_data_refuses_to_claim(self):
        s = stats.summarize(self._rows(3))
        self.assertFalse(s["claimable"])
        self.assertIsNone(s["margin_s"])
        self.assertTrue(s["margin_is_default"])
        self.assertEqual(s["effective_margin_s"], config.DEFAULT_MARGIN_S)

    def test_no_data_at_all(self):
        s = stats.summarize([])
        self.assertEqual(s["n"], 0)
        self.assertFalse(s["claimable"])
        self.assertEqual(s["confidence"], "none")

    def test_enough_data_makes_a_claim(self):
        # Rows in the bucket for "now", so the hour basis is used.
        daytype, hour = stats.bucket_of(__import__("time").time())
        s = stats.summarize(self._rows(config.MIN_FOR_CLAIM + 5, daytype, hour))
        self.assertTrue(s["claimable"])
        self.assertEqual(s["basis"], "hour")
        self.assertIsNotNone(s["margin_s"])
        self.assertFalse(s["margin_is_default"])

    def test_falls_back_to_wider_basis_when_hour_is_thin(self):
        """Thin at this hour, rich across the day: borrow the weaker claim, labelled."""
        import time as _t
        daytype, hour = stats.bucket_of(_t.time())
        other = (hour + 3) % 24
        rows = self._rows(2, daytype, hour) + self._rows(40, daytype, other)
        s = stats.summarize(rows)
        self.assertEqual(s["basis"], "daytype")
        self.assertIn("any", s["basis_label"])
        self.assertTrue(s["claimable"])


class TestBuckets(unittest.TestCase):
    def test_sgt_offset_applied(self):
        # 2026-01-01T00:30:00+08:00 == 2025-12-31T16:30:00Z
        epoch = 1767198600
        self.assertEqual(stats.sgt_hhmm(epoch), "00:30")

    def test_weekend_detection(self):
        # 2026-01-03 is a Saturday in Singapore.
        daytype, _ = stats.bucket_of(1767369600)
        self.assertEqual(daytype, "weekend")


if __name__ == "__main__":
    unittest.main(verbosity=2)
