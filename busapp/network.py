"""The static bus network: where we can walk, what calls there, where it goes."""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from . import config


class Network:
    def __init__(self, raw: dict):
        self.raw = raw
        self.origin = raw["origin"]
        self.stops: Dict[str, dict] = raw["stops"]
        self.services: Dict[str, dict] = raw["services"]
        self.reachable: Dict[str, List[dict]] = raw["reachable"]
        self.poll_stops: List[str] = raw["poll_stops"]
        self.generated_at = raw.get("generated_at", "")
        self._walk = {s["code"]: s for s in self.origin["stops"]}

    @classmethod
    def load(cls, path: Optional[str] = None) -> "Network":
        path = path or config.NETWORK_PATH
        if not os.path.exists(path):
            raise SystemExit(
                "Missing %s\nRun: python3 scripts/fetch_network.py" % os.path.relpath(path, config.HERE)
            )
        with open(path) as f:
            return cls(json.load(f))

    # --- lookups -----------------------------------------------------------
    def stop(self, code: str) -> Optional[dict]:
        return self.stops.get(code)

    def walk(self, code: str) -> dict:
        """Walking details for one of the boardable stops near the building."""
        return self._walk.get(code, {"walk_min": 1, "walk_m": 0})

    def service_numbers(self) -> List[str]:
        nums = {s["service"] for s in self.services.values()}
        return sorted(nums, key=lambda x: (len(x), x))

    def label(self, code: str) -> str:
        s = self.stops.get(code)
        if not s:
            return code
        return "%s (%s)" % (s["name"], s["road"]) if s["road"] else s["name"]

    # --- search ------------------------------------------------------------
    def search(self, query: str, limit: int = 12) -> List[dict]:
        """Find destinations by stop name, road, or stop code.

        Directly reachable stops rank first: those are the ones we can speak to
        with real history. Unreachable matches are still returned, flagged, so
        the app can say "that needs a transfer" instead of "not found".
        """
        q = (query or "").strip().lower()
        if len(q) < 2:
            return []

        scored = []
        for code, s in self.stops.items():
            name = s["name"].lower()
            road = s["road"].lower()
            if q == code.lower():
                score = 0
            elif name.startswith(q):
                score = 1
            elif q in name:
                score = 2
            elif road.startswith(q):
                score = 3
            elif q in road:
                score = 4
            elif code.startswith(q):
                score = 5
            else:
                continue
            direct = code in self.reachable
            # Reachable stops sort ahead of equally-good unreachable ones.
            scored.append(((0 if direct else 1), score, len(s["name"]), code, s, direct))

        scored.sort(key=lambda t: t[:4])
        out = []
        for _, _, _, code, s, direct in scored[:limit]:
            opts = self.reachable.get(code, [])
            out.append(
                {
                    "code": code,
                    "name": s["name"],
                    "road": s["road"],
                    "direct": direct,
                    "services": sorted({o["service"] for o in opts}, key=lambda x: (len(x), x)),
                    "hops": opts[0]["hops"] if opts else None,
                }
            )
        return out

    def suggestions(self, limit: int = 8) -> List[dict]:
        """A few recognisable direct destinations, to prime an empty search box.

        The far end of each service is both memorable and genuinely reachable,
        which makes these safe things to offer a first-time user.
        """
        seen = set()
        out = []
        for svc in sorted(self.services.values(),
                          key=lambda s: (len(s["service"]), s["service"])):
            code = svc["downstream"][-1]
            stop = self.stops.get(code)
            if not stop or code in seen:
                continue
            seen.add(code)
            out.append({
                "code": code,
                "name": stop["name"],
                "road": stop["road"],
                "services": sorted({o["service"] for o in self.reachable.get(code, [])},
                                   key=lambda x: (len(x), x)),
            })
        # Interchanges and stations read as landmarks; lead with them.
        out.sort(key=lambda s: (0 if ("Int" in s["name"] or "Ter" in s["name"]
                                      or "Stn" in s["name"]) else 1, s["name"]))
        return out[:limit]

    def options_for(self, dest_code: str) -> List[dict]:
        """Every direct way to get from the building to dest_code.

        One entry per service+direction, cheapest ride first.
        """
        out = []
        for opt in self.reachable.get(dest_code, []):
            svc = self.services[opt["key"]]
            board = svc["board_stop"]
            walk = self.walk(board)
            out.append(
                {
                    "key": opt["key"],
                    "service": opt["service"],
                    "direction": svc["direction"],
                    "route_name": svc["name"],
                    "terminus": svc["terminus"],
                    "board_stop": board,
                    "board_name": self.stops[board]["name"],
                    "board_road": self.stops[board]["road"],
                    "walk_min": walk["walk_min"],
                    "walk_m": walk["walk_m"],
                    "hops": opt["hops"],
                }
            )
        out.sort(key=lambda o: (o["hops"], o["walk_min"]))
        return out
