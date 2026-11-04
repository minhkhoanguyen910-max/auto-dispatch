# Plan 1: Simulated day vs. real data

Saved 2026-10-28 so the simulated results can be compared with real days once the app is in real use.

## 1. Simulated results (saved 2026-10-28)

**Data:** made up at random by `scripts/simulate_day.py`, not real orders; no database, Google or Telegram.

| Part | Assumption |
|---|---|
| Orders | random times, spread evenly over a 10-hour day |
| Addresses | random points spread evenly within 4 km of a centre (central Leeds) |
| Drivers | 2, starting at the centre, always accepting offers |
| Driving times | estimates: straight line × 1.3 at 25 km/h; no real roads, no traffic |
| Time at a stop | exactly 5 minutes |
| Deadlines | every order within 60 minutes (automatic limit); no Urgent, no chosen times |
| Rules compared | **today**: nearest driver, nearest stop next; **route planning**: steps 3, 4 and 8 (least extra time, best gap, reshuffle when it saves ≥ 3 min or keeps a deadline) |

Reproduce any row exactly with the same `--seed`:

```
.venv\Scripts\python.exe scripts\simulate_day.py --orders 100 --seed 1 --detail
```

### Summary

| Orders/day | Seed | Rule | Within 1 h | Avg wait | 90% within | Worst | Driving | Last done |
|---|---|---|---|---|---|---|---|---|
| 100 | 1 | Today | 81% | 35 m | 85 m | 230 m | 10.7 h | 11.3 h |
| 100 | 1 | Route planning | 82% | 40 m | 63 m | 83 m | 11.7 h | 11.0 h |
| 100 | 2 | Today | 83% | 31 m | 73 m | 203 m | 10.9 h | 11.6 h |
| 100 | 2 | Route planning | **97%** | 38 m | 58 m | 83 m | 11.9 h | 10.9 h |
| 140 | 1 | Today | 63% | 73 m | 198 m | 411 m | 10.3 h | 12.0 h |
| 140 | 1 | Route planning | 52% | 64 m | 113 m | 218 m | 10.0 h | 11.5 h |
| 140 | 2 | Today | 65% | 75 m | 220 m | 517 m | 12.3 h | 12.6 h |
| 140 | 2 | Route planning | 55% | 67 m | 128 m | 227 m | 11.5 h | 12.4 h |

### Customers by waiting time

| Orders | Seed | Rule | 0–30 m | 30–60 | 60–90 | 90–120 | 120–180 | 180–300 | 300 m+ | Late minutes total |
|---|---|---|---|---|---|---|---|---|---|---|
| 100 | 1 | Today | 67 | 14 | 9 | 5 | 3 | 2 | 0 | 899 |
| 100 | 1 | Route planning | 36 | 46 | 18 | 0 | 0 | 0 | 0 | 91 |
| 100 | 2 | Today | 72 | 11 | 10 | 6 | 0 | 1 | 0 | 504 |
| 100 | 2 | Route planning | 37 | 60 | 3 | 0 | 0 | 0 | 0 | 27 |
| 140 | 1 | Today | 63 | 25 | 16 | 6 | 14 | 9 | 7 | 5,190 |
| 140 | 1 | Route planning | 27 | 46 | 37 | 20 | 8 | 2 | 0 | 2,268 |
| 140 | 2 | Today | 67 | 24 | 13 | 8 | 13 | 8 | 7 | 5,417 |
| 140 | 2 | Route planning | 22 | 55 | 31 | 13 | 16 | 3 | 0 | 2,715 |

### 140 orders, seed 1: each hour's orders (within 1 h / worst wait)

| Hour placed | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| Today | 9/10 · 113m | 8/15 · 142m | 11/19 · 335m | 6/13 · 374m | 8/13 · 200m | 14/19 · 411m | 8/14 · 275m | 8/12 · 219m | 7/10 · 163m | 9/15 · 129m |
| Route planning | 10/10 · 54m | 15/15 · 58m | 12/19 · 218m | 6/13 · 125m | 4/13 · 92m | 5/19 · 186m | 7/14 · 135m | 2/12 · 115m | 4/10 · 125m | 8/15 · 99m |

### What it shows
- **Normal load (100/day):** route planning is better: nobody waits hours (worst 83 min vs 203–230), up to 97%
  within the hour, total lateness down 90–95%. Average wait is a few minutes higher and there's ~1 h more driving
  per day across both drivers: nearest-first saves driving by leaving far customers waiting.
- **Overload (140/day; with these assumptions 2 drivers manage ~100–110):** someone must wait over an hour
  whatever the rule. Nearest-first serves many nearby customers very fast and **abandons a few for hours** (7 over
  5 hours); route planning **shares the delay** (none over 5 hours, total lateness halved) but fewer end up within
  the hour (52–55% vs 63–65%), because lateness is scored per minute so old orders are served before new ones.

### Decision (2026-10-30): B
How route planning should behave when overloaded:
- **A. Fairness:** no one waits hours, lowest total lateness, fewer within the hour.
- **B. Most customers within the hour (chosen):** add a fixed penalty per late customer; a few wait much longer.
- **C. A balance of both.**

## 1b. Rule B results (saved 2026-10-30)

Same simulated days and assumptions as above; `--late-penalty-min 60` (the default). The simulation now picks
drivers exactly as the app does (minutes late break ties before "finishes least later"), so its rule A figures
differ a little from the table above, which was saved before that.

| Orders/day | Seed | Rule | Within 1 h | Avg wait | 90% within | Worst | Driving | Last done |
|---|---|---|---|---|---|---|---|---|
| 100 | 1 | Today | 81% | 35 m | 85 m | 230 m | 10.7 h | 11.3 h |
| 100 | 1 | A (shared) | 89% | 38 m | 60 m | 73 m | 12.1 h | 11.1 h |
| 100 | 1 | **B (60 min)** | **95%** | 37 m | 59 m | 175 m | 12.0 h | 11.0 h |
| 100 | 2 | Today | 83% | 31 m | 73 m | 203 m | 10.9 h | 11.6 h |
| 100 | 2 | A (shared) | 95% | 39 m | 59 m | 87 m | 12.0 h | 11.0 h |
| 100 | 2 | **B (60 min)** | **98%** | 38 m | 59 m | 88 m | 11.9 h | 10.9 h |
| 140 | 1 | Today | 63% | 73 m | 198 m | 411 m | 10.3 h | 12.0 h |
| 140 | 1 | A (shared) | 46% | 64 m | 106 m | 240 m | 10.5 h | 11.6 h |
| 140 | 1 | **B (60 min)** | **77%** | 65 m | 119 m | 384 m | 10.8 h | 11.8 h |
| 140 | 2 | Today | 65% | 75 m | 220 m | 517 m | 12.3 h | 12.6 h |
| 140 | 2 | A (shared) | 50% | 66 m | 111 m | 255 m | 11.5 h | 12.2 h |
| 140 | 2 | **B (60 min)** | **74%** | 71 m | 149 m | 561 m | 12.2 h | 12.3 h |

Penalty size, overloaded days (140/day, seeds 1 / 2): **30 min** 68% / 69% within the hour, 90% within 131 / 130 m;
**60 min** 77% / 74%, 119 / 149 m; **120 min** 79% / 80%, 190 / 210 m. 60 is the chosen middle: 120 adds little
within the hour and makes the 90% wait much longer.

What it shows: B gets the most customers within the hour on every day. The price, when overloaded, is a few very
long waits (worst 6–9 hours in these days; A never above about 4 hours).

Also asked, not answered yet: the real delivery area (farthest customer from the centre, km), the typical
minutes per stop, and orders per day (normal / busy), to rerun the simulation with real figures.

## 1c. Real figures, Hastings (saved 2026-10-30)

From the user: customers within the area from Hollington to Ore and the Ridge to the seafront (about **2.2 km** from
a centre near St Helen's Road), **5 min** per stop, **70 orders** a normal day and **100** a busy one. Still assumed:
2 drivers starting at the centre, a 10-hour day, a 60-minute limit, estimated driving times.

    scripts/simulate_day.py --radius-km 2.2 --service-min 5 --orders 70|100 --seed 1|2|3

| Day | Today: within 1 h · worst | Route planning A · worst | Route planning B · worst |
|---|---|---|---|
| 70 orders, 2 drivers (3 days) | 97–100% · 33–81 m | 100% · 32–59 m | 100% · 32–59 m (same as A) |
| 100 orders, 2 drivers (3 days) | 92–100% · 53–122 m | 100% · 56–59 m | 100% · 56–59 m (same as A) |
| 70 orders, **1 driver** (2 days) | 80–86% · 138–221 m | 66–77% · 114–117 m | **86–89%** · 154–232 m |
| 100 orders in **6 h** (rush), 2 drivers (2 days) | 76–83% · 206–231 m | 68–92% · 81–110 m | **87–94%** · 90–186 m |

What it shows: two drivers aren't overloaded on normal or busy days, and route planning keeps everyone within the
hour (today's rule doesn't always). On hard days (a driver off, a rush) rule B keeps the most customers within the
hour; rule A can then do worse than today. Real times will be somewhat longer than these estimates (hills, one-way
streets), so real days sit closer to the hard rows.

## 2. Later: replay real days (not built)

Once there are a few weeks of real orders (after the pre-release wipe), replay **actual days** with both rules and
compare with what really happened. Planned approach:
- **Input from the database, per day:** each order's placed time, drop-off location, the driver who delivered it,
  delivered time, and the real time from Arrived to Delivered; drivers' first positions of the day.
- **Driving times:** remembered Google times (`travel_times`) where available, estimates otherwise (no new
  Google cost).
- **Three columns to compare:** what really happened, today's nearest-first rule replayed, route planning
  replayed. Same figures as above (within the limit, average / 90% / worst wait, waiting bands, driving).
- **Also check the simulation itself:** real stop times, real order bursts (lunch / evening) and real clustering
  versus the random assumptions, and tune `simulate_day.py` to match.
- Fits in `scripts/simulate_day.py` as a `--replay YYYY-MM-DD` option.
