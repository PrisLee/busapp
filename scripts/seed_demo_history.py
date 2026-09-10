"""Fill the database with SIMULATED past arrivals so the app can be demonstrated.

Real reliability data takes weeks to accumulate. This lets you see the finished
interface today. It sets a flag that makes the app display a banner saying the
history is fake, because a buffer number you cannot trust is worse than none.

    python3 scripts/seed_demo_history.py --days 21
    python3 scripts/seed_demo_history.py --clear
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from busapp import stats, store               # noqa: E402
from busapp.datamall import SimulatedClient   # noqa: E402
from busapp.network import Network            # noqa: E402


def seed(days: int) -> int:
    net = Network.load()
    sim = SimulatedClient(net)
    conn = store.connect()
    now = time.time()
    inserted = 0

    for svc in net.services.values():
        stop_code, service = svc["board_stop"], svc["service"]
        for day_offset in range(1, days + 1):
            when = now - day_offset * 86400
            for actual in sim._arrivals(stop_code, service, when):
                if actual > now:
                    continue
                # Mirror the simulator's own prediction error: the ETA first seen
                # for this bus, roughly ten minutes out, carries the full bias.
                bias = (sim._rand("bias", stop_code, service, actual) - 0.5) * 360
                first_eta = int(actual + bias)
                daytype, hour = stats.bucket_of(actual)
                if store.record_arrival(
                    conn, stop_code=stop_code, service=service, arrived_at=int(actual),
                    first_eta=first_eta, first_seen_at=int(actual - 600), obs_count=10,
                    error_s=int(actual) - first_eta, daytype=daytype, hour=hour,
                ):
                    inserted += 1

    store.set_meta(conn, "demo_history", "1")
    return inserted


def clear() -> int:
    conn = store.connect()
    cur = conn.execute("DELETE FROM arrivals")
    conn.commit()
    store.set_meta(conn, "demo_history", "0")
    return cur.rowcount


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=21)
    ap.add_argument("--clear", action="store_true", help="delete all arrivals and the demo flag")
    args = ap.parse_args()

    if args.clear:
        print("deleted %d arrivals" % clear())
        return
    n = seed(args.days)
    print("seeded %d simulated arrivals over %d days" % (n, args.days))
    print("The app will show a banner saying this history is not real.")
    print("Remove it with: python3 scripts/seed_demo_history.py --clear")


if __name__ == "__main__":
    main()
