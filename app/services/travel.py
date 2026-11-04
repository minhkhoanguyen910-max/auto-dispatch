"""Driving times between places, for route planning (Plan 1, step 1).

`matrix()` gives the driving time from each origin to each destination, in seconds. It uses Google's Route
Matrix when a Maps key is set, and remembers every answer in the database (`travel_times`), so each trip is
paid for once: times without traffic don't change and are kept for 30 days; times with live traffic only for
5 minutes. Without a key, when Google fails, or once `ROUTES_DAILY_LIMIT` pairs were requested today, it
estimates from the straight-line distance instead. Each answer says where it came from.

With `ROUTES_TRAFFIC=next-leg` (the default) callers ask for live traffic only where it decides something
now: the driver's next leg and "on the way" checks. Other trips use times without traffic (cheaper).
"""

import logging
import math
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

import httpx
from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import RoutesUsage, TravelTime

log = logging.getLogger(__name__)

MATRIX_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
MAX_PAIRS_PER_REQUEST = 625  # Google's limit for one Route Matrix request
KEEP = {False: timedelta(days=30), True: timedelta(minutes=5)}
# Roads aren't straight: driving distance is about this much longer than the straight line
ROAD_FACTOR = 1.3
# Places closer than this (degrees, about 50 m) count as the same place
GRID = 0.0005

Source = Literal["google", "estimate", "unknown"]
LatLng = tuple[float, float]


@dataclass(frozen=True)
class Leg:
    seconds: int | None  # None: a place has no map location, so the time is unknown
    source: Source
    traffic: bool = False  # live traffic was taken into account


def place_key(p: LatLng) -> str:
    """"51.50071,-0.12462" -> "51.5005,-0.1245": nearby points share remembered times."""
    return f"{round(p[0] / GRID) * GRID:.4f},{round(p[1] / GRID) * GRID:.4f}"


def straight_line_m(a: LatLng, b: LatLng) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def estimate_seconds(a: LatLng, b: LatLng) -> int:
    return round(straight_line_m(a, b) * ROAD_FACTOR / (settings.fallback_speed_kmh * 1000 / 3600))


def _traffic_used(wanted: bool) -> bool:
    return settings.routes_traffic == "all" or (wanted and settings.routes_traffic == "next-leg")


def _now() -> datetime:
    return datetime.now(UTC)


def today() -> date:
    return _now().date()


async def _reserve(session: AsyncSession, pairs: int) -> bool:
    """Count `pairs` against today's limit; False (nothing counted) if they don't fit."""
    used = await session.scalar(select(RoutesUsage.pairs).where(RoutesUsage.day == today()).with_for_update()) or 0
    if used + pairs > settings.routes_daily_limit:
        return False
    await session.execute(
        insert(RoutesUsage)
        .values(day=today(), pairs=pairs)
        .on_conflict_do_update(index_elements=[RoutesUsage.day], set_={"pairs": RoutesUsage.pairs + pairs})
    )
    return True


def _waypoint(key: str) -> dict:
    lat, lng = map(float, key.split(","))
    return {"waypoint": {"location": {"latLng": {"latitude": lat, "longitude": lng}}}}


async def _ask_google(
    client: httpx.AsyncClient, origins: list[str], destinations: list[str], traffic: bool
) -> dict[tuple[str, str], int]:
    """One Route Matrix request. Pairs Google found no route for are left out."""
    resp = await client.post(
        MATRIX_URL,
        json={
            "origins": [_waypoint(o) for o in origins],
            "destinations": [_waypoint(d) for d in destinations],
            "travelMode": "DRIVE",
            "routingPreference": "TRAFFIC_AWARE" if traffic else "TRAFFIC_UNAWARE",
        },
        headers={
            "X-Goog-Api-Key": settings.google_maps_api_key,
            "X-Goog-FieldMask": "originIndex,destinationIndex,duration,condition,status",
        },
        timeout=15,
    )
    resp.raise_for_status()
    found = {}
    for element in resp.json():
        if element.get("condition") != "ROUTE_EXISTS" or element.get("status", {}).get("code"):
            continue
        m = re.fullmatch(r"(\d+(?:\.\d+)?)s", element.get("duration", ""))
        if m:
            found[(origins[element.get("originIndex", 0)], destinations[element.get("destinationIndex", 0)])] = round(float(m.group(1)))
    return found


def _requests(missing: set[tuple[str, str]]) -> list[tuple[list[str], list[str]]]:
    """Group the missing pairs into requests without asking for pairs we already know: origins that need the
    same destinations share a request (a new stop costs one row and one column, not the whole matrix)."""
    by_origin: dict[str, set[str]] = {}
    for o, d in missing:
        by_origin.setdefault(o, set()).add(d)
    groups: dict[frozenset[str], list[str]] = {}
    for o, ds in by_origin.items():
        groups.setdefault(frozenset(ds), []).append(o)
    requests = []
    for ds, os_ in groups.items():
        dests = sorted(ds)
        rows = max(1, MAX_PAIRS_PER_REQUEST // len(dests))
        for i in range(0, len(os_), rows):
            requests.append((sorted(os_)[i : i + rows], dests))
    return requests


async def matrix(
    session: AsyncSession,
    origins: list[LatLng | None],
    destinations: list[LatLng | None],
    traffic: bool = False,
    nearest: int | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[list[Leg]]:
    """Driving time from each origin to each destination: `result[i][j]` is origins[i] → destinations[j].
    A place without a map location (None) gives unknown legs. `traffic` asks for live traffic (see the module
    docstring). With `nearest`, Google is only asked about each origin's that many nearest destinations (by
    straight line); farther trips use a remembered time if there is one, else an estimate (route planning
    almost never puts far-apart stops next to each other). Commits the remembered times and usage."""
    use_traffic = _traffic_used(traffic)
    okeys = [place_key(o) if o else None for o in origins]
    dkeys = [place_key(d) if d else None for d in destinations]
    wanted = {(o, d) for o in okeys if o for d in dkeys if d and d != o}
    ask = wanted
    if nearest is not None:
        places = {k: p for k, p in zip(dkeys, destinations) if k}
        ask = set()
        for o, okey in zip(origins, okeys):
            if o:
                closest = sorted((k for k in places if k != okey), key=lambda k: straight_line_m(o, places[k]))[:nearest]
                ask |= {(okey, k) for k in closest}

    known: dict[tuple[str, str], int] = {}
    if wanted:
        fresh_since = _now() - KEEP[use_traffic]
        rows = await session.execute(
            select(TravelTime.origin, TravelTime.destination, TravelTime.seconds).where(
                TravelTime.traffic == use_traffic,
                TravelTime.fetched_at >= fresh_since,
                tuple_(TravelTime.origin, TravelTime.destination).in_(list(wanted)),
            )
        )
        known = {(o, d): s for o, d, s in rows}

    missing = ask - known.keys()
    if missing and settings.google_maps_api_key:
        own_client = client is None
        client = client or httpx.AsyncClient()
        try:
            for req_origins, req_dests in _requests(missing):
                pairs = len(req_origins) * len(req_dests)
                if not await _reserve(session, pairs):
                    log.warning("Route Matrix daily limit (%s pairs) reached; estimating", settings.routes_daily_limit)
                    break
                try:
                    found = await _ask_google(client, req_origins, req_dests, use_traffic)
                except (httpx.HTTPError, ValueError) as err:
                    log.warning("Route Matrix request failed, estimating: %s", err)
                    continue
                if found:
                    await session.execute(
                        insert(TravelTime)
                        .values([{"origin": o, "destination": d, "traffic": use_traffic, "seconds": s} for (o, d), s in found.items()])
                        .on_conflict_do_update(
                            index_elements=[TravelTime.origin, TravelTime.destination, TravelTime.traffic],
                            set_={"seconds": insert(TravelTime).excluded.seconds, "fetched_at": _now()},
                        )
                    )
                known.update(found)
        finally:
            if own_client:
                await client.aclose()
    await session.commit()

    result = []
    for o, okey in zip(origins, okeys):
        row = []
        for d, dkey in zip(destinations, dkeys):
            if o is None or d is None:
                row.append(Leg(None, "unknown"))
            elif okey == dkey:
                row.append(Leg(0, "google" if settings.google_maps_api_key else "estimate", use_traffic))
            elif (okey, dkey) in known:
                row.append(Leg(known[(okey, dkey)], "google", use_traffic))
            else:
                row.append(Leg(estimate_seconds(o, d), "estimate"))
        result.append(row)
    return result
