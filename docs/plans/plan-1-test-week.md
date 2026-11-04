# Plan 1: test week with real days (from 2026-10-30)

The dispatcher works as usual (WhatsApp, Telegram) and records the same day in the **test copy** alongside, so real
days can be replayed with route planning rule B afterwards. Drivers aren't involved: nothing is sent to them.

## Set-up (once)
- Test copy: **https://dispatch.dispatchflow.site** (from 2026-10-30 the live app is **stopped** and that address points
  to the test server; the SSH tunnel to 8190 no longer keeps you logged in, the login cookie is HTTPS-only).
  **Back to live after the week:** in `/etc/nginx/sites-available/dispatch.dispatchflow.site` set `proxy_pass` back to
  `http://127.0.0.1:8090`, `nginx -t && systemctl reload nginx`, then `docker compose start` in
  `/home/oogway/telegram-dispatch-system`. Live backup of 2026-10-30: `/root/backups/dispatch-live-20261030-1233`.
  Route planning is **off** there
  during the week: the drivers don't follow it, so its planned order and times would only confuse; rule B is applied
  in the replay instead. Data emptied for the week on 2026-10-30 (backup of the earlier test data kept).
- **Drivers:** add "Driver 1" (12:00–17:00) and "Driver 2" (17:00–22:00) with **Recorded by the dispatcher (no
  Telegram)** on. Set the test driver Daniel **inactive**, or orders without a driver are offered to his Telegram.
- **Products / customers:** as needed; customers' addresses are remembered after their first order.

## Each order (each tap records the time)
| In real life | In the test copy |
|---|---|
| Order arrives on WhatsApp | **New order**: customer, address, items; **Send to: Driver 1 (you record)** = given now |
| Sent to the driver on Telegram (if not chosen above) | Order → **Given to driver** |
| You ask the driver when they'll get there | Order → **Asked for ETA** |
| Driver says "after 15 minutes" | Order → **15 min · Driver says** |
| Driver says "here" | Order → **Arrived** |
| Driver replies "done" | Order → **Delivered** (tap twice) |
| Shift change, driver hands over | Order → **Give to another driver** (before Arrived) |

## Addresses
- Type the address only; instructions ("meet at the bumpy bit", "bottom end") go in **Notes for the driver**.
- Addresses are looked up in the delivery area first (`ADDRESS_AREA`): "1 Sussex Road" is St Leonards', pubs and shops
  are found by name ("Railway pub", "Tesco Fernside Avenue").
- **Not found**, or **only the street found**: the form stops once, with **Pick on the map**. Saving again goes ahead
  (without a location, or with the street).
- Yesterday's 16 addresses (2026-10-29): 12 exact, 3 found by name, 3 street only (Blomfield Road, Kenilworth Road,
  Crowhurst Road), 0 wrong; the old lookup put 4 in other towns and found 3 nowhere.

## After the week
The orders, their recorded times and the drivers are exported and replayed: today's rule, B, and B with a second
driver at busy hours, against what really happened (plan-1-simulation.md, part 2).
