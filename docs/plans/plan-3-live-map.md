# Plan 3: Live map of drivers

Status: **in progress** (started 2026-10-28). Each step is agreed before it's built.

Progress: step 1 done (browser key `Dispatch web map`, restricted to `http://localhost:8090/*` and
`http://127.0.0.1:8090/*` and the Maps JavaScript API, plus Map ID `Dispatch map` (JavaScript, vector, no tilt /
rotation); both in `.env` and checked with a real map load). Step 2 done (heading and speed saved from Traccar,
knots → km/h; "moving" from 5 km/h; key and Map ID given to the web; migration 0018). Step 3 done (Map page, 2026-10-28). Step 4 built (QR set-up, 2026-10-28), waiting for the user's test on an
iPhone.

Step 4 details:
- **Decided:** report every **30 s or 30 m**, high accuracy (`TRACCAR_INTERVAL_SECONDS`, `TRACCAR_DISTANCE_METERS`,
  `TRACCAR_ACCURACY`); Traccar Client's own default is 300 s / 75 m.
- **Confirmed in Traccar Client's source code** (github.com/traccar/traccar-client): the app registers the
  `org.traccar.client://` link on iPhone and Android; opening such a link asks the driver to confirm, then applies
  `url`, `id`, `interval` (seconds), `distance` (meters), `accuracy` (highest / high / low). So the QR code holds
  `org.traccar.client://config?url=…&id=…&interval=30&distance=30&accuracy=high` and is scanned with the phone's
  camera ("Open in Traccar Client").
- Built in: `DriversPage.tsx` (`GpsSetup`, `traccarSetupLink`, `SetupQr`; npm `qrcode` 1.5.4), `/drivers/setup-info`.

Step 3 details settled while building:
- **One colour per driver** (instead of "green when online"), so each driver's numbered pins match them; grey still
  means GPS offline.
- **The mouse wheel zooms the map directly and one finger moves it on phones** (Google's default needs Ctrl+wheel
  on a page that can scroll, awkward for a full-page map).
- Markers move in place every 5 s (one Google map load per visit); "Fit all" zooms to everything, otherwise the
  map stays where the dispatcher put it; legend hidden on phones to leave room for the map.
- Built in: `web/src/pages/MapPage.tsx`, `web/src/lib/maps.ts` (loads Google Maps the recommended async way;
  shows a clear message if the key is refused or not set), menu item "Map".

## Decisions
1. **Google Maps** (not OpenStreetMap): the Google account, billing and key already exist; addresses are
   geocoded with Google; free live traffic layer. Cost: 10,000 map loads free per month, counted when the page
   is opened (not when markers move); a few dispatchers stay well within it.
2. **Orders on the map too:** numbered drop-off pins for each driver's stops (in route order), yellow pins for
   orders waiting for a driver.
3. **No trail of past positions for now** (would need storing every position); maybe later.
4. **Traccar Client frequency set up for the drivers by the app** (user's request): the Drivers page shows a
   **QR code / setup link** per driver that fills in Traccar Client's server address, device identifier and
   reporting frequency, instead of typing them.

## Plan
1. **Google Cloud (user, ~5 min, with instructions):** enable the **Maps JavaScript API**; create a **second key
   for the browser**, restricted to the web app's address (HTTP referrer, e.g. `http://localhost:8090/*` and the
   future domain) and to the Maps JavaScript API only. Put it in `.env` as `GOOGLE_MAPS_BROWSER_KEY`. (The
   existing key stays server-only.)
2. **Backend:** also save Traccar's **course (direction) and speed** with each position (`drivers`), so the map
   shows an arrow and moving / stopped; give the web the browser key via `/app-settings`.
3. **Web: a new "Map" page** in the menu:
   - a marker per driver with their name: green = GPS online, grey = offline ("last seen 25 min ago"), arrow
     when moving;
   - click a driver: phone, the stop they're heading to with its ETA, number of stops;
   - drop-off pins numbered in each driver's route order (route planning on; otherwise in order), yellow pins
     for orders waiting for a driver; click a pin to open the order;
   - fits all drivers in view; **traffic on/off** switch; refreshes every 5 seconds; full screen on phones.
4. **Traccar Client set-up by QR code (decision 4):** on each driver's setup tab, a QR code / link with the
   server URL (`TRACCAR_CLIENT_URL`), their device identifier and the recommended reporting settings
   (every **15–30 s**; Traccar's maintainer also recommends a **distance** setting, since interval-only reporting
   was unreliable in the newer app). Traccar Client's documented link format is
   `org.traccar.client://config?url=…&id=…&interval=30` (parameter names to be confirmed on a real phone with the
   current app version, since forum posts differ). Plus clear manual steps as a fallback.
5. **Tests:** test drivers at known positions in a test copy (no Telegram), browser check on desktop and phone;
   QR code checked on a real phone by the user.

## Notes
- **"Live" depends on the phones:** positions update as often as Traccar Client sends them (its default can be
  minutes). 15–30 s uses more battery.
- Sources for the QR / link set-up: Traccar forum threads
  ["Deeplinking question about lib/configuration_service.dart"](https://www.traccar.org/forums/topic/deeplinking-question-about-libconfigurationservicedart/),
  ["Question about configuring the new app"](https://www.traccar.org/forums/topic/question-about-configuring-the-new-app/),
  ["QR code configuration guide"](https://www.traccar.org/forums/topic/qr-code-configuration-guide-how-to-add-mode-option-to-qr/).

## Extra: addresses that aren't on the map (2026-10-28)
Found while testing: order "Abbey Load" (a typo) got no location, so it had no pin, was dispatched without
knowing who's nearest, and nobody was told. Agreed with the user (warn, don't hold the order):
- **Address suggestions while typing** (Google Place Autocomplete, Places API (New) on the browser key) in the
  order form; the customer's recent addresses still come first. A picked suggestion brings its location.
  Cost: suggestions 10,000 free a month then $2.83 / 1,000 (at most 12 billed per address), the picked
  address 10,000 free then $5 / 1,000; it saves the Geocoding call for that order.
- **Check before saving** a hand-typed address (`POST /orders/locate`); if Google can't find it, the form warns
  and a second press saves it anyway.
- **"Not on map"** label on orders and a note above the Map listing them.
- **Bug fixed:** Geocoding wasn't limited to the UK ("Abbey Loadx" landed in California), and an unknown
  address came back as the middle of the country. Now `ADDRESS_COUNTRY` (default GB) limits look-ups and
  suggestions, and country / region-only answers count as "not found".
