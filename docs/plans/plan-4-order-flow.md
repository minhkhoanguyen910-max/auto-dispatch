# Order journey after Plan 4

Every path an order can take on the live system once Plan 4 is merged, from the moment the dispatcher creates it
until it's delivered or canceled, and who does what at each point. Written 2026-10-29 for the review before the
merge.

Live settings this describes: `ROUTE_OPTIMIZATION` off · `MEETING_POINTS=parked` (only "I'm parked here") · offers
answered within 60 s · no automatic "deliver by" (`MAX_WAIT_MINUTES=0`).

In the flowcharts: orange = driver (Telegram), blue = dispatcher (web), grey = the app by itself, green = order
status.

## 1. From new order to delivered or canceled

The main path. Every order goes through it; the other scenarios are detours along the way.

```mermaid
flowchart TD
  A["Dispatcher creates the order<br/>customer, address, items"]:::disp --> B{"'Send to' a chosen driver?"}
  B -- "No: automatic" --> D["Offer to the best driver not asked yet<br/>GPS online first, then nearest, then fewest orders"]:::sys
  B -- "Yes" --> C["Offer to that driver only"]:::sys
  C --> E{"Driver's answer within 60 s"}
  D --> E
  E -- "✅ Accept" --> F["Accepted<br/>driver gets the card and the delivery list"]:::state
  E -- "❌ Decline, no answer,<br/>or Telegram unreachable" --> G{"Any driver left to ask?"}
  G -- "Yes" --> D
  G -- "No" --> H["Web shows 'No driver'"]:::disp
  H -- "Dispatcher: Retry" --> D
  F -- "Driver: 🚚 On my way" --> I["On the way"]:::state
  I -- "Driver: 📍 Arrived" --> J["Arrived"]:::state
  J -- "Driver: ✅ Delivered" --> K(["Delivered"]):::done
  Z["Dispatcher: Cancel, with an optional reason<br/>possible at any point before Delivered"]:::disp -.-> Y(["Canceled<br/>driver told, chat cleaned up, list updated"]):::cancel
  classDef disp fill:#dbe6ff,stroke:#1d4ed8,color:#0b1a3a
  classDef drv fill:#fde7c8,stroke:#b45309,color:#3a1d00
  classDef sys fill:#e7e9ec,stroke:#6b7280,color:#1f2328
  classDef state fill:#d5efe9,stroke:#0f6b5c,color:#06302a
  classDef done fill:#0f6b5c,stroke:#0f6b5c,color:#ffffff
  classDef cancel fill:#f3d6d6,stroke:#9b1c1c,color:#3a0a0a
```

- Only active drivers who connected Telegram are offered orders. Each driver is asked at most once per round;
  **Retry** starts a new round.
- While an order waits, the web shows who is deciding and the seconds left. "No driver" appears once nobody is left.
- With route optimization off (live), the driver's list is nearest first and every accepted order has
  **On my way**.

## 2. While the order is being delivered

What each side can do at each status. None of this changes the main path; it runs alongside it.

| Status | Driver can | Dispatcher can | The app does |
|---|---|---|---|
| Waiting for driver | Accept or decline the offer | Edit the order (the deciding driver sees the new details) · Retry after "No driver" · Cancel | Moves to the next driver after 60 s |
| Accepted | 🚚 On my way · `/orders` for the list | Ask driver for ETA · Edit (driver told; a new address re-sorts the list) · Cancel | Sends the delivery list again when stops change |
| On the way | ⏱ 2 / 3 / 5 min when close · answer an ETA question (3 / 5 / 7 / 10+) | Gets an orange "arrives at ~14:05: contact the customer" alert · Ask for ETA · Edit · Cancel | — |
| Arrived | ✏️ Report a change (new quantities apply at once, or a written note) · 📌 I'm parked here (section 3) · ✅ Delivered | Edit the order or **Mark as handled** for a written change (driver told) · OK for applied quantities · Cancel | Alerts the dispatcher about every change report |
| Delivered | — | Correct the items only, e.g. if the driver tapped Delivered instead of reporting a change (recorded, driver not messaged) | Deletes the order's extra messages from the driver's chat; sends a fresh list |
| Canceled | — | — | Driver gets one cancel notice with the details; other messages about it are deleted |

## 3. "I'm parked here, ask the customer to come out" (new with Plan 4)

The only meeting-point feature switched on for live. It appears on the card once the driver taps **📍 Arrived**,
and can be used once per order.

```mermaid
flowchart TD
  A["Arrived"]:::state --> B["Driver: 📌 I'm parked here,<br/>ask the customer to come out"]:::drv
  B --> C{"Allowed?"}
  C -- "No" --> R["Pop-up gives the reason:<br/>already asked once for this order,<br/>customer is 'always to the door',<br/>or address not on the map"]:::drv
  R --> RD(["Driver delivers to the door"]):::done
  C -- "Yes" --> D{"Phone GPS from the last 2 min?"}
  D -- "Yes" --> F
  D -- "No" --> E["Driver: 📍 Send my location<br/>or types a position / pastes a Google Maps link"]:::drv
  E --> F["Bot shows the customer's walk<br/>⚠️ warning above 250 m, never refused"]:::sys
  F -- "✖ Cancel" --> RD
  F -- "✅ Ask the customer to come out" --> G["Web: red alert and beep<br/>'Driver is waiting now: call the customer'"]:::disp
  G --> H["Dispatcher phones or texts the customer<br/>Copy message for the customer"]:::disp
  H -- "Customer is coming" --> I["Driver told: ✅ the customer is coming out"]:::drv
  H -- "Customer declined" --> J["Driver told: 🚪 take it to the door<br/>customer's decline count +1;<br/>at 2 they become 'always to the door'"]:::drv
  H -- "No answer yet" --> L
  I --> L{"Delivered within 5 min?<br/>counted from 'Customer is coming',<br/>or from the request if nobody answered"}
  L -- "Yes" --> M(["Delivered at the spot<br/>spot counts as worked"]):::done
  L -- "No" --> N["Bot: ⏱ Customer not out after 5 min?<br/>🚪 I'll take it to the door"]:::sys
  N -- "Driver taps it" --> O(["Driver walks to the door, Delivered<br/>spot counts as 'customer not there'"]):::done
  N -- "Customer comes after all" --> M
  J --> RD
  classDef disp fill:#dbe6ff,stroke:#1d4ed8,color:#0b1a3a
  classDef drv fill:#fde7c8,stroke:#b45309,color:#3a1d00
  classDef sys fill:#e7e9ec,stroke:#6b7280,color:#1f2328
  classDef state fill:#d5efe9,stroke:#0f6b5c,color:#06302a
  classDef done fill:#0f6b5c,stroke:#0f6b5c,color:#ffffff
```

- The ready-made customer message reads like: "Hi Jane, your driver is here now with your delivery (order #52),
  parked at Queens Road, about 3 min walk (220 m) from your address: *map link* . Could you come out to meet
  them? They'll wait about 5 minutes."
- If the dispatcher answers **Customer is coming** after the bot already asked "Customer not out?", the bot waits
  another 5 minutes and asks again.
- The customer page has **Always deliver to the door**: on after 2 declines (including "I'm parked here"
  declines), or set by hand. Turning it off resets the count.
- Canceling the order while a request is open simply removes the request; the driver gets the normal cancel
  notice.

## 4. "Meet the customer instead" (built, switched off on live)

Switched on only with `MEETING_POINTS=all`. For reference, the path it adds on any upcoming stop:

```mermaid
flowchart LR
  A["Driver: 📍 Meet the customer instead"]:::drv --> B["Spot: own pin, typed position,<br/>or 🔁 Same place as last time"]:::drv
  B --> C["Bot: walk at most 250 m, time saved,<br/>warnings: small saving, short notice"]:::sys
  C --> D["Dispatcher: copy message, send by deadline"]:::disp
  D -- "Agreed" --> E["Stop moves to the spot,<br/>navigation link, route re-planned"]:::sys
  D -- "Declined / no answer in time" --> F(["Deliver to the door"]):::done
  E --> G{"Customer there within 5 min of Arrived?"}
  G -- "Yes" --> H(["Delivered at the spot"]):::done
  G -- "No" --> I["🚪 Drive to the address,<br/>back to On my way"]:::drv
  classDef disp fill:#dbe6ff,stroke:#1d4ed8,color:#0b1a3a
  classDef drv fill:#fde7c8,stroke:#b45309,color:#3a1d00
  classDef sys fill:#e7e9ec,stroke:#6b7280,color:#1f2328
  classDef done fill:#0f6b5c,stroke:#0f6b5c,color:#ffffff
```

## 5. Every button, and who presses it

| Who | Button or action | When it's there | What happens |
|---|---|---|---|
| Driver | ✅ Accept / ❌ Decline | New offer, 60 s | Accept: the order is theirs. Decline or silence: next driver |
| Driver | 🚚 On my way | Accepted | Status On the way; ETA buttons appear |
| Driver | ⏱ 2 / 3 / 5 min | On the way | Dispatcher alerted to contact the customer |
| Driver | 📍 Arrived | On the way | Status Arrived; Report a change and I'm parked here appear |
| Driver | ✏️ Report a change | Arrived | New quantities apply at once, or a note goes to the dispatcher |
| Driver | 📌 I'm parked here | Arrived, once per order | Section 3 |
| Driver | 🚪 I'll take it to the door | 5 min after the request or after "Customer is coming" | Recorded as customer not there |
| Driver | ✅ Delivered | Arrived | Order finished, chat cleaned up |
| Dispatcher | Send to / Retry / Cancel | Waiting for driver | Choose the driver, ask everyone again, or stop |
| Dispatcher | Ask driver for ETA | Accepted to Arrived | Driver gets 3 / 5 / 7 / 10+ min buttons |
| Dispatcher | Edit order | Until Delivered (items only after) | Driver gets the new details |
| Dispatcher | Copy message / Customer is coming / Customer declined | "I'm parked here" request open | Driver told in Telegram |
| Dispatcher | Always deliver to the door | Customer page | Blocks "I'm parked here" requests for that customer |
