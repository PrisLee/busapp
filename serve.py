#!/usr/bin/env python3
"""Start busapp: the web app and the collector, in one process.

    python3 serve.py                 # app on http://127.0.0.1:8080
    python3 serve.py --port 9000
    python3 serve.py --no-collector  # UI only, do not poll

With DATAMALL_API_KEY set it uses live LTA data. Without it, the app runs on a
simulator and says so on every screen.
"""
import argparse
import os
import runpy
import sys

from busapp import config
from busapp.server import serve


def ensure_network():
    """Fetch the route data on first run so `python3 serve.py` is enough."""
    if os.path.exists(config.NETWORK_PATH):
        return
    print("No network data yet -- fetching route data from BusRouter SG...")
    script = os.path.join(config.HERE, "scripts", "fetch_network.py")
    try:
        runpy.run_path(script, run_name="__main__")
    except SystemExit:
        pass
    if not os.path.exists(config.NETWORK_PATH):
        sys.exit("Could not build data/network.json. Run scripts/fetch_network.py manually.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=config.HOST)
    ap.add_argument("--port", type=int, default=config.PORT)
    ap.add_argument("--no-collector", action="store_true",
                    help="serve the UI without polling for arrivals")
    args = ap.parse_args()
    ensure_network()
    try:
        serve(args.host, args.port, with_collector=not args.no_collector)
    except OSError as e:
        sys.exit("cannot bind %s:%d -- %s" % (args.host, args.port, e))


if __name__ == "__main__":
    main()
