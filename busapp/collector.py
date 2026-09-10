"""The collector. Per the brief, this -- not the UI -- is the actual product.

DataMall never tells you when a bus arrived, only when it *expects* to. So we
poll, and infer arrivals from the shape of the estimates:

    A bus is "the next bus" until it isn't. When the leading ETA for a service
    jumps forward by more than a couple of minutes, the bus we were watching has
    gone. Its last observed ETA is our best estimate of when it actually turned up.

We keep, for each bus we track, the *first* ETA we ever saw for it. The
difference between that and its arrival is the prediction error -- the number
that tells a commuter how much margin to leave. That is the whole point.

Run standalone (for cron, systemd, or a spare terminal):

    python3 -m busapp.collector            # poll forever
    python3 -m busapp.collector --once     # single poll, for a cron entry
"""
from __future__ import annotations

import argparse
import sys
import time
from typing import Optional

from . import config, stats, store
from .datamall import ArrivalError, build_client
from .network import Network

# If we have not seen a service for this long, we cannot tell a departure from a
# service break or a collector restart. Start the bus afresh instead of guessing.
STALE_AFTER_S = 900

# Require this many sightings before trusting a reconstructed arrival, so a bus
# glimpsed once on its way past does not become a data point.
MIN_SIGHTINGS = 2


class Collector:
    def __init__(self, conn, network: Network, client=None, verbose: bool = False):
        self.conn = conn
        self.network = network
        self.client = client or build_client(network)
        self.verbose = verbose
        # Only trust services the static network says call here.
        self.expected = {}
        for svc in network.services.values():
            self.expected.setdefault(svc["board_stop"], set()).add(svc["service"])

    def log(self, msg: str) -> None:
        if self.verbose:
            sys.stderr.write("[%s] %s\n" % (stats.sgt_hhmm(time.time()), msg))

    # --- one pass ----------------------------------------------------------
    def poll_once(self) -> dict:
        tracking = store.get_tracking(self.conn)
        totals = {"stops": 0, "observations": 0, "arrivals": 0, "errors": []}

        for stop_code in self.network.poll_stops:
            try:
                payload = self.client.fetch(stop_code)
            except ArrivalError as e:
                totals["errors"].append(str(e))
                self.log("poll failed for %s: %s" % (stop_code, e))
                continue

            totals["stops"] += 1
            polled_at = payload["polled_at"]
            rows = []
            wanted = self.expected.get(stop_code, set())

            for service, buses in payload["services"].items():
                if service not in wanted:
                    continue  # a service that passes but does not serve our journey
                for b in buses:
                    rows.append((stop_code, service, b["slot"], b["eta"], b["load"],
                                 b["feature"], b["type"], b["monitored"], polled_at))
                if self._track(tracking, stop_code, service, buses[0]["eta"], polled_at):
                    totals["arrivals"] += 1

            totals["observations"] += store.record_observations(self.conn, rows)

        ok = totals["stops"] > 0
        store.set_meta(self.conn, "last_poll_at", int(time.time()))
        store.set_meta(self.conn, "last_poll_ok", "1" if ok else "0")
        store.set_meta(self.conn, "last_poll_error", totals["errors"][0] if totals["errors"] else "")
        store.set_meta(self.conn, "client", self.client.name)
        return totals

    # --- arrival reconstruction -------------------------------------------
    def _track(self, tracking, stop_code: str, service: str,
               next_eta: int, polled_at: int) -> bool:
        """Update state for one service. Returns True if an arrival was recorded."""
        key = (stop_code, service)
        prev = tracking.get(key)
        recorded = False

        if prev is None or prev["next_eta"] is None:
            state = dict(next_eta=next_eta, first_eta=next_eta,
                         first_seen_at=polled_at, obs_count=1, last_seen_at=polled_at)
        elif polled_at - (prev["last_seen_at"] or 0) > STALE_AFTER_S:
            # Too long a silence to reason about. Begin again.
            self.log("%s@%s stale, resetting" % (service, stop_code))
            state = dict(next_eta=next_eta, first_eta=next_eta,
                         first_seen_at=polled_at, obs_count=1, last_seen_at=polled_at)
        elif next_eta > prev["next_eta"] + config.DEPARTURE_JUMP_S:
            # The bus we were watching is gone: it arrived and left.
            arrived_at = min(polled_at, max(prev["next_eta"], prev["last_seen_at"]))
            if (prev["obs_count"] or 0) >= MIN_SIGHTINGS:
                daytype, hour = stats.bucket_of(arrived_at)
                first_eta = prev["first_eta"]
                recorded = store.record_arrival(
                    self.conn,
                    stop_code=stop_code, service=service, arrived_at=arrived_at,
                    first_eta=first_eta, first_seen_at=prev["first_seen_at"],
                    obs_count=prev["obs_count"],
                    error_s=(arrived_at - first_eta) if first_eta else None,
                    daytype=daytype, hour=hour,
                )
                if recorded:
                    self.log("arrival: %s@%s at %s (error %+ds over %d sightings)" % (
                        service, stop_code, stats.sgt_hhmm(arrived_at),
                        (arrived_at - first_eta) if first_eta else 0, prev["obs_count"]))
            # ...and the bus behind it becomes the one we watch.
            state = dict(next_eta=next_eta, first_eta=next_eta,
                         first_seen_at=polled_at, obs_count=1, last_seen_at=polled_at)
        else:
            # Same bus, closer. Keep the first ETA: that is what we measure against.
            state = dict(next_eta=next_eta, first_eta=prev["first_eta"],
                         first_seen_at=prev["first_seen_at"],
                         obs_count=(prev["obs_count"] or 0) + 1, last_seen_at=polled_at)

        store.set_tracking(self.conn, stop_code, service, **state)
        tracking[key] = dict(state, stop_code=stop_code, service=service)
        return recorded

    # --- loop --------------------------------------------------------------
    def run_forever(self, interval: Optional[int] = None) -> None:
        interval = interval or config.POLL_INTERVAL_S
        self.log("collecting every %ds from %s via %s" % (
            interval, ", ".join(self.network.poll_stops), self.client.name))
        last_prune = 0.0
        while True:
            started = time.time()
            try:
                t = self.poll_once()
                self.log("polled %d stops, %d observations, %d arrivals%s" % (
                    t["stops"], t["observations"], t["arrivals"],
                    (" | " + t["errors"][0]) if t["errors"] else ""))
            except Exception as e:  # never let one bad poll kill the collector
                sys.stderr.write("collector error: %r\n" % (e,))

            if started - last_prune > 86400:
                removed = store.prune(self.conn)
                if removed:
                    self.log("pruned %d raw observations" % removed)
                last_prune = started

            time.sleep(max(1.0, interval - (time.time() - started)))


def main() -> None:
    ap = argparse.ArgumentParser(description="Poll DataMall and log bus arrivals.")
    ap.add_argument("--once", action="store_true", help="single poll then exit (for cron)")
    ap.add_argument("--interval", type=int, default=config.POLL_INTERVAL_S)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    network = Network.load()
    conn = store.connect()
    c = Collector(conn, network, verbose=not args.quiet)
    if not c.client.live:
        sys.stderr.write("No DATAMALL_API_KEY set -- collecting SIMULATED data.\n")
    if args.once:
        t = c.poll_once()
        print("stops=%d observations=%d arrivals=%d" % (t["stops"], t["observations"], t["arrivals"]))
        for e in t["errors"]:
            print("error: %s" % e, file=sys.stderr)
        sys.exit(1 if t["errors"] and t["stops"] == 0 else 0)
    c.run_forever(args.interval)


if __name__ == "__main__":
    main()
