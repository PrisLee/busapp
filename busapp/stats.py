"""Turning logged ETAs into a number you can plan a morning around.

Two questions, two different statistics:

1. "When do I leave?"  -> How much earlier than predicted can this bus show up?
   Measured as the gap between the ETA we first saw for a bus and when it
   actually turned up. We plan to the 90th percentile of that.

2. "What if I miss it?" -> The wait for the next one. Derived from observed
   headways using the waiting-time distribution, not the raw average, because
   a passenger arriving at an arbitrary moment is more likely to land in a long
   gap than a short one. Ignoring that would understate the wait.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Sequence, Tuple

from . import config


# --- time ------------------------------------------------------------------
def sgt_struct(epoch: float) -> time.struct_time:
    return time.gmtime(epoch + config.SGT_OFFSET_HOURS * 3600)


def bucket_of(epoch: float) -> Tuple[str, int]:
    """(daytype, hour) in Singapore time. Peak behaviour differs by both."""
    t = sgt_struct(epoch)
    daytype = "weekend" if t.tm_wday >= 5 else "weekday"
    return daytype, t.tm_hour


def sgt_hhmm(epoch: float) -> str:
    t = sgt_struct(epoch)
    return "%02d:%02d" % (t.tm_hour, t.tm_min)


# --- generic ---------------------------------------------------------------
def quantile(values: Sequence[float], q: float) -> Optional[float]:
    """Linear-interpolated quantile. None for an empty sample."""
    xs = sorted(values)
    if not xs:
        return None
    if len(xs) == 1:
        return float(xs[0])
    pos = (len(xs) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def confidence_tier(n: int) -> str:
    for threshold, name in config.CONFIDENCE_TIERS:
        if n >= threshold:
            return name
    return "none"


# --- headways and waiting --------------------------------------------------
def headways(arrivals: Sequence[dict], daytype: Optional[str] = None,
             hour: Optional[int] = None) -> List[int]:
    """Gaps between consecutive arrivals, bucketed by when the later bus came.

    Gaps outside [MIN_HEADWAY_S, MAX_HEADWAY_S] are dropped: those are
    duplicate detections or overnight service breaks, not headways.
    """
    out = []
    prev = None
    for a in arrivals:
        t = a["arrived_at"]
        if prev is not None:
            gap = t - prev
            if config.MIN_HEADWAY_S <= gap <= config.MAX_HEADWAY_S:
                if daytype is None:
                    out.append(gap)
                else:
                    dt, hr = bucket_of(t)
                    if dt == daytype and (hour is None or hr == hour):
                        out.append(gap)
        prev = t
    return out


def wait_quantile(gaps: Sequence[int], q: float) -> Optional[float]:
    """The wait a passenger arriving at a random moment beats q of the time.

    For a renewal process with observed gaps H:
        P(wait > w) = sum(max(0, H_i - w)) / sum(H_i)
    That is monotone decreasing in w, so we invert it by bisection.
    """
    gaps = [g for g in gaps if g > 0]
    if not gaps:
        return None
    total = float(sum(gaps))
    target = 1.0 - q

    def survival(w: float) -> float:
        return sum(max(0.0, g - w) for g in gaps) / total

    lo, hi = 0.0, float(max(gaps))
    for _ in range(60):
        mid = (lo + hi) / 2
        if survival(mid) > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def mean_wait(gaps: Sequence[int]) -> Optional[float]:
    """Expected wait for a random arrival: E[H^2] / (2 E[H])."""
    gaps = [g for g in gaps if g > 0]
    if not gaps:
        return None
    return sum(g * g for g in gaps) / (2.0 * sum(gaps))


# --- how early can it be? --------------------------------------------------
def early_margin(arrivals: Sequence[dict], q: float) -> Optional[float]:
    """Seconds of margin so the bus beats you to the stop no more than 1-q of the time.

    error_s = actual arrival - first predicted arrival. Negative means the bus
    came *earlier* than first advertised, which is exactly how you miss it.
    """
    earlies = []
    for a in arrivals:
        e = a.get("error_s")
        if e is None:
            continue
        earlies.append(max(0, -int(e)))
    if not earlies:
        return None
    return quantile(earlies, q)


# --- the summary the UI consumes -------------------------------------------
def summarize(arrivals: Sequence[dict], now: Optional[float] = None) -> dict:
    """Reliability for one (stop, service), for the current time bucket.

    Falls back from "this hour" to "this daytype" to "any time" so a bucket with
    thin data can still borrow a weaker but honest claim -- always labelled with
    which basis was used.
    """
    now = time.time() if now is None else now
    daytype, hour = bucket_of(now)
    rows = [dict(a) for a in arrivals]

    bases = (
        ("hour", [r for r in rows if r["daytype"] == daytype and r["hour"] == hour],
         "%s %02d:00-%02d:59" % (daytype, hour, hour)),
        ("daytype", [r for r in rows if r["daytype"] == daytype], "any %s hour" % daytype),
        ("all", rows, "all logged times"),
    )

    chosen = bases[-1]
    for basis in bases:
        if len(basis[1]) >= config.MIN_FOR_CLAIM:
            chosen = basis
            break

    basis_name, sample, basis_label = chosen
    n = len(sample)
    gaps = headways(rows, daytype if basis_name != "all" else None,
                    hour if basis_name == "hour" else None)
    q = config.PLANNING_QUANTILE

    margin = early_margin(sample, q)
    tier = confidence_tier(n)
    claimable = n >= config.MIN_FOR_CLAIM

    return {
        "n": n,
        "n_total": len(rows),
        "basis": basis_name,
        "basis_label": basis_label,
        "confidence": tier,
        "claimable": claimable,
        "quantile": q,
        "margin_s": int(round(margin)) if (claimable and margin is not None) else None,
        "margin_is_default": not (claimable and margin is not None),
        "effective_margin_s": int(round(margin)) if (claimable and margin is not None)
                              else config.DEFAULT_MARGIN_S,
        "headway_n": len(gaps),
        "headway_median_s": int(round(quantile(gaps, 0.5))) if gaps else None,
        "wait_typical_s": int(round(mean_wait(gaps))) if gaps else None,
        "wait_p90_s": int(round(wait_quantile(gaps, q))) if gaps else None,
    }
