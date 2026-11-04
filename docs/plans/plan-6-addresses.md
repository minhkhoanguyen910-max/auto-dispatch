# Plan 6: Local place names and better address search

Status: **planned** (2026-11-02, questions answered the same day), nothing built. To look at **after the Plan 1 test week**, with its numbers (how
often an address needed Pick on the map, or was wrong). Each step explained and agreed before it's built; built and
tested in a test copy, merged into `main` only when the user says so.

## For whoever builds this

- Read this whole file first; the decisions are the user's and aren't re-argued.
- Orders never go beyond the Hastings / St Leonards area (`ADDRESS_AREA=50.868,0.578,6`).
- The server is short of memory (about 1.5–4 GB free, no swap): nothing heavy (no full Nominatim install).

## The need

Dispatchers type addresses the way customers and drivers say them: "Prince of wales", "Railway pub", "Tesco fernside
avenue", "bottom of Kenilworth road", "Morrisons car park", "5 St Johns Road next to Warrior station". No website knows
the names local people actually use. Google knows houses and businesses but also offers the same street name in other
towns; it can't know "the bumpy bit".

## Already done (Plan 1 test week, 2026-10-30 to 11-02)

- Lookups prefer the delivery area and never land outside it; pubs and shops are found by name (Places API).
- **Suggestions only from the delivery area** (2026-11-02): on the first real day, picking a suggestion put 7 of 37
  orders' address text in London, Glasgow, St Neots, Thornton Heath, Hampton, Battle (the map location was corrected
  by hand, so only the text was wrong).
- **Suggestions only in Hastings and St Leonards** (`ADDRESS_TOWNS`, 2026-11-02): Google's area limit still offered
  Battle (Station Road, Crowhurst Lane, the Railway pub) and Bexhill (Tesco Ravenside); those are now left out.
- Pick on the map (with search by name), a hint that instructions go in Notes, customers' earlier addresses remembered.

## Sources considered (2026-11-02)

| Source | What it knows | Cost | Use here |
|---|---|---|---|
| **Own list of local names** (this plan) | the names *these* dispatchers, customers and drivers use | free | **first** |
| Royal Mail **PAF** (via Ideal Postcodes, getAddress.io, Loqate, ...) | every delivery address, flats and building names; postcode → list of houses | per lookup | **no** (user, 2026-11-02) |
| **OpenStreetMap** (already on the server for the maps) | pubs, shops, car parks, footpaths; "local name" and old / alternative names | free | yes, as a small search |
| Ordnance Survey AddressBase / Open Names | official addresses / streets and places (no house numbers in Open Names) | licensed / free | no |
| postcodes.io | postcode → position | free | as a check |
| Google (now) | houses, businesses | per lookup | stays, after our own sources |

## The plan

### 1. Local names, learned from the dispatcher's work (first)
- A shared list of **places**: a name, other names for it ("Warrior station", "Warrior Square station"), its position
  (the spot to go to), and an optional note for the driver ("parent and child bays at the back").
- **The place's driver note is pre-filled** in the order's "Notes for the driver" when an order goes to that place
  (user, 2026-11-02); the dispatcher sees it and can change or delete it before sending.
- **Saved while working:** in Pick on the map, "Save as a place name" with what was typed ("Morrisons car park",
  "bottom of Kenilworth road"). Also from an order's address after it's corrected.
- **Found first:** typing in the address box shows matching local places at the top (marked as yours), then the
  customer's earlier addresses, then OpenStreetMap, then Google. Spelling slips match ("kennilworth").
- A **Places** page to see, rename, merge and delete them.

### 2. OpenStreetMap place search on our own server (free)
- From the same OpenStreetMap download the maps use, keep only named places in the area (pubs, shops, car parks,
  schools, stations, ...) with their names, local names and alternative names, in a small table (thousands of rows,
  not a full search engine).
- Searched after the local list and before Google: free, and knows local spellings and nicknames that are mapped.

### 3. Postcode checks (free)
- A typed postcode (TN34 / TN37 / TN38) is checked against where the address landed (postcodes.io or Ordnance Survey
  Code-Point Open); far apart → the form warns and offers Pick on the map.

### ~~4. Royal Mail addresses by postcode~~
- Not wanted (user, 2026-11-02).

## Steps

1. Local places: data, "Save as a place name", found first in the address box and map search, the Places page.
2. OpenStreetMap places table and search.
3. Postcode check.
4. Tests: the test week's real addresses (all 37 of 2026-10-30 and the later days) found right, none outside the area.

## Open questions (after the test week)

1. ~~Postcode → house list (Royal Mail)~~: not wanted (2026-11-02).
2. ~~A place's driver note on every order~~: yes, pre-filled in Notes for the driver, editable (2026-11-02).
