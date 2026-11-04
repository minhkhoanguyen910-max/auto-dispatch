# Plan 1: Automatic route sequencing and ETAs

Status: **in progress**, steps 1–3 merged into `main` on 2026-10-28; the remaining steps are built directly on `main` (decided 2026-10-28), behind `ROUTE_OPTIMIZATION=off`. Each step is agreed before it's built. The automatic 1-hour limit is switched off in `.env` (`MAX_WAIT_MINUTES=0`) until step 4 uses it.

Progress: step 1 done (driving times), step 2 done (database fields and settings), step 3 done (optimizer), step 4 done (automatic re-planning, 2026-10-28), step 5 done (Telegram: drivers follow the sequence, 2026-10-28), step 6 done (web app, 2026-10-28), step 7 done (ask the driver for an ETA, 2026-10-28), step 8 done (smarter driver choice, 2026-10-28). Step 9 in progress: usage counter and simulated day done (results in [plan-1-simulation.md](plan-1-simulation.md)).

**Paused 2026-10-28, waiting for the user:**
1. ~~Overload behaviour~~: **decided B** (most customers within the hour), 2026-10-30; built on branch
   `plan-1-overload` (see plan-1-improvements.md, 23).
2. ~~Real figures~~: given 2026-10-30 (about 2.2 km, 5 min per stop, 70 / 100 orders a day); results in
   plan-1-simulation.md, section 1c.
3. Then: build the driver simulator, run the real test (Daniel set inactive meanwhile), switch on
   (`ROUTE_OPTIMIZATION=true`, `MAX_WAIT_MINUTES=60`).
4. After real use: replay real days and compare with the saved simulated results (plan-1-simulation.md, part 2).

## Decisions so far

- Drivers may **not** change the delivery sequence. The system decides it automatically, by calculation.
- The optimizer minimizes **time** (traffic-aware driving time + time at each stop), not distance. Distance
  is only a tie-breaker.
- **The stop the driver is heading to never changes** (after "On my way" / "Arrived"); only the stops after
  it can be reordered. **Exception:** if that order's drop-off address changes (customers change addresses
  often), the stop is unlocked, the route is recalculated at once and the driver is told.
- **"On the way" exception (agreed):** when a driver heading to a stop gets a new order that lies on their
  way, the new stop goes first if **all** hold: detour ≤ `ON_THE_WAY_MAX_DETOUR_MINUTES` (default 3); the
  current customer is delayed ≤ `ON_THE_WAY_MAX_DELAY_MINUTES` (default 8, includes the time at the new
  stop); the current stop isn't Urgent and its "deliver by" isn't missed; the driver hasn't sent "arriving in
  N min" for it; the driver hasn't tapped Arrived. The 3 legs involved are compared with live traffic.
  Driver message: "🔄 New stop on your way: deliver #52 first, then continue to #41". 0 switches it off.
- A new **sequence** is applied only when it saves at least a threshold (default 3 minutes), or a deadline
  would otherwise be missed, or a stop was removed. ETAs always update.
- Optimizer goal: **shortest total time** (driver finishes soonest); Urgent ×10, missed "deliver by" heavily
  penalized.
- **Longest wait 1 hour:** new orders get "deliver by" = placed + `MAX_WAIT_MINUTES` (60) automatically unless
  a time is chosen.
- Solver: **Google OR-Tools** (free, local). Driving times: **Google Routes API (Route Matrix)**; without a
  key, a rough estimate from straight-line distance.
- **Traffic: the mix (agreed)**, `ROUTES_TRAFFIC=next-leg` (`all` / `none` also possible). Stop-to-stop
  times without traffic, kept 30 days (they don't change); live traffic only for the driver's next leg and the
  3 legs of an "on the way" decision. Daily cap `ROUTES_DAILY_LIMIT=5000` pairs, then estimates.
  Estimate for 2 drivers (~30 orders each per day): ~$150/month (all traffic-aware would be ~$950).

## Answers

1. Stops per driver at once: **up to 20** (decided). Drivers on a typical day: **2**.
2. Time windows: **"Urgent"** and **"deliver by HH:MM"** (no "after HH:MM" yet) (default, accepted).
3. Vehicle limits (items/weight per trip): **none** (default, accepted).
4. Moving an already-accepted order to another driver: **no**; accept/decline stays as it is (default, accepted).

## Steps

### 0. Prerequisites
- Google key working, with **Geocoding API** and **Routes API** enabled.
- ~~Settle the open questions above.~~ Done.

### 1. Driving times (`app/services/travel.py`)
- Driving time in seconds between every pair of points (driver position + stops).
- With the key: Google Route Matrix (traffic per the mix above). Without it (or if Google fails, or the
  daily cap is reached): straight-line distance × 1.3 at `FALLBACK_SPEED_KMH` (25), marked "estimated".
- Cache in the database (shared by the API and the bot): no-traffic pairs 30 days, traffic pairs 5 minutes;
  places rounded to ~50 m. Only missing pairs are requested.
- Stops without coordinates go last and are flagged on the web.

### 2. Database
- Orders: `is_urgent`, `deliver_by`, `route_position`, `planned_arrival_at`, `planned_arrival_latest`.
- Drivers: reuse `list_order_ids` as the official route; add `route_planned_at`.
- Orders also get `eta_estimated` (ETA based on estimates, shown as "rough").
- `deliver_by`: date and time with time zone; entered as a time meaning today in `APP_TIMEZONE`
  (Europe/London).
- Settings (`.env`): `ROUTE_OPTIMIZATION=off` (until step 9), `SERVICE_MINUTES=5` (learned after 20 deliveries
  in 30 days: median Arrived → Delivered), `REROUTE_THRESHOLD_MINUTES=3`, `ON_THE_WAY_MAX_DETOUR_MINUTES=3`,
  `ON_THE_WAY_MAX_DELAY_MINUTES=8`, `ROUTES_NEAREST=8`, `APP_TIMEZONE=Europe/London`.
- ETA range: ETA → ETA + 2 min + 15% of the time until arrival (doubled for estimates).

### 3. Optimizer (`app/services/optimizer.py`, OR-Tools)
- In: driver GPS position, remaining stops (current stop locked first), driving times, time per stop,
  urgent flags, deadlines.
- Out: best sequence + arrival time for every stop (= the ETAs).
- Deadlines respected; if impossible, report by how much ("earliest ~14:20") instead of failing. Urgent =
  large cost per minute of delay.
- Time limit ~1–2 s per solve. Unit tests with fixed driving times.

### 4. Automatic re-planning (`replan(driver)`, replaces nearest-first in `app/services/route.py`)
- Runs when: an order is accepted / edited (address, location) / canceled / delivered; urgent or deadline
  changes; GPS shows the driver ahead/behind plan by more than a few minutes (checked every 2–3 min by the
  bot worker, not on every GPS ping).
- Stability rules as in "Decisions so far".
- One re-plan per driver at a time (database lock).

### 5. Telegram: drivers follow the sequence
- Only the **next stop** shows "🚚 On my way"; others show "⏳ Stop 3 of 5, after #41".
- Server-side check in `driver_set_status`: out-of-sequence taps get "Please finish #41 first".
- "📋 Your deliveries" is read-only, in planned sequence with approximate times.
- On a sequence change, one message: "🔄 Route updated: next stop is now #45" (none if only times shift).
- 🔴 Urgent and "by 14:00" shown on the order.

### 6. Web app
- Orders list/details: "~14:40 (14:30–14:50) · stop 3 of 5 · Driver X" ("rough" without Google);
  pending orders: "Waiting for a driver".
- **Copy reply** button: "Hi! Your delivery should arrive around 14:40, roughly 25 minutes."
- New/edit order: **Urgent** switch and optional **Deliver by** time.
- Warning (and pop-up) when a deadline becomes impossible: "Can't make 14:00, earliest ~14:20".
- Drivers page: each driver's route in sequence with planned times.

### 7. Ask the driver for an ETA
- Web button → Telegram message with 10 / 20 / 30 / 45+ min buttons → answer shown next to the calculated
  ETA, with a pop-up (reuses the "arriving soon" mechanism).

### 8. Smarter driver choice (insertion cost)
- For a new order, compute each eligible driver's **extra time** if they took it (steps 3–4); offer it to the
  driver with the smallest increase that keeps every deadline; distance as tie-breaker.
- Evaluate only the nearest few drivers (Google cost). Offer/accept/decline unchanged.

### 9. Testing and rollout
- Optimizer tests with known answers; simulation replaying past orders vs. today's nearest-first rule.
- API tests inside a rolled-back transaction (no test orders reach real drivers).
- Telegram test with a test driver account.
- `ROUTE_OPTIMIZATION=on/off` switch; watch Google usage; tune the threshold.

## Order and dependencies

| Step | Depends on | Works without the Google key? |
|---|---|---|
| 1–4 Driving times, database, optimizer, re-planning | – | Yes, with rough estimates |
| 5 Telegram sequence lock | 4 | Yes |
| 6 Web ETAs, urgent, deadlines | 4 | Yes, marked "rough" |
| 7 Ask driver for ETA | – | Yes |
| 8 Smarter driver choice | 3–4 | Possible, but meant for real driving times |
| 9 Testing and rollout | all | Precise results need the key |

All agreed changes are logged in [plan-1-improvements.md](plan-1-improvements.md).

Suggested: build 1–7 first and test with estimated times; switch on Google driving times once the key works;
then step 8.

**Google cost:** Route Matrix is billed per start→stop pair; a driver with 6 stops needs at most ~49 pairs per
re-plan, fewer with caching. Set a daily quota cap on the Routes API.

## Background (why these rules)

- **Locked current stop:** changing a destination mid-drive wastes the distance already driven and breaks
  promises already made to that customer.
- **Threshold:** frequent re-plans can flip the sequence back and forth for gains of seconds; drivers then
  can't plan and stop trusting the list.
- **Time, not distance:** 5 km on a fast road can be quicker than 2 km in town; customers ask "when?", and
  deadlines are times.
- **"Least extra time" driver choice:** the nearest driver may already have a full route, so adding the order
  would make their other customers late; a farther driver with a lighter route can be better overall.
