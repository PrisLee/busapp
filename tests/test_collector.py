"""Tests for arrival reconstruction -- inferring arrivals from vanishing ETAs."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from busapp import store  # noqa: E402
from busapp.collector import Collector  # noqa: E402
from busapp.network import Network  # noqa: E402

BASE = 1_700_000_000


def tiny_network():
    raw = {
        "generated_at": "test",
        "origin": {"name": "Origin", "address": "addr", "lat": 0.0, "lon": 0.0,
                   "stops": [{"code": "S1", "name": "Stop One", "road": "Rd",
                              "lat": 0.0, "lon": 0.0, "walk_m": 80, "walk_min": 1,
                              "services": ["9"]}]},
        "poll_stops": ["S1"],
        "services": {"9:1": {"key": "9:1", "service": "9", "direction": 1,
                             "name": "A to B", "board_stop": "S1",
                             "downstream": ["D1"], "terminus": "B"}},
        "reachable": {"D1": [{"key": "9:1", "service": "9", "board_stop": "S1", "hops": 1}]},
        "stops": {"S1": {"code": "S1", "lon": 0.0, "lat": 0.0, "name": "Stop One", "road": "Rd"},
                  "D1": {"code": "D1", "lon": 0.0, "lat": 0.0, "name": "Dest", "road": "Rd2"}},
    }
    return Network(raw)


class ScriptedClient:
    """Replays a fixed sequence of (polled_at, eta) for service 9 at S1."""
    name = "scripted"
    live = False

    def __init__(self, steps):
        self.steps = list(steps)
        self.i = 0

    def fetch(self, stop_code):
        polled_at, eta = self.steps[self.i]
        self.i += 1
        return {"stop_code": stop_code, "polled_at": polled_at,
                "services": {"9": [{"slot": 1, "eta": eta, "load": "SEA",
                                    "feature": "WAB", "type": "SD", "monitored": 1}]}}


class TestReconstruction(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.conn = store.connect(os.path.join(self.dir, "t.sqlite3"))
        self.net = tiny_network()

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_steps(self, steps):
        c = Collector(self.conn, self.net, client=ScriptedClient(steps))
        recorded = 0
        for _ in steps:
            recorded += c.poll_once()["arrivals"]
        return recorded, store.arrivals_for(self.conn, "S1", "9")

    def test_eta_jump_records_one_arrival(self):
        """A bus tracked down to 280s, then the ETA leaps: it arrived and left."""
        n, rows = self.run_steps([
            (BASE + 0,   BASE + 300),   # first sighting
            (BASE + 60,  BASE + 300),
            (BASE + 120, BASE + 290),
            (BASE + 180, BASE + 280),   # last sighting before it goes
            (BASE + 300, BASE + 900),   # the bus behind
        ])
        self.assertEqual(n, 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["arrived_at"], BASE + 280)
        # It came 20s earlier than the ETA we first saw -- the number that matters.
        self.assertEqual(rows[0]["error_s"], -20)

    def test_countdown_alone_records_nothing(self):
        n, rows = self.run_steps([
            (BASE + 0,   BASE + 600),
            (BASE + 60,  BASE + 540),
            (BASE + 120, BASE + 480),
        ])
        self.assertEqual(n, 0)
        self.assertEqual(rows, [])

    def test_single_sighting_is_not_trusted(self):
        """One glimpse then a jump: we never watched it, so we do not claim it."""
        n, rows = self.run_steps([
            (BASE + 0,  BASE + 120),
            (BASE + 60, BASE + 900),
        ])
        self.assertEqual(n, 0)

    def test_long_silence_does_not_invent_an_arrival(self):
        """After a gap we cannot distinguish a departure from a restart."""
        n, rows = self.run_steps([
            (BASE + 0,     BASE + 300),
            (BASE + 60,    BASE + 240),
            (BASE + 60000, BASE + 60900),   # collector was down for hours
        ])
        self.assertEqual(n, 0)

    def test_raw_observations_are_kept(self):
        self.run_steps([(BASE + 0, BASE + 300), (BASE + 60, BASE + 240)])
        self.assertEqual(store.coverage(self.conn)["observations"], 2)

    def test_arrivals_are_idempotent(self):
        """Re-recording the same arrival must not double-count it."""
        for _ in range(2):
            store.record_arrival(self.conn, stop_code="S1", service="9",
                                 arrived_at=BASE, first_eta=BASE, first_seen_at=BASE,
                                 obs_count=3, error_s=0, daytype="weekday", hour=8)
        self.assertEqual(len(store.arrivals_for(self.conn, "S1", "9")), 1)


class TestUnexpectedServices(unittest.TestCase):
    def test_services_we_do_not_serve_are_ignored(self):
        """DataMall lists every service at a stop; we only log the ones we use."""
        dir_ = tempfile.mkdtemp()
        try:
            conn = store.connect(os.path.join(dir_, "t.sqlite3"))
            net = tiny_network()

            class Noisy:
                name, live = "noisy", False

                def fetch(self, stop_code):
                    return {"stop_code": stop_code, "polled_at": BASE, "services": {
                        "9": [{"slot": 1, "eta": BASE + 300, "load": "SEA",
                               "feature": "", "type": "SD", "monitored": 1}],
                        "999": [{"slot": 1, "eta": BASE + 60, "load": "SEA",
                                 "feature": "", "type": "SD", "monitored": 1}]}}

            Collector(conn, net, client=Noisy()).poll_once()
            self.assertEqual(store.coverage(conn)["observations"], 1)
        finally:
            shutil.rmtree(dir_, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
