"""Tests for the leave-by arithmetic: lift wait + walk + margin."""
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from busapp import config, planner, store  # noqa: E402
from busapp.network import Network  # noqa: E402


def tiny_network(walk_min=1):
    raw = {
        "generated_at": "test",
        "origin": {"name": "CT Hub 2", "address": "addr", "lat": 0.0, "lon": 0.0,
                   "stops": [{"code": "S1", "name": "Stop One", "road": "Rd",
                              "lat": 0.0, "lon": 0.0, "walk_m": walk_min * 80,
                              "walk_min": walk_min, "services": ["9"]}]},
        "poll_stops": ["S1"],
        "services": {"9:1": {"key": "9:1", "service": "9", "direction": 1,
                             "name": "A to B", "board_stop": "S1",
                             "downstream": ["D1"], "terminus": "B"}},
        "reachable": {"D1": [{"key": "9:1", "service": "9", "board_stop": "S1", "hops": 1}]},
        "stops": {"S1": {"code": "S1", "lon": 0.0, "lat": 0.0, "name": "Stop One", "road": "Rd"},
                  "D1": {"code": "D1", "lon": 0.0, "lat": 0.0, "name": "Dest", "road": "Rd2"}},
    }
    return Network(raw)


class FixedClient:
    """Reports buses at fixed offsets from `now`."""
    name, live = "fixed", False

    def __init__(self, now, offsets):
        self.now, self.offsets = now, offsets

    def fetch(self, stop_code):
        return {"stop_code": stop_code, "polled_at": int(self.now), "services": {
            "9": [{"slot": i + 1, "eta": int(self.now + off), "load": "SEA",
                   "feature": "WAB", "type": "SD", "monitored": 1}
                  for i, off in enumerate(self.offsets)]}}


class TestLeaveBy(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.conn = store.connect(os.path.join(self.dir, "t.sqlite3"))
        self.net = tiny_network(walk_min=1)
        self.now = time.time()

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def plan(self, offsets):
        return planner.plan(self.conn, self.net, FixedClient(self.now, offsets),
                            "D1", now=self.now)

    def test_all_three_components_are_subtracted(self):
        """With no history: lift + walk + the default margin."""
        t = self.plan([900])["options"][0]["target"]
        expected = config.EXIT_BUFFER_S + 60 + config.DEFAULT_MARGIN_S
        self.assertEqual(t["eta_epoch"] - t["leave_epoch"], expected)

    def test_lift_buffer_is_reported_for_display(self):
        t = self.plan([900])["options"][0]["target"]
        self.assertEqual(t["exit_min"], config.EXIT_BUFFER_MIN)
        self.assertEqual(t["exit_s"], config.EXIT_BUFFER_S)
        self.assertEqual(t["walk_min"], 1)

    def test_lift_buffer_is_configurable(self):
        original = (config.EXIT_BUFFER_MIN, config.EXIT_BUFFER_S)
        try:
            config.EXIT_BUFFER_MIN, config.EXIT_BUFFER_S = 9, 540
            t = self.plan([1800])["options"][0]["target"]
            self.assertEqual(t["exit_min"], 9)
            self.assertEqual(t["eta_epoch"] - t["leave_epoch"], 540 + 60 + config.DEFAULT_MARGIN_S)
        finally:
            config.EXIT_BUFFER_MIN, config.EXIT_BUFFER_S = original

    def test_bus_too_soon_for_the_lift_is_skipped(self):
        """A bus you cannot reach even at a run is not offered; the next one is."""
        t = self.plan([60, 1800])["options"][0]["target"]
        self.assertEqual(t["eta_epoch"], int(self.now + 1800))

    def test_no_catchable_bus_reports_the_miss(self):
        r = self.plan([60])
        o = r["options"][0]
        self.assertIsNone(o["target"])
        self.assertIsNotNone(o["too_late_for"])

    def test_lift_buffer_makes_a_bus_uncatchable_that_walking_alone_would_allow(self):
        """The point of the change: 4 minutes is enough to walk, not to get downstairs."""
        offsets = [config.EXIT_BUFFER_S - 60]     # under the lift allowance
        self.assertIsNone(self.plan(offsets)["options"][0]["target"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
