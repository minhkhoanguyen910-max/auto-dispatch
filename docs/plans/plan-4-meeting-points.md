# Plan 4: Meeting points

Status: **in progress** on branch `plan-4-meeting-points` (planned 2026-10-29, revised the same day: the driver
asks for a meeting point; no automatic suggestions). Steps 1-6 done (walking map; data, migrations 0020-0021;
the driver asks in Telegram; the dispatcher asks the customer; planning and the driver use agreed meeting points;
"Same place as last time", 2026-10-29); step 7 (final check) left, not merged into `main` yet. To be built on the Linux server (where OSRM runs), by Claude Code running there.

## For whoever builds this

- Read this whole file first; the **Decisions** list is the user's and isn't re-argued. Background:
  `docs/plans/plan-1-route-sequencing.md` and `plan-1-improvements.md` (route optimization, built but off) and
  `plan-3-live-map.md` (map, OSRM road paths).
- One step at a time: explain the step to the user in plain words, wait for their agreement, then build it.
- **Branch `plan-4-meeting-points`**, worked on in its own folder (`git worktree`, e.g.
  `../telegram-dispatch-system-plan4`), never by switching branches in the live folder: the live app runs from
  that folder on `main`, and `docker compose up` there deploys whatever is checked out. Test containers run
  under their own compose project name (`docker compose -p plan4 ...`) so they never replace the live ones.
- Test copy for the browser: `docker compose -p plan4 -f docker-compose.yml -f docker-compose.plan4.yml up -d
  --build db traccar api web bot` (maps: add `osrm-download osrm osrm-foot`). Other ports; its own bot
  (@ForTestDispatchBot): plan4's `.env` is its own file with the test bot's token, never the live bot's (one reader
  per bot). Open the web app through an SSH tunnel to port 8190.
- After each finished and tested step, commit and push the branch and update the Status line here. Merge into
  `main` (and deploy) only when the user says so.
- Test with `ROUTE_OPTIMIZATION` off and on. Use test drivers without Telegram so no real driver gets messages.
- Only one copy of Claude should work on this plan at a time (pull before starting), to avoid clashing changes.

## The idea

Some addresses are close to the driver in a straight line but far by car: one-way streets, pedestrian zones,
railways (example: Hastings town centre, about 250 m apart but about 2 km by car). For those, the customer can
walk a few minutes to a spot the car reaches easily. **The driver asks for it** on a stop they know is hard to
reach, picks the spot (their own, or the same place as last time), the dispatcher asks the customer, and only when the
customer agrees does the driver go there.

## Decisions (user, 2026-10-29)

1. **The driver asks.** Nothing happens unless the driver requests a meeting point on a stop; the app never
   asks the driver on its own. (Replaces the earlier automatic suggestions and asking drivers while driving.)
2. **The dispatcher asks the customer** with a ready-made message (copied, like "My reply"); no automatic SMS.
3. **Limits:** the customer walks at most **250 m**. The saving is shown to the driver and the dispatcher; below
   `MEETING_MIN_SAVING_MINUTES` (default 10, judged on the middle of the time range) it's shown as a warning,
   not blocked.
4. **Customer not there:** the driver waits at most **5 min**, then drives to the door.
5. **OSRM finds and measures, Google decides:** the free self-hosted OSRM (car and walking road maps) looks for
   spots and measures distances and walks; Google with live traffic gives the driving times for the saving.
   OSRM's own times have no traffic and are too low in town centres, so they're never shown as the saving.
6. **The whole trip counts:** a spot for stop B is judged on A -> B -> C (previous and next stop), not A -> B.
7. **The driver makes the final decision** on the spot: they know the streets and they have to stop there. The
   dispatcher doesn't check spots; they only talk to the customer. No Street View photos needed.
8. **Drivers' choices and outcomes are saved** (each spot: how often chosen, worked, customer not there).
9. **The customer is bothered as little as possible:** asked at most once per order; an agreed spot never
   moves; a customer who declines **2** times becomes "always deliver to the door" (the dispatcher can change
   it), and the driver's request is then refused straight away; a spot that worked before for this customer
   **at this destination** is offered first ("Same place as last time?", see decision 12).
10. **10 minutes' notice for the customer, as a warning:** the dispatcher's message should reach the customer
    at least 10 min before the driver gets to the spot. A later request isn't refused: the driver sees "⚠️ Only
    about 6 min before you get there. The customer may not have time. Ask anyway?" and the dispatcher sees
    "Short notice: 6 min" (to call rather than text). Drivers can ask early, on any upcoming stop.
11. **After "Arrived": "I'm parked here".** Once the driver has tapped Arrived (e.g. parked 200 m from a
    pedestrian zone), they can still ask, with their current location as the spot: **[📌 I'm parked here, ask
    the customer to come out]**. The dispatcher sees it as urgent ("Driver is waiting now: call the
    customer"); the 5-minute "Customer not here?" timer applies, then the driver walks to the door. Drivers
    don't see customers' details, so this goes through the dispatcher like any request. **Refused only once
    the stop is delivered.**
12. **No suggested spots (2026-10-29):** the app doesn't search for spots; the driver knows the streets and sends
    their own (pin, typed position or Google Maps link). The only offer is **[🔁 Same place as last time]**:
    customers' addresses change, so it's the spot that worked (delivered there) for **the same customer and the
    same destination** (a door within 50 m), the one that worked most often first, then the most recent. Not
    other customers' spots at the same address.
13. **Start with "I'm parked here" only (2026-10-29):** `MEETING_POINTS=parked` (the default) offers only
    [📌 I'm parked here, ask the customer to come out] after Arrived, to try it with real drivers first;
    `MEETING_POINTS=all` also switches on [📍 Meet the customer instead] (and "Same place as last time"). An
    "I'm parked here" spot counts as worked once delivered, even if nobody tapped "Customer is coming".
    **No 250 m limit for "I'm parked here"** (the driver is already parked and can't do better): a longer walk is
    shown with a warning, and a spot off the walking map gets a straight-line estimate. Spots chosen in advance
    ("Meet the customer instead") keep the 250 m limit. If nobody answers an "I'm parked here" request, the bot
    still asks "Customer not here?" 5 min after it (tapping it records "customer not there"); after a late
    "Customer is coming" it waits 5 min again. Declines of "I'm parked here" count toward "always to the door".

## How the comparison works

The driver comes from A (previous stop, or GPS for the first stop) and goes on to C (next stop). For stop B:

- **Door:** Google's driving time with traffic A -> B -> C, using the nearest point the car can reach for B,
  **plus** the driver's walk to the door and back when the address isn't on a road (e.g. a pedestrian zone).
- **Meeting point X:** Google's driving time with traffic A -> X -> C. The customer's walk happens while the
  driver drives, so it doesn't add to the driver's time.
- **Saving** = door minus meeting point, shown as a range ("saves about 10-14 min"); distances shown exactly.
- **Last stop** (no C): A -> B against A -> X.

## How it goes for one order

1. **Driver (Telegram):** on any upcoming stop, **[📍 Meet the customer instead]**, then
   the spot: a location pin, a typed position or a Google Maps link, or **[🔁 Same place as last time]** when
   there is one (decision 12); the bot shows the walk and the saving to confirm. Refused straight away if the
   customer is "always to the door", was already asked for this order, or the stop is delivered; a warning,
   not a refusal, when there's less than 10 min left for the customer. After "Arrived" the button is
   **[📌 I'm parked here, ask the customer to come out]** (decision 11).
2. **Dispatcher (web):** the order shows "Driver asks to meet at Queens Rd (saves about 10-14 min)" and
   **"Send to the customer by 14:15"** with a countdown (or **"Short notice: 6 min"** when it's late), and "Copy message for the customer" (spot, Google Maps
   link, planned time when route optimization is on). Buttons **Customer agreed** / **Customer declined**. Not
   answered by the deadline: it expires.
3. **Driver:** agreed -> "✅ The customer will meet you at Queens Rd at about 14:25", the stop moves to the spot
   and the route is re-planned. Declined or expired -> "🚪 Deliver to the door as usual".
4. **At the spot:** after "Arrived", if the order isn't delivered within 5 min, the bot asks "Customer not here?
   Drive to the address"; the button switches the stop back to the door and re-plans. The dispatcher sees it.

## Working with Plan 1 (route optimization)

Plan 1 is built and switched off (`ROUTE_OPTIMIZATION=false`); Plan 4 is built on it and tested both ways, so
switching optimization on later needs no change to Plan 4.

1. **One rule for where a stop is:** the agreed meeting point if there is one, otherwise the address. Every place
   in planning and dispatch that reads the address location uses this rule instead.
2. **A and C** come from the driver's current list when optimization is off, from the optimized order when on.
3. **Agreed meeting points stay** when the order of stops changes; a request still waiting for the customer
   keeps its spot, and its deadline is recalculated from the new arrival time.
4. **The time in the customer's message** comes from Plan 1's ETA when optimization is on; when off, the message
   has no time (or the dispatcher types one).
5. **Waiting for the customer doesn't count as stop time:** meeting-point deliveries are left out of the average
   time at a stop that Plan 1 learns from past deliveries.
6. **Tests** run with optimization off and on.

## Steps

1. **Walking road map.** A second OSRM service with the walking profile (footpaths, pedestrian zones, both
   directions on one-way streets), built from the same OpenStreetMap download as the car one; the download is
   kept until both are built, and a server that already has the car map gets the walking map without losing
   the car map meanwhile. New setting `OSRM_FOOT_URL` and a `walk()` function next to `route()`.
2. **Data.** Known spots (position, name, how often chosen / worked / customer not there). Each order: its
   meeting point, the walk, the saving range, the deadline, and a status: *requested*, *asked*, *agreed*,
   *customer declined*, *expired*, *customer not there*, *delivered*. Each customer: **"Always deliver to the
   door"** and their decline count.
3. **The driver asks (Telegram).** [📍 Meet the customer instead] on any upcoming stop, with **Send my own
   spot**; the refusals in decision 9, the short-notice warning in decision 10, "I'm parked here" after
   Arrived (decision 11). The web shows the request with the walk and the saving.
4. **Asking the customer (web).** Deadline and countdown, "Copy message for the customer", Customer agreed /
   declined, expiry, the decline count.
5. **Planning uses agreed meeting points** (rule 1 above) and the driver's Telegram stop shows the spot with a
   navigation link; the 5-minute "Customer not here?" button.
6. ~~Suggest spots~~: dropped (decision 12). Instead **"Same place as last time"** for the same customer and
   destination.
7. **Tests:** automatic tests for each step (optimization off and on); a check with real Hastings addresses
   (including whether the saving there really reaches 10 min with Google's times).

### Later

8. **Check against reality:** compare predicted savings with the drivers' real GPS times for meeting-point
   deliveries, and adjust the settings if the predictions are off.

## Open questions

None.
