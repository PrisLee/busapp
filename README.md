# busapp — a buffer clock for the commute out of CT Hub 2

**One question, answered honestly: what time do I leave the building?**

Not a trip planner. Not a map. Those exist and [BusRouter SG](https://busrouter.sg)
does them well — this links out to it rather than rebuilding them. This app answers
the thing no bus app will tell you: *how much slack does this route actually demand?*

Example of the intended answer:

> **Leave at 8:11.** Service 61 has been 6+ min later than its estimate on
> 4 of the last 10 weekdays at this hour. A 3-minute buffer is not enough.

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

Between them they reach **427 stops with zero transfers** — about 8% of the island's
5,208 stops. That is the honest coverage of v1, and it includes Shenton Way, Toa Payoh,
Ang Mo Kio, Eunos, Tampines, Clementi, Buona Vista and Woodlands.

*(Stops, services and reachability above were derived from BusRouter SG's open route
data, not estimated.)*

### What the user does

1. Types a destination.
2. We resolve it to reachable stops among those 427 and pick the candidate services.
3. We return a **leave-by time with a stated confidence**, drawn from logged history
   for that service, at that stop, at that time-of-day and day-of-week.

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

## Status

Brief agreed. Nothing built yet.

Needed to start: a DataMall API key, and a decision on where the collector runs.
