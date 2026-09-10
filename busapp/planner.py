"""Turning live ETAs plus logged history into "leave at HH:MM".

The recommendation is deliberately conservative in one direction only: we would
rather send you out a minute early than have the bus beat you to the stop. So

    leave_by = bus ETA - walking time - margin

where `margin` is the 90th percentile of how much *earlier* than first predicted
this service has actually shown up. Where there is no history, we use a flat
default and the UI says so rather than implying we measured it.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from . import config, stats, store

# Two buses closer together than this are bunched: the pair is one opportunity,
# not two, and a long hole usually follows.
BUNCH_WINDOW_S = 180


def _bus_view(bus: dict, now: float) -> dict:
    from .datamall import LOAD_LABELS
    return {
        "eta_epoch": bus["eta"],
        "eta_hhmm": stats.sgt_hhmm(bus["eta"]),
        "in_min": int(round((bus["eta"] - now) / 60.0)),
        "load": bus.get("load", ""),
        "load_label": LOAD_LABELS.get(bus.get("load", ""), ""),
        "type": {"SD": "Single deck", "DD": "Double deck", "BD": "Bendy"}.get(bus.get("type", ""), ""),
    }


def _signals(buses: List[dict], history: dict, now: float) -> List[dict]:
    """Reliability warnings we can raise from live data alone, with no history."""
    out = []
    if len(buses) >= 2:
        gap = buses[1]["eta"] - buses[0]["eta"]
        if gap <= BUNCH_WINDOW_S:
            out.append({
                "kind": "bunched",
                "text": "Two buses %d min apart, then likely a long gap. Catch this pair or expect a wait."
                        % max(1, int(round(gap / 60.0))),
            })
        typical = history.get("headway_median_s")
        if typical and gap > typical * 2:
            out.append({
                "kind": "gap",
                "text": "Nothing for %d min after this one, against a usual %d min gap."
                        % (int(round(gap / 60.0)), int(round(typical / 60.0))),
            })
    if len(buses) == 1:
        out.append({"kind": "only-one", "text": "Only one bus is being tracked right now."})
    return out


def plan_option(conn, option: dict, live: Dict[str, List[dict]], now: float) -> dict:
    """Attach live arrivals, history and a leave-by time to one boarding option."""
    service = option["service"]
    board = option["board_stop"]

    history = stats.summarize(store.arrivals_for(conn, board, service), now=now)
    buses = sorted(live.get(service, []), key=lambda b: b["eta"])
    walk_s = option["walk_min"] * 60
    margin_s = history["effective_margin_s"]

    target = None
    for bus in buses:
        leave_at = bus["eta"] - walk_s - margin_s
        # A bus you would have to leave for more than a minute ago is not yours.
        if leave_at >= now - 60:
            target = {
                "eta_epoch": bus["eta"],
                "eta_hhmm": stats.sgt_hhmm(bus["eta"]),
                "in_min": int(round((bus["eta"] - now) / 60.0)),
                "leave_epoch": int(leave_at),
                "leave_hhmm": stats.sgt_hhmm(leave_at),
                "leave_in_min": int(round((leave_at - now) / 60.0)),
                "walk_min": option["walk_min"],
                "margin_min": int(round(margin_s / 60.0)),
                "margin_s": margin_s,
                "margin_is_default": history["margin_is_default"],
            }
            break

    missed = None
    if buses and target is None:
        missed = {
            "eta_hhmm": stats.sgt_hhmm(buses[0]["eta"]),
            "in_min": int(round((buses[0]["eta"] - now) / 60.0)),
        }

    return dict(
        option,
        buses=[_bus_view(b, now) for b in buses],
        target=target,
        too_late_for=missed,
        history=history,
        signals=_signals(buses, history, now),
    )


def plan(conn, network, client, dest_code: str, now: Optional[float] = None) -> dict:
    now = time.time() if now is None else now
    dest = network.stop(dest_code)
    if not dest:
        return {"error": "unknown_stop", "code": dest_code}

    options = network.options_for(dest_code)
    result = {
        "now": int(now),
        "now_hhmm": stats.sgt_hhmm(now),
        "destination": {
            "code": dest_code, "name": dest["name"], "road": dest["road"],
            "busrouter_url": "https://busrouter.sg/#/stops/%s" % dest_code,
        },
        "reachable": bool(options),
        "options": [],
        "best_key": None,
    }
    if not options:
        return result

    # One live fetch per boarding stop, shared across the services that use it.
    live_by_stop: Dict[str, Dict[str, List[dict]]] = {}
    errors = []
    for stop_code in {o["board_stop"] for o in options}:
        try:
            live_by_stop[stop_code] = client.fetch(stop_code)["services"]
        except Exception as e:
            live_by_stop[stop_code] = {}
            errors.append(str(e))

    planned = [plan_option(conn, o, live_by_stop.get(o["board_stop"], {}), now) for o in options]

    # Lead with the bus that gets you moving soonest; options with no live bus last.
    def sort_key(o):
        if o["target"]:
            return (0, o["target"]["eta_epoch"])
        return (1, o["hops"])

    planned.sort(key=sort_key)
    result["options"] = planned
    result["best_key"] = planned[0]["key"] if planned and planned[0]["target"] else None
    if errors:
        result["live_error"] = errors[0]
    return result
