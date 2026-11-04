# Plan 1: Improvements log

Every change to [Plan 1](plan-1-route-sequencing.md) agreed during the work, newest last. Each entry says what
changed, why, and where it's built.

---

### 1. Up to 20 stops per driver (2026-10-23)
- **Decided:** a driver carries up to 20 stops at once; 2 drivers on a typical day.
- **Effect:** a full route is 21 places (driver + 20 stops), within one Google Route Matrix request (limit 625
  pairs) and fast for OR-Tools.

### 2. Address change unlocks the current stop (2026-10-23)
- **Why:** customers change drop-off addresses often; the "locked stop" rule mustn't keep a driver going to the
  old place.
- **Change:** if the address of the order the driver is heading to changes, that stop is unlocked, the route is
  recalculated at once and the driver is told.
- **Built in:** step 4 (re-planning), step 5 (driver message).

### 3. "On the way" exception (2026-10-23)
- **Why:** a new order whose drop-off lies between the driver and their current destination should be done on
  the way, not after driving past it and coming back.
- **Change:** the new stop goes first if **all** hold:
  - detour ≤ `ON_THE_WAY_MAX_DETOUR_MINUTES` (**3**)
  - the current customer is delayed ≤ `ON_THE_WAY_MAX_DELAY_MINUTES` (**8**, includes the time at the new stop)
  - the current stop isn't Urgent and its "deliver by" isn't missed
  - the driver hasn't sent "arriving in N min" for the current stop
  - the driver hasn't tapped Arrived
- Driver message: "🔄 New stop on your way: deliver #52 first, then continue to #41". Setting either limit to 0
  switches it off.
- **Built in:** step 4.

### 4. Traffic: the mix, plus fair "on the way" comparisons (2026-10-23)
- **Why:** live traffic costs twice as much per pair (Pro $10 vs Essentials $5 per 1,000; free 5,000 vs 10,000
  per month), but matters mostly for the leg being driven now.
- **Change:** `ROUTES_TRAFFIC=next-leg`: stop-to-stop times without traffic; live traffic only for the driver's
  next leg and for the **3 legs of an "on the way" decision** (driver → new stop, new stop → current
  destination, driver → current destination), so both options are compared on the same basis.
  `all` / `none` remain possible.
- **Built in:** step 1 (`app/services/travel.py`).

### 5. Remember driving times; only ask for missing pairs (2026-10-23)
- **Why:** each pair is billed; the same trips recur.
- **Change:** times are stored in the database (shared by the API and the bot). Without traffic: kept **30 days**
  (decided; each pair expires on its own and is asked again only when next needed, nothing is wiped). With
  traffic: 5 minutes. Places rounded to ~50 m. Only pairs not already known are requested (a new stop costs its
  row and column, not the whole matrix). A stop's trip "to itself" is never requested (10 stops = 100 pairs).
- **Daily cap:** `ROUTES_DAILY_LIMIT=5000` pairs, then estimates.
- **Built in:** step 1.

### 6. Start routes from the locked or just-delivered stop (2026-10-23)
- **Why:** a "from the driver's position" row is new every time the driver moves, so requesting it at every
  re-plan would cost ~n pairs each time.
- **Change:** while driving, the route is calculated from the locked stop (only driver → locked stop is needed,
  1 live-traffic pair); right after a delivery, the driver's position is taken as the stop just delivered. A full
  driver row is requested only when neither applies.
- **Built in:** steps 3–4.

### 7. Only nearby trips from Google (2026-10-23)
- **Why:** halves the no-traffic pairs; the optimizer almost never puts far-apart stops next to each other.
- **Change:** for each place, Google is asked only about its **8 nearest** other stops (`ROUTES_NEAREST=8`,
  by straight line); farther trips are estimated (or use a remembered Google time if one exists).
- **Built in:** step 1 addition (`travel.matrix(..., nearest=...)`), used by step 3.

### 8. Cost estimate for 50–150 orders per driver per day (2026-10-23)
- 2 drivers, 26 working days, upper estimate (no repeat addresses):
  - with improvements 4–7: **~$250–$750 per month**
  - without "only nearby trips": ~$470–$1,400 per month
  - all traffic-aware would be far more
- Geocoding stays mostly within the free 10,000 per month, since known addresses aren't looked up again.
- Option not chosen (for later): a free self-hosted routing engine (OSRM/GraphHopper) for no-traffic times,
  leaving only live-traffic pairs paid (~$130–$290 per month).

### 9. Step 2 details (2026-10-23)
- `ROUTE_OPTIMIZATION` **off** by default until step 9 (today's behaviour unchanged until tested).
- `deliver_by` is a date and time with time zone; the dispatcher enters a time ("14:00"), meaning today in
  `APP_TIMEZONE` (**Europe/London**, clock changes included).
- **ETA range:** from the ETA to ETA + **2 min + 15%** of the time until arrival; doubled when based on estimates;
  only extends later (customers mind late more than early). E.g. arrival in 40 min: "~14:40 (14:40–14:48)".
- **Time per stop:** `SERVICE_MINUTES=5` until there are **20 deliveries in the last 30 days**; then the typical
  (median) time from Arrived to Delivered.
- Route fields (`route_position`, `planned_arrival_at`, `planned_arrival_latest`, `eta_estimated`) are
  recalculated for **all** of a driver's orders whenever their route changes (accept, edit, cancel, deliver,
  address change, running ahead/behind).

### 10. Optimizer goal: shortest total time (2026-10-24)
- **Decided:** goal **A**, the driver finishes all stops as soon as possible (most deliveries per day with 2
  drivers). Not B (least total customer waiting), which serves groups first but drives more.
- Urgent stops count 10× and missed "deliver by" times carry a heavy cost per minute, so customers in a rush
  or with a deadline are protected.
- Possible later: a setting that also gives customer waiting a small weight (a "mix"); default off.
- **Built in:** step 3.

### 11. Longest wait one hour, set automatically (2026-10-24)
- **Decided:** a customer should wait at most **1 hour**.
- **Change:** a new order without a chosen "deliver by" gets **placed time + `MAX_WAIT_MINUTES`** (**60**;
  0 = off), marked as automatic (`deliver_by_auto`), so the web can show "by 15:40 (1-hour limit)". A time the
  dispatcher chooses wins (earlier or later). Editing an order keeps its deadline unless one is sent; sending
  none (null) removes it.
- Through the optimizer's deadline rule (step 3), every order is planned to arrive within the hour where
  possible; when it can't, the dispatcher is warned (step 6).
- **Built in:** now (orders API, migration 0015).

### 12. Ignore deliveries under 30 seconds when learning the time at a stop (2026-10-24)
- **Why:** a delivery under 30 seconds from Arrived to Delivered is a mistaken tap or a test (the database had
  11 test deliveries of ~18 s), which would make the learned time far too short.
- **Change:** they're left out of the median; the 20-delivery minimum counts only real ones.
- **Built in:** `app/services/eta.py` (`stop_seconds`).

### 13. Exact answers for small routes (2026-10-24)
- **Change:** with up to **7 stops**, every possible order is tried (at most 5,040), which is exact and instant;
  OR-Tools searches above that (time limit 1 s). Tests: on 8-stop routes with deadlines and urgent stops,
  OR-Tools matched the true best (found by trying all 40,320 orders) in every case.
- **Measured:** on random 20-stop routes, the optimized order drives **5–17% less** than today's nearest-first
  rule (4–11 minutes per route), and keeps deadlines nearest-first misses.
- **Built in:** step 3 (`app/services/optimizer.py`).

### 14. Merged into main; remaining steps directly on main (2026-10-28)
- Steps 1–3 (and the decisions above) were merged into `main` (fast-forward, no conflicts) and the database
  brought to migration 0015.
- **Decided:** steps 4–9 are built directly on `main`; safe while `ROUTE_OPTIMIZATION=off`.
- The automatic 1-hour "deliver by" is **switched off** for now (`MAX_WAIT_MINUTES=0` in `.env`) until step 4
  connects it to route planning; set it back to 60 then.

### 15. Step 4 details settled while building (2026-10-28)
- **The delivery list follows the plan:** with `ROUTE_OPTIMIZATION` on, the driver's "Your deliveries" list
  shows the planned sequence instead of re-sorting nearest-first (otherwise the list would undo the plan).
- **"Right after a delivery":** within **10 minutes** of the last delivery, and if GPS doesn't show the driver
  more than **300 m** away, the route is planned from that stop (improvement 6).
- **Arrived stops stay locked**, even if their address is edited (the driver is already at the door); only an
  "On my way" stop is unlocked by an address change.
- **On-the-way and address-change swaps** are recorded in both orders' history ("Set automatically (on the
  way): deliver before #41", "Moved after #52 (on the way)"); a moved stop's "arriving in N min" is cleared.
- **3-minute check:** only drivers heading to a stop are checked (1 live-traffic pair each); more than **3
  minutes** off plan triggers a re-plan.
- Built in: `app/services/planning.py`; triggers in `app/services/dispatch.py`; check in `app/bot.py`.

### 16. A changed address is placed again like a new order (2026-10-28)
- **Why:** a stop's place in the sequence was chosen for its old address. Keeping that place after the address
  changes can send the driver past another stop and back (e.g. A → B → C where B moved just past C), and the
  3-minute stability rule wouldn't fix a small detour like that.
- **Change:** when an address changes, that stop is taken out and fitted into the **best gap for its new
  address**, like a new order; the other stops keep their order (full reshuffle still only if it saves ≥ 3 min
  or keeps a deadline). Example: A → B → C, B moves just past C → **A → C → B**.
- **While driving (unchanged rules):** a later stop moving never changes where the driver is heading; only if
  the stop being driven to itself moves is it placed again (improvement 2), and an Arrived stop stays as it is
  (improvement 15).
- **Built in:** `app/services/planning.py`.

### 17. Step 5 details settled while building (2026-10-28)
- **Decided:** drivers see the approximate time at each stop in their list (`~14:40`); deadlines shown to
  drivers are only times the dispatcher chose (⏰ By 15:00) and 🔴 Urgent, not the automatic 1-hour limit;
  the time at a stop doesn't depend on quantities (so quantity changes don't re-plan).
- **Waiting cards say less than first proposed:** instead of "⏳ Stop 3 of 5, after #41" (which would change
  after almost every delivery and mean editing up to 20 cards each time, hitting Telegram's limits), a waiting
  card says "⏳ Not your next stop yet. Follow 📋 Your deliveries." with no button. Stop numbers and times are
  in the delivery list. So only the card that gains or loses "On my way" is edited.
- **Route messages only when the next stop isn't the one the driver would expect:** delivering a stop and
  moving on to the following one sends nothing; a reshuffle, an on-the-way stop or an address change does.
  When the 3-minute check reorders stops, a fresh list is sent too; when only times shift, nothing is sent.
- **Out-of-sequence "On my way"** (e.g. an old button) is refused: "Please finish #41 first."
- **Built in:** `app/telegram_ui.py`, `app/services/dispatch.py` (`route_changed`, `check_route_drift`),
  `app/services/notifier.py`.

### 18. Step 6: the web app (2026-10-28)
- **Two reply buttons** on a planned order (decided):
  - **Copy reply**: "Hi! Your delivery should arrive around 14:40, between 14:40 and 14:48."
  - **Copy my reply**: the dispatchers' own wording, written once under "My reply text" (stored in the
    database, shared by everyone, up to 500 characters), with `{time}` `{latest}` `{minutes}` `{name}`
    (customer's first name) `{order}` filled in. Hidden until a text is saved.
- **Late pop-up: once, plus once more if it gets 20 minutes worse** than at that first alert (decided). Orders
  already late when the page opens show the red badge only (like the other alerts).
- Orders show "~14:40 (14:40–14:48) · stop 3 of 5", "rough" for estimated times, 🔴 Urgent, "by 15:00", and
  for the dispatcher also the automatic limit ("by 15:40 (1-hour limit)"). Order form: Urgent switch and
  Deliver by time ("Empty: within 60 min"). Drivers page: each driver's route in order.
- **Times on the web are London time, 24-hour** (`APP_TIMEZONE`), whatever the dispatcher's computer clock is
  set to, so they match Telegram and "deliver by" is read correctly. Found while testing: this computer's clock
  is UTC+9. Order history dates use London time too.
- All of it only shows while `ROUTE_OPTIMIZATION` is on.
- **Built in:** `app/routers/app_settings.py` (+ migration 0016), `web/src/lib/route.ts`, `OrderStatus.tsx`,
  `OrderAlerts.tsx`, `OrdersPage.tsx`, `DriversPage.tsx`, `lib/time.ts`.

### 19. Later (not built): a traffic factor for later stops (2026-10-28)
- **Why:** with `ROUTES_TRAFFIC=next-leg`, only the driver's current leg uses live traffic; later stops use
  normal driving times, so on a bad traffic day their ETAs come out too early (softened by the ETA range and by
  corrections as the driver goes).
- **Idea:** compare the current leg's live-traffic time with its normal time (e.g. 13 vs 10 min = traffic 30%
  slower now) and stretch the later stops' driving times by the same factor, capped (e.g. at most 1.5×) so one
  jam on the current street doesn't overreact. No extra Google cost: both times are already fetched every 3
  minutes.
- **Decided:** not now; the user will ask for it later. Fits in `app/services/planning.py` (where the plan for
  the stops after the locked one is evaluated).

### 20. Step 7: ask the driver for an ETA (2026-10-28)
- **Decided:** works **even with route planning off**; answer buttons **3 / 5 / 7 / 10+ min** ("10+" is saved as
  "or later"); **Copy reply uses the driver's answer if it's from the last 15 minutes**, otherwise the planned
  arrival (route planning).
- Web: **Ask driver for ETA** on an order a driver is delivering. After asking: "Asked 1 min ago, waiting for the
  answer…" (button off); after 5 minutes without an answer: "No answer yet" and **Ask again**. The answer shows
  as "Minh says ~15:10 · 2 min ago" and pops up for the dispatcher.
- Telegram: "⏱ The dispatcher asks: when will you reach #52 (12 High Street)?" with the four buttons; after
  tapping, the question shows the answer instead of the buttons. Cleaned up with the order's other messages.
- **Only recorded as asked once Telegram accepts the question**, so the web never waits for an answer to a
  question the driver didn't get (found while building). Drivers without Telegram, or finished orders: refused
  with a message. The answer doesn't change the route; both question and answer are in the order history.
- **Built in:** `app/services/dispatch.py` (`ask_driver_eta`, `driver_answer_eta`), `app/bot.py`,
  `app/telegram_ui.py` (`eta_question`), `POST /orders/{id}/ask-eta`, migration 0017; web `OrdersPage.tsx`
  (`AskDriverEta`), `OrderAlerts.tsx`, `lib/route.ts`.

### 21. Step 8: smarter driver choice (2026-10-28)
- **Decided:** drivers with **GPS online come first** (as before); the offer card stays as it is (no "would be
  stop 3 of 6").
- With route planning on, a new order is offered to the driver who'd **finish the least later** with it fitted
  into the best gap of their route, **as long as all their deadlines are still kept**; if every driver would miss
  one, the one missing them least. Distance breaks ties. Accept / decline / "Send to" unchanged; after a
  decline the next best is asked.
- "Extra time" is how much later the driver finishes (goal A, improvement 10). It doesn't count other customers
  waiting a little longer unless that breaks a deadline, so the **automatic 1-hour limit matters here**: with
  `MAX_WAIT_MINUTES=0`, only chosen deadlines protect customers. Set it back to 60 when route planning is
  switched on (step 9).
- Only the **5 nearest** eligible drivers are worked out (each costs a few Google pairs); an order without a map
  location goes to the nearest driver as before.
- Measured in tests: a busy nearer driver loses the order to a free one 1.5 km away that adds less time (7:28 vs
  8:24); a driver whose customers would be 20 minutes late loses it to one who keeps every deadline.
- **Built in:** `app/services/planning.py` (`offer_cost`), `app/services/dispatch.py` (`next_candidate`,
  `_least_extra_time`).

### 22. Step 9 so far: usage counter, simulated day, open decisions (2026-10-28)
- **Built:** "Google driving times today: N of 5,000 pairs" on the Drivers page (route planning on);
  `scripts/simulate_day.py` compares today's nearest-first rule with route planning on a random day
  (`--detail` for waiting bands and each hour).
- **Results saved** in [plan-1-simulation.md](plan-1-simulation.md), to compare with real days later (decided).
- **Open:** how to behave when overloaded (A fairness / B most within the hour / C balance), and the real area,
  stop time and orders per day.
- **Still to do in step 9:** the driver simulator (decided instead of a second Telegram account), the real test
  with Daniel set inactive meanwhile (agreed), then switching on (`ROUTE_OPTIMIZATION=true`,
  `MAX_WAIT_MINUTES=60`).

### 23. Overload rule B: most customers within the hour (2026-10-30)
- **Decided:** rule **B** (of A fairness / B most within the hour / C balance), with a fixed cost per late customer
  of **60 minutes'** lateness (`LATE_CUSTOMER_PENALTY_MINUTES`, 0 = rule A).
- **Change:** the optimizer adds that cost for each customer who'd miss their "deliver by"; for routes over 7 stops
  OR-Tools' answer (cost per late second only) is improved by a short local search (move / swap stops) on the full
  cost. Driver choice puts the fewest newly late customers first (`Offer.key`), and a reshuffle counts as keeping a
  deadline when fewer customers are late (`fewer_late`). The simulation compares today, A and B.
- **Results** (simulated, 2 drivers): see [plan-1-simulation.md](plan-1-simulation.md), section 1b. Overloaded
  (140/day): 74–77% within the hour (A 46–50%, today 63–65%); the price is a few very long waits (worst 384–561 min).
- **Built in:** `optimizer.py`, `planning.py` (`fewer_late`, `Offer.key`), `dispatch.py` (`_least_extra_time`),
  `scripts/simulate_day.py`. On branch `plan-1-overload` (on top of `plan-4-meeting-points`, tested in the plan4 test
  copy).

### 24. Test week: drivers recorded by the dispatcher, handing over (2026-10-30)
- **Why:** real days are needed to judge rule B, but the real drivers use Telegram for real work and can't use a
  test bot.
- **Change:** a driver can be **recorded by the dispatcher** (no Telegram, never offered orders automatically); on the
  order the dispatcher records **Given to driver** ("Send to" them on a new order does it at once), **Driver says N
  min** and **Delivered**, each with the time. **Give to another driver** (any driver, while they haven't reached the
  stop) hands an order over, e.g. at the 17:00 shift change; a Telegram driver losing it is told and their messages
  about it deleted, one getting it receives the order card. Migration 0022. Guide: [plan-1-test-week.md](plan-1-test-week.md).
- **Built in:** `dispatch.py` (`record_given`, `record_driver_eta`, `record_delivered`, `give_to_driver`), `orders.py`
  (`/record/*`, `/give`), Drivers page switch, `RecordForDriver` on the order.

### 25. Addresses in the delivery area (2026-10-30)
- **Why:** dispatchers type addresses loosely ("Prince of wales", "1 Sussex road", "Tesco fernside avenue"); the
  lookup searched the whole UK, so some landed in other towns and pubs weren't found.
- **Change:** `ADDRESS_AREA` (Hastings: `50.868,0.578,6`): the address lookup prefers the area and never lands outside
  it; when it finds nothing, or only the street while the text also names a place, places are searched by name in the
  area (Places API (New)). The form stops once for "not found" or "street only" with **Pick on the map** (also next
  to the coordinates); suggestions favour the area; a hint says instructions go in Notes. Yesterday's 16 addresses:
  12 exact, 3 by name, 3 street only, none wrong (before: 4 in other towns, 3 not found).
- **Built in:** `geocoding.py` (`find`, `find_place`, `delivery_area`), `/orders/locate` (precision),
  `MapPicker.tsx`, `OrdersPage.tsx`. Not Plan 1 as such, but needed for the test week; useful for live too.

