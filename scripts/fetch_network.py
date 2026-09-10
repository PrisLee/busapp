"""Build data/network.json: everything the app needs about the bus network.

Source: BusRouter SG's open route data (https://data.busrouter.sg). We trim it to
what the brief actually needs -- the stops around CT Hub 2, the services that call
there, and where those services can take you without a transfer.

Run this once (and again whenever LTA changes routes):

    python3 scripts/fetch_network.py
"""
from __future__ import annotations

import json
import math
import os
import sys
import urllib.request
from datetime import datetime, timezone

STOPS_URL = "https://data.busrouter.sg/v1/stops.min.json"
SERVICES_URL = "https://data.busrouter.sg/v1/services.min.json"

# CT Hub 2, 4 Kallang Avenue. The one hardcoded fact in the app, per the brief.
ORIGIN = {
    "name": "CT Hub 2",
    "address": "4 Kallang Avenue, Singapore 339510",
    "lat": 1.31085,
    "lon": 103.86265,
}

# How far we consider "walkable" from the building, and assumed walking pace.
WALK_RADIUS_M = 200
WALK_METRES_PER_MIN = 80

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(HERE, "data", "network.json")


def fetch(url: str) -> dict:
    sys.stderr.write("fetching %s\n" % url)
    req = urllib.request.Request(url, headers={"User-Agent": "busapp/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Good-enough planar distance for a city-sized area."""
    dy = (lat2 - lat1) * 111320.0
    dx = (lon2 - lon1) * 111320.0 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


def walk_minutes(dist_m: float) -> int:
    return max(1, int(round(dist_m / WALK_METRES_PER_MIN)))


def build() -> dict:
    stops_raw = fetch(STOPS_URL)
    services_raw = fetch(SERVICES_URL)

    # stops.min.json: code -> [lon, lat, name, road]
    stops = {}
    for code, v in stops_raw.items():
        stops[code] = {
            "code": code,
            "lon": v[0],
            "lat": v[1],
            "name": v[2],
            "road": v[3] if len(v) > 3 else "",
        }

    # 1. Which stops can we walk to from the building?
    origin_stops = []
    for code, s in stops.items():
        d = metres(ORIGIN["lat"], ORIGIN["lon"], s["lat"], s["lon"])
        if d <= WALK_RADIUS_M:
            origin_stops.append(
                {
                    "code": code,
                    "name": s["name"],
                    "road": s["road"],
                    "lat": s["lat"],
                    "lon": s["lon"],
                    "walk_m": int(round(d)),
                    "walk_min": walk_minutes(d),
                    "services": [],
                }
            )
    origin_stops.sort(key=lambda s: s["walk_m"])
    origin_codes = {s["code"] for s in origin_stops}
    walk_m_by_code = {s["code"]: s["walk_m"] for s in origin_stops}
    if not origin_stops:
        raise SystemExit("no stops within %dm of the origin -- check coordinates" % WALK_RADIUS_M)

    # 2. Which services call at those stops, and what lies downstream of each?
    #    A service may appear in up to two directions; we keep each direction that
    #    touches a walkable stop, since they go opposite ways.
    services = {}
    reachable = {}

    for svc, info in services_raw.items():
        for direction, route in enumerate(info.get("routes", []), start=1):
            # A route may pass several walkable stops. Board at the one with the
            # shortest walk from the building -- that is what a person actually
            # does, and it is the stop we will poll and quote ETAs for.
            # First occurrence only: loop services can list a stop twice.
            candidates = []
            for i, stop_code in enumerate(route):
                if stop_code in origin_codes and not any(c[1] == stop_code for c in candidates):
                    candidates.append((i, stop_code))
            if not candidates:
                continue
            board_index, board_stop = min(candidates, key=lambda c: walk_m_by_code[c[1]])

            downstream = [c for c in route[board_index + 1:] if c in stops]
            if not downstream:
                continue  # boards at the last stop; useless to us

            key = "%s:%d" % (svc, direction)
            services[key] = {
                "key": key,
                "service": svc,
                "direction": direction,
                "name": info.get("name", ""),
                "board_stop": board_stop,
                "downstream": downstream,
                "terminus": stops[downstream[-1]]["name"],
            }

            for st in origin_stops:
                if st["code"] == board_stop and svc not in st["services"]:
                    st["services"].append(svc)

            for hops, dest in enumerate(downstream, start=1):
                reachable.setdefault(dest, []).append(
                    {"key": key, "service": svc, "board_stop": board_stop, "hops": hops}
                )

    for st in origin_stops:
        st["services"].sort(key=lambda s: (len(s), s))

    # Cheapest option first, so the UI can lead with the most direct ride.
    for dest, opts in reachable.items():
        opts.sort(key=lambda o: o["hops"])

    poll_stops = sorted({s["board_stop"] for s in services.values()})

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {"stops": STOPS_URL, "services": SERVICES_URL},
        "origin": dict(ORIGIN, stops=origin_stops),
        "poll_stops": poll_stops,
        "services": services,
        "reachable": reachable,
        "stops": stops,
    }


def main() -> None:
    net = build()
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(net, f, separators=(",", ":"))

    svc_names = sorted({s["service"] for s in net["services"].values()}, key=lambda x: (len(x), x))
    size_kb = os.path.getsize(OUT_PATH) / 1024
    print("wrote %s (%.0f KB)" % (os.path.relpath(OUT_PATH, HERE), size_kb))
    print("  origin        : %s" % net["origin"]["name"])
    print("  walkable stops: %s" % ", ".join(
        "%s (%dm)" % (s["code"], s["walk_m"]) for s in net["origin"]["stops"]))
    print("  stops to poll : %s" % ", ".join(net["poll_stops"]))
    print("  services      : %d (%s)" % (len(svc_names), " ".join(svc_names)))
    print("  direct reach  : %d of %d stops" % (len(net["reachable"]), len(net["stops"])))


if __name__ == "__main__":
    main()
