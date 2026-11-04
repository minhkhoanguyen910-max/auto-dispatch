# Auto Dispatch

Dispatchers enter orders on a web page; the backend offers each order to the nearest driver
over Telegram. Drivers report GPS with the Traccar Client app and update statuses in the bot.

**Stack:** React (Vite, Mantine) · FastAPI · aiogram · PostgreSQL + PostGIS · Traccar · Google Maps Platform

## Services

| Service   | Port (host)            | Purpose                                         |
|-----------|------------------------|-------------------------------------------------|
| `db`      | `127.0.0.1:5450`       | Postgres: `dispatch` (app) and `traccar` DBs    |
| `traccar` | `127.0.0.1:8082` UI, `5055` GPS | Receives phone GPS, forwards positions to `api` |
| `web`     | `127.0.0.1:8090`       | React app (nginx); proxies `/api/*` to `api`    |
| `api`     | `127.0.0.1:8100`       | FastAPI; runs migrations on start               |
| `bot`     | -                      | Driver Telegram bot (polling)                   |
| `osrm`    | -                      | Road map: the live map moves drivers along roads between GPS fixes |
| `osrm-foot` | -                    | Walking map: how far a customer walks to a meeting point |

`osrm` and `osrm-foot` use OpenStreetMap roads for `OSRM_REGIONS` (`.env`). The first start downloads and
prepares them (`osrm-download`, then a few minutes in `osrm` and `osrm-foot`); until then, and wherever a fix
can't be matched to a road, drivers move in straight lines. To cover other areas, change `OSRM_REGIONS` and run `docker compose up -d`.

## Setup

```bash
cp .env.example .env   # fill in the values
docker compose up -d --build
curl localhost:8100/health

# First web login: create an admin (asks for a password)
docker compose exec api python -m app.cli create-admin --email you@example.com --name "Your Name"
```

The web app and Traccar's UI only listen on the server itself. Put a reverse proxy with HTTPS in front of
`127.0.0.1:8090` (e.g. a host nginx site plus a Let's Encrypt certificate from certbot), set
`COOKIE_SECURE=true` in `.env`, and open `https://<your-domain>`. Admins can add more users on the **Users** page.
For Traccar's UI, use an SSH tunnel: `ssh -L 8082:localhost:8082 <user>@<server>`, then `http://localhost:8082`.

## Running without Docker (Windows)

Same services, installed directly on the machine:

| Docker service | Without Docker                                                         |
|----------------|------------------------------------------------------------------------|
| `db`           | PostgreSQL + PostGIS on `localhost:5432`                               |
| `traccar`      | Traccar for Windows (Windows service), ports `8082` and `5055`         |
| `api`          | `uvicorn` from `.venv` on `127.0.0.1:8100`                             |
| `web`          | `npm run preview` on `8090` (serves `web/dist`, proxies `/api` to the API) |
| `bot`          | `python -m app.bot` from `.venv`                                       |

**Install once:**

1. [Python 3.11+](https://www.python.org/downloads/) and [Node.js](https://nodejs.org/).
2. [PostgreSQL](https://www.postgresql.org/download/windows/). Note the `postgres` password you choose.
   Then open **Stack Builder** (Start menu → PostgreSQL), pick your server, and install
   **Spatial Extensions → PostGIS bundle**.
3. [Traccar for Windows](https://www.traccar.org/download/) (it bundles its own Java).

**Set up** (creates the database role and databases, enables PostGIS, adds `DATABASE_URL` and
`TRACCAR_URL` to `.env`, installs Python packages into `.venv`, runs migrations, builds the web app,
and writes `traccar.xml`):

```powershell
copy .env.example .env   # fill in the values
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

Copy the generated `traccar.xml` over `C:\Program Files\Traccar\conf\traccar.xml` (needs admin), then
restart the **traccar** service (`services.msc`). The first time, open `http://localhost:8082` and
register the admin account with `TRACCAR_ADMIN_EMAIL` / `TRACCAR_ADMIN_PASSWORD` from `.env`.

**Start** (opens one window each for the API, the bot and the web app; close a window to stop it):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
.venv\Scripts\python -m app.cli create-admin --email you@example.com --name "Your Name"   # first time only
```

Then open `http://localhost:8090`. PostgreSQL and Traccar run as Windows services and start with Windows.
After pulling frontend changes, rebuild with `cd web; npm run build`.

## Frontend development

```bash
cd web
npm install
npm run dev   # http://localhost:5173, proxies /api to the api service on :8100
```

## Adding a driver

1. On the web app, open **Drivers → Add driver** (name, phone). This also creates the driver's
   GPS device in Traccar, with a generated 6-digit identifier.
2. In the driver's **Phone setup** window:
   - **Telegram:** send the driver their personal link (**Send via Telegram** or copy it). When they
     open it and tap Start, the bot stores their Telegram ID and the link stops working.
   - **GPS:** the driver installs Traccar Client and enters the **Server URL** and **Device identifier**
     shown there (`TRACCAR_CLIENT_URL` in `.env`).
3. The driver list shows **GPS online/offline** (a fix within `DRIVER_ONLINE_MINUTES`, default 5) and
   **Telegram connected**.

## Customers

Every new order is for a customer. The **Customers** page lists them (search by name, phone or
address) with their order count; opening one shows its latest orders. A customer has a name, any
number of **phone numbers** (each with an optional label such as "Mobile"; the first is the main
number, shown in lists and on orders) and notes.

Customers have **no fixed address**: drop-offs change from order to order (home, work, a friend's,
a hotel...). Each order stores its own drop-off address as text. A customer's **recent addresses**
are simply where their last orders went (newest first, at most 5; canceled orders don't count),
followed by addresses that came with an import and no order has used yet. There's no address list
to keep up to date.

A phone number belongs to one customer only. Numbers are stored in international form, so however
a number is written it's the same one and can't be given to two customers: `07911 123456`, `7911123456` (Excel drops the leading 0),
`447911123456`, `+44 (0)7911 123456` and `0044 7911 123456` are all `+447911123456`. Numbers
without a country code are taken to be in `PHONE_COUNTRY_CODE` (default `44`, the UK); foreign
numbers need their `+` or `00` prefix.

On **New order**, search for the customer (or **New customer** without leaving the form), then type
the drop-off address or tap one of their recent addresses below the box.

**Drivers never see customers**: the Telegram messages show only the drop-off address, the items
and quantities, and the order's notes for the driver. Customer notes are for dispatchers only.

**Import** takes contacts exported from a phone or Outlook (**.vcf**, vCard 2.1/3.0/4.0: each
contact's name, numbers with their Mobile/Home/Work or custom label, addresses and note; fax and
pager numbers are left out), or a CSV or Excel (.xlsx) file whose first row has the column names `name`, `phone`,
`address`, and optionally more phone columns (`phone 2`, `mobile`, `home phone`, `work phone`; the
kind becomes the number's label), `label`, `address 2`, `label 2`, …, `notes`. It first shows what
it will do (new customers, added numbers and addresses, rows skipped and why), then imports on
confirmation. A contact or row with a number an existing customer has (without any number: the customer's
name) adds its other numbers and addresses to that customer, so several rows can give one customer
several numbers and addresses, and importing the same file twice adds nothing. A row whose numbers
belong to two different customers is skipped.

With `GOOGLE_MAPS_API_KEY` set, addresses are put on the map with the Google Geocoding API when an
order is saved. An address the customer used before (or that came with their import) reuses its
known location, so Google is only asked about addresses new to that customer. Without a key, or when Google doesn't find an address, the order is still saved,
just without a map location (the coordinates field on the order form can fill it in).

## Products

The **Products** page holds the catalog (name, optional SKU, price). When creating an order, add
items (product + quantity); the order shows each line and the total, and so does the driver's
Telegram message. An item keeps the product's name and price from when the order was created, so
editing a product later doesn't change existing orders. Products can't be deleted, only made
inactive (hidden from new orders).

## Driver updates: arriving soon, changes

- **Arriving soon:** while "On my way", the driver's Telegram message has **⏱ 2 / 3 / 5 min**
  buttons. Tapping one records the arrival time; the web app pops up a notification (on any page,
  with a beep) so the dispatcher can contact the customer, and the order shows "Arriving ~14:32".
- **New quantities at the door:** once the driver has tapped **📍 Arrived**, the order has
  **✏️ Report a change** in Telegram (not before: changes are only known at the customer). The bot
  asks each product in turn (the order's items, then every other active product) and the driver
  **types the new quantity**; then it shows a summary (old → new, totals) with **Send / Cancel**.
  **Send** applies the quantities to the order right away; the web app alerts the dispatcher ("Minh changed
  the items at the door: Water 2 → 3 · total 70,000 → 115,000", with **OK** / **Edit order**).
  Setting everything to 0 is refused: that's a cancellation, so the driver calls the dispatcher.
- **Corrections after delivery:** if a driver tapped ✅ Delivered instead of reporting a change
  (on the card, Report a change is above Delivered to make that less likely), the dispatcher can
  open the delivered order and use **Correct items…**. Only the items (and the customer) can be changed; it's recorded
  in the history ("Corrected after delivery by Minh: Water 2 → 3") and the driver isn't messaged.
- **Change reported (written):** for anything other than quantities, the form's **💬 Something
  else? Write a message** lets the driver type it instead. The web app pops up a notification and the order shows the
  driver's note with **Edit order** and **Mark as handled**. Saving an edit (address, item quantities,
  notes) handles the report, updates the driver's order message and sends them the new details.

- **Delivery list:** whenever a driver accepts an order, or one of their orders is edited, canceled
  or delivered, the bot sends them "📋 Your deliveries": every current destination with its items,
  total and notes, **nearest first** — from the driver's last GPS position to the nearest stop, then
  the stop nearest to that one, and so on (straight-line distance, `app/services/route.py`). Stops
  without coordinates come last. The previous list is deleted, so the newest one is always the only
  one. `/orders` sends it on request. When an edit doesn't change any drop-off address or map
  location (only items or notes), a new list is sent but **not re-sorted**: the stops keep the
  order of the previous list.

- **One message per finished order:** the bot remembers every message about an order (update
  notices, the change-report conversation including the driver's own text, ...). When the order is
  **delivered**, it deletes them all and keeps only the order card ("🎉 Delivered" with the final
  items and total). When it's **canceled** after a driver accepted it, the cancel notice (with the
  order details) is the one message kept. Offers other drivers declined or let expire are deleted
  too. Telegram only lets bots delete messages younger than 48 hours; older ones stay. Nothing is
  lost: the order's history on the web keeps every report, ETA and edit, and edits are recorded in
  detail ("Order updated by Minh: Water 2 → 3; Ice added (1)").

Pop-ups only appear while the web app is open; reports that arrived earlier still show on the order.

## How dispatch works

The bot process also runs the dispatch worker (`app/services/dispatch.py`), every 2 seconds:

1. Offers nobody answered within `OFFER_TIMEOUT_SECONDS` (default 60) expire.
2. Each `PENDING` order without an open offer goes to the best driver not yet asked this round:
   active, Telegram connected (and the chosen driver only, if one was picked), preferring GPS online,
   then nearest to the drop-off (when coordinates are known), then fewest active orders.
3. The driver gets the order in Telegram with **Accept / Decline**, then **On my way → Arrived → Delivered**.

If every driver declines or lets the offer expire, the order stays `PENDING` and the web app shows
**No driver accepted** with a **Send to drivers again** button (starts a new round).

## Order statuses

`PENDING → ACCEPTED → EN_ROUTE → AT_STOP → COMPLETED`, and any non-final status can go to
`CANCELED`. Allowed transitions live in `app/models.py` (`TRANSITIONS`).

Each time an order is offered to a driver, a row goes into `order_offers` with the
result (`ACCEPTED`, `DECLINED`, `EXPIRED`).

## Migrations

```bash
docker compose run --rm -v ./migrations:/srv/migrations api alembic revision --autogenerate -m "message"
```
Review the generated file, then restart `api` to apply it.
