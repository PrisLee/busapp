# busapp — a buffer clock for the commute out of CT Hub 2

**One question, answered honestly: what time do I leave the building?**

Not a trip planner. Not a map. Those exist and [BusRouter SG](https://busrouter.sg)
does them well — this links out to it rather than rebuilding them. This app answers
the thing no bus app will tell you: *how much slack does this route actually demand?*

Example of the intended answer:

> **Leave your desk at 8:11.** Bus 61 is due at 8:23. That's 5 minutes for the lift,
> 2 to walk to the stop, and a 5-minute margin because 61 has turned up that much
> earlier than first predicted in 9 of the last 10 logged mornings.

## Why this exists

Walked as a Five Whys, from symptom to cause:

1. **Why build with bus data?** → Curiosity about what the network actually does.
2. **Why does that matter?** → Some routes are unreliable.
3. **Why does unreliability matter?** → It wrecks commute planning.
4. **Why is planning hard?** → You only ever see a *single live ETA*, never its
   variance. So you cannot know how much slack to leave.
5. **Why is the variance invisible?** → **Nobody keeps the history.** LTA DataMall
   forgets every estimate the moment it serves it.

**Root cause: the record is never kept.** That is the one cause squarely within our
control, and it is why *the collector, not the UI, is the actual product.*

## Shape of the thing

**Origin is fixed. Destination is user input.**

That combination is what makes the project tractable. A fixed origin means the set of
services we could ever need is small and known in advance, so we can log all of them
continuously — whatever destination the user types, the answer comes from history we
already have. No cold start per trip.

### The origin: CT Hub 2, 4 Kallang Avenue

Four stops within walking distance (distances measured from the building):

| Stop | Name | Road | Walk | Services |
|-------|------------------------|-------------|-------|----------|
| 07369 | Aft Kallang Bahru | Lavender St | ~70 m | 11 |
| 07379 | Aperia / Bef Kallang Rd | Lavender St | ~110 m | 11 |
| 07361 | Bef Kallang Bahru | Lavender St | ~125 m | 5 |
| 07371 | Aft Kallang Rd | Lavender St | ~180 m | 11 |

### The 11 services we log

`13` `61` `67` `107` `107M` `133` `141` `145` `175` `961` `961M`

Between them they reach **426 stops with zero transfers** — about 8% of the island's
5,208 stops. That is the honest coverage of v1, and it includes Shenton Way, Toa Payoh,
Ang Mo Kio, Eunos, Tampines, Clementi, Buona Vista and Woodlands.

We board at whichever of these is the shortest walk for a given direction, which works
out to **three stops polled**: `07369` and `07379` share a direction, so the nearer one
wins. Coverage is 426 stops once boarding is pinned to the nearest stop this way.

*(Stops, services and reachability above were derived from BusRouter SG's open route
data, not estimated.)*

### What the user does

1. Types a destination.
2. We resolve it to reachable stops among those 426 and pick the candidate services.
3. We return a **leave-by time with a stated confidence**, drawn from logged history
   for that service, at that stop, at that time-of-day and day-of-week.

The leave-by time is when to leave your **desk**, not the building:

    leave_by = bus ETA - lift wait - walk to stop - margin

Every term is shown as its own row in the interface, so the number can be audited
rather than taken on faith. Three of the four are facts or measurements; the lift wait
is an assumption, which is why it is labelled and configurable:

| Term | Where it comes from | Default |
|-----------|---------------------------------------------------|---------|
| Bus ETA | LTA DataMall, live | — |
| Lift wait | **Assumption** about the building, not measured | 5 min |
| Walk | Stop distance at 80 m/min | 1-3 min |
| Margin | Measured p90 of how early the service runs | 2 min until measured |

Change the lift allowance with `BUSAPP_EXIT_BUFFER_MIN=7`, or set it to `0` if you
work on the ground floor.

## Data strategy: live now, history accrues

There is no public historical record of when buses actually came. DataMall serves live
estimates only. So:

- **Day one:** live ETAs, plus in-the-moment signals we *can* read without history —
  bunching, abnormal gaps, estimates that jump around. The app says plainly that it has
  no track record yet.
- **Continuously:** a cron collector polls DataMall arrivals for the four stops above
  and writes every estimate to a store. Four stops is a trivial polling load, which is
  exactly the point.
- **As data accrues:** derive real headways and variance, and the buffer number appears.
  **No fake confidence** — if the data doesn't support a claim, the app doesn't make one.

## Scope boundaries

**In scope:** direct journeys from CT Hub 2 on the 11 services; the leave-by number;
linking out to BusRouter SG for maps and route detail.

**Out of scope for v1:** maps, route browsing, turn-by-turn planning, MRT, the return
leg, and **journeys requiring a transfer** — we have no history for a second leg, so
those degrade to live-only and are labelled as such.

## Success test

In three months: *you leave the building on its advice instead of your own guess, and
you are not late.* One real user, genuinely served. It is built so a second origin could
be added, but that doesn't happen until the first number is one you'd stake a morning on.

## Known risk

The first two or three weeks are a collector writing rows to a database with almost
nothing to show. The temptation will be to broaden scope to feel productive. **Don't.**
Thin data across many routes is worse than none, because it invites false confidence.

## Data sources

- [LTA DataMall](https://datamall.lta.gov.sg) — live bus arrivals (API key required)
- [BusRouter SG open data](https://data.busrouter.sg) — stops and route topology
- [busrouter.sg](https://busrouter.sg) — where we hand off for maps and route browsing

## Running it

No dependencies, no build step, no package manager. Python 3.7+ standard library only.

```sh
python3 serve.py            # fetches route data on first run, then serves on :8080
```

Open <http://127.0.0.1:8080>. That single command starts the web app *and* the
collector, so history begins accumulating from the moment you first run it.

Without an API key it runs on a **simulator** and says so on every screen. For live
buses, get a free key from [LTA DataMall](https://datamall.lta.gov.sg):

```sh
cp .env.example .env        # paste your key in
set -a; source .env; set +a
python3 serve.py
```

Other entry points:

```sh
python3 serve.py --port 9000 --no-collector   # UI only, no polling
python3 -m busapp.collector                   # collector alone (a long-running service)
python3 -m busapp.collector --once            # single poll, for a cron entry
python3 scripts/fetch_network.py              # refresh routes after an LTA change
python3 -m unittest discover -s tests         # 30 tests
```

To see the finished interface before real history exists, seed simulated arrivals.
The app then shows a banner saying the history is fake, because a buffer number you
cannot trust is worse than no number:

```sh
python3 scripts/seed_demo_history.py --days 21
python3 scripts/seed_demo_history.py --clear    # remove it before trusting anything
```

### Where the collector should run

It needs to poll continuously, so a laptop that sleeps will leave holes in the record.
Either a small always-on host running `python3 -m busapp.collector`, or a cron entry
calling `--once` every minute:

```
* * * * * cd /path/to/busapp && DATAMALL_API_KEY=... python3 -m busapp.collector --once --quiet
```

Polling 3 stops each minute is roughly 4,300 calls a day, comfortably inside DataMall's
allowance. Raw observations are pruned after 30 days; derived arrivals are kept forever.

## How it works

```
scripts/fetch_network.py  -> data/network.json   route topology, from BusRouter SG
busapp/collector.py       -> data/busapp.sqlite3 polls DataMall, reconstructs arrivals
busapp/stats.py                                  headways, waiting time, margins
busapp/planner.py                                live ETAs + history -> "leave at HH:MM"
busapp/server.py          -> public/             JSON API and the front end
```

### Reconstructing arrivals

DataMall never says when a bus *arrived*, only when it expects to. So the collector
watches the leading ETA for each service. While a bus approaches, that ETA counts down.
When it jumps forward by more than two minutes, the bus we were watching is gone, and
its last observed ETA is our best estimate of when it actually turned up.

For each bus we also keep the **first** ETA we ever saw for it. The difference between
that and its arrival is the prediction error -- and the 90th percentile of how much
*earlier* than first advertised a service arrives is exactly the margin you need.

### Why the wait is worse than the timetable says

A passenger arriving at an arbitrary moment is more likely to land in a long gap than a
short one, so the felt wait exceeds half the average headway. The app uses the
waiting-time distribution rather than the mean:

    P(wait > w) = sum(max(0, H - w)) / sum(H)

Ten buses an hour, evenly spaced, means a 5-minute typical wait. Ten buses an hour
arriving in bunches can mean 9 minutes. Same timetable, different morning. That
difference is the reason this app exists.

## Honest limitations

- **Bunched buses are undercounted.** Two buses nose to tail do not move the leading ETA
  by the two minutes the detector needs, so the pair often reads as one arrival. This
  understates bunching -- the very thing the app is meant to expose. Raw observations are
  kept precisely so a better detector can be run over them later.
- **Ride time is not measured.** We only watch the boarding stops, so the app says when
  to leave and which bus to catch, not when you will arrive. It does not pretend to.
- **Transfers are out of scope.** No history exists for a second leg, so journeys needing
  a change are declined with a link to BusRouter SG rather than guessed at.
- **A sleeping collector leaves holes.** Headways spanning a gap in the record are
  discarded rather than counted as long waits.
- **The margin is one-sided by design.** It protects against a bus arriving early, not
  against one running late. Arriving early at the stop costs you a few minutes; missing
  the bus costs you the whole headway.
- **The lift wait is assumed, not measured.** Five minutes is a guess about the building,
  applied uniformly. In reality it varies by floor and time of day, and nothing here
  observes it. If mornings feel consistently rushed or slack, change
  `BUSAPP_EXIT_BUFFER_MIN` rather than trusting the default.

## Status

Built and working, on simulated data. 30 tests pass.

Still open: a DataMall API key for live arrivals, and an always-on home for the
collector. Until several weeks of real arrivals exist, the app will keep saying it has
no track record -- which is the intended behaviour, not a bug.
