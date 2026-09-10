"""Live arrivals: the real LTA DataMall client, and a simulator for when there is no key.

Both return the same normalised shape:

    {"stop_code": "07369", "polled_at": <epoch>, "services": {
        "61": [{"slot": 1, "eta": <epoch>, "load": "SEA", ...}, ...]}}

The simulator is a real simulation, not random noise: each service has a
timetable with jitter, so ETAs count down across polls and buses depart. That
means the arrival-reconstruction code below gets properly exercised offline --
and the app is demonstrable before a key arrives.
"""
from __future__ import annotations

import hashlib
import json
import struct
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Dict, List, Optional

from . import config, stats

LOAD_LABELS = {"SEA": "Seats available", "SDA": "Standing available", "LSD": "Limited standing"}


class ArrivalError(RuntimeError):
    pass


def _parse_eta(value: str) -> Optional[int]:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(value).timestamp())
    except ValueError:
        return None


class DataMallClient:
    """Talks to LTA DataMall. One HTTP call per bus stop."""

    name = "datamall"
    live = True

    def __init__(self, key: str, url: str = config.DATAMALL_URL, timeout: int = 20):
        if not key:
            raise ArrivalError("DATAMALL_API_KEY is empty")
        self.key = key
        self.url = url
        self.timeout = timeout

    def fetch(self, stop_code: str) -> dict:
        url = "%s?BusStopCode=%s" % (self.url, stop_code)
        req = urllib.request.Request(
            url, headers={"AccountKey": self.key, "Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            hint = " (check DATAMALL_API_KEY)" if e.code in (401, 403) else ""
            raise ArrivalError("DataMall HTTP %s for stop %s%s" % (e.code, stop_code, hint))
        except (urllib.error.URLError, TimeoutError) as e:
            raise ArrivalError("DataMall unreachable: %s" % e)
        except json.JSONDecodeError:
            raise ArrivalError("DataMall returned non-JSON for stop %s" % stop_code)

        polled_at = int(time.time())
        services: Dict[str, List[dict]] = {}
        for svc in payload.get("Services", []):
            num = svc.get("ServiceNo")
            if not num:
                continue
            buses = []
            for slot, field in enumerate(("NextBus", "NextBus2", "NextBus3"), start=1):
                bus = svc.get(field) or {}
                eta = _parse_eta(bus.get("EstimatedArrival", ""))
                if eta is None:
                    continue
                buses.append({
                    "slot": slot,
                    "eta": eta,
                    "load": bus.get("Load") or "",
                    "feature": bus.get("Feature") or "",
                    "type": bus.get("Type") or "",
                    "monitored": 1 if str(bus.get("Monitored", "")) in ("1", "True") else 0,
                })
            buses.sort(key=lambda b: b["eta"])
            if buses:
                services[num] = buses
        return {"stop_code": stop_code, "polled_at": polled_at, "services": services}


class SimulatedClient:
    """A deterministic bus network, for running without an API key.

    Every service gets a base headway and a reliability character. Some services
    are steady; some bunch badly. Arrival times are a pure function of the day
    and the bus index, so ETAs stay consistent from one poll to the next.
    """

    name = "simulated"
    live = False

    # service -> (base headway minutes, unreliability 0..1)
    CHARACTER = {
        "13": (9, 0.35), "61": (11, 0.55), "67": (12, 0.45), "107": (14, 0.30),
        "107M": (16, 0.30), "133": (10, 0.25), "141": (13, 0.50), "145": (8, 0.60),
        "175": (15, 0.40), "961": (12, 0.35), "961M": (18, 0.25),
    }
    FIRST_BUS_H = 6
    LAST_BUS_H = 24

    def __init__(self, network, seed: str = "busapp"):
        self.network = network
        self.seed = seed
        self._by_stop: Dict[str, List[str]] = {}
        for svc in network.services.values():
            self._by_stop.setdefault(svc["board_stop"], [])
            if svc["service"] not in self._by_stop[svc["board_stop"]]:
                self._by_stop[svc["board_stop"]].append(svc["service"])

    def _rand(self, *parts) -> float:
        """Deterministic float in [0,1) from arbitrary inputs."""
        raw = "|".join([self.seed] + [str(p) for p in parts]).encode()
        digest = hashlib.sha256(raw).digest()
        return struct.unpack(">I", digest[:4])[0] / 2**32

    def _day_start(self, epoch: float) -> int:
        t = stats.sgt_struct(epoch)
        midnight_utc = epoch - (t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec)
        return int(midnight_utc + self.FIRST_BUS_H * 3600)

    def _arrivals(self, stop_code: str, service: str, epoch: float) -> List[int]:
        """Actual arrival times for the service day containing `epoch`."""
        base_min, chaos = self.CHARACTER.get(service, (12, 0.4))
        day = stats.sgt_struct(epoch).tm_yday
        year = stats.sgt_struct(epoch).tm_year
        peak_hours = {7, 8, 9, 17, 18, 19}

        t = self._day_start(epoch)
        end = t + (self.LAST_BUS_H - self.FIRST_BUS_H) * 3600
        out: List[int] = []
        i = 0
        while t < end and i < 200:
            out.append(int(t))
            hour = stats.sgt_struct(t).tm_hour
            headway = base_min * (0.7 if hour in peak_hours else 1.15)
            # Bunching: a bimodal gap, so some buses arrive nose-to-tail and the
            # next gap is punishing. This is what makes the felt wait exceed the
            # advertised frequency.
            r = self._rand(year, day, stop_code, service, i)
            if r < chaos * 0.4:
                gap = headway * 0.15          # bunched behind the one in front
            elif r < chaos * 0.7:
                gap = headway * 2.1           # the hole that follows
            else:
                gap = headway * (0.75 + 0.5 * self._rand("j", year, day, stop_code, service, i))
            t += max(60, gap * 60)
            i += 1
        return out

    def fetch(self, stop_code: str) -> dict:
        now = int(time.time())
        services: Dict[str, List[dict]] = {}
        for service in self._by_stop.get(stop_code, []):
            upcoming = [a for a in self._arrivals(stop_code, service, now) if a > now - 30][:3]
            buses = []
            for slot, actual in enumerate(upcoming, start=1):
                remaining = max(0, actual - now)
                # Prediction error, decaying to zero as the bus gets close --
                # mirroring how a real ETA firms up on approach.
                bias = (self._rand("bias", stop_code, service, actual) - 0.5) * 360
                reported = actual + bias * min(1.0, remaining / 600.0)
                buses.append({
                    "slot": slot,
                    "eta": int(reported),
                    "load": ("SEA", "SDA", "LSD")[
                        int(self._rand("load", stop_code, service, actual, now // 60) * 3)],
                    "feature": "WAB",
                    "type": "SD" if self._rand("type", service, actual) < 0.7 else "DD",
                    "monitored": 1,
                })
            if buses:
                services[service] = buses
        return {"stop_code": stop_code, "polled_at": now, "services": services}


def build_client(network):
    """Real client when a key is configured, simulator otherwise."""
    if config.DATAMALL_KEY:
        return DataMallClient(config.DATAMALL_KEY)
    return SimulatedClient(network)
