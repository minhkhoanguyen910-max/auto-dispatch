"""Roads for the live map, from the self-hosted OSRM server (the `osrm` service).

`road_path()`: the roads a driver most likely drove between two GPS fixes. Without OSRM_URL, when OSRM fails,
or when the match doesn't look like real driving, there's no path and the map slides the marker in a straight
line as before.
`route()`: the driving route between two points the dispatcher picks on the map.
`walk()`: the walking route between two points, from OSRM's walking map (the `osrm-foot` service), for meeting
points: footpaths and pedestrian zones, and one-way streets both ways.
`nearest_road()`: the nearest point a car can reach, with the street's name (meeting points, and doors in
pedestrian zones).
"""

import logging
from dataclasses import dataclass
from datetime import datetime

import httpx

from app.config import settings
from app.services.travel import LatLng, straight_line_m

log = logging.getLogger(__name__)

# Closer than this the driver barely moved (or it's GPS jitter): no path needed
MIN_MOVE_M = 20
# Fixes further apart in time than this aren't one stretch of driving (the phone stopped reporting)
MAX_GAP_SECONDS = 180
# How far (meters) each fix may be from the road it's matched to, for GPS error
SNAP_RADIUS_M = 30
# A road path this many times longer than the straight line is a detour the driver most likely didn't take
MAX_DETOUR = 3
# OSRM snaps a point to the nearest road (or path) however far it is; farther than this, the point is outside the map
MAX_ROUTE_SNAP_M = 500


class RoutingUnavailable(Exception):
    """OSRM isn't set up (no OSRM_URL / OSRM_FOOT_URL) or isn't answering."""


@dataclass
class Route:
    path: list[LatLng]
    meters: float
    seconds: float  # without traffic; walking pace for walks


async def route(a: LatLng, b: LatLng) -> Route | None:
    """Driving route from `a` to `b`, or None if either point isn't near a road in the road map."""
    return await _route(settings.osrm_url, "driving", a, b)


async def walk(a: LatLng, b: LatLng) -> Route | None:
    """Walking route from `a` to `b`, or None if either point isn't near a path in the walking map."""
    return await _route(settings.osrm_foot_url, "foot", a, b)


async def _route(osrm_url: str, profile: str, a: LatLng, b: LatLng) -> Route | None:
    if not osrm_url:
        raise RoutingUnavailable
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                f"{osrm_url}/route/v1/{profile}/{a[1]},{a[0]};{b[1]},{b[0]}",
                params={"overview": "full", "geometries": "geojson"},
            )
            data = resp.json()
    except (httpx.HTTPError, ValueError) as err:
        log.warning("OSRM unavailable: %s", err)
        raise RoutingUnavailable from err
    if data.get("code") != "Ok" or any(w["distance"] > MAX_ROUTE_SNAP_M for w in data["waypoints"]):
        return None
    r = data["routes"][0]
    return Route([(lat, lng) for lng, lat in r["geometry"]["coordinates"]], r["distance"], r["duration"])


@dataclass
class RoadPoint:
    point: LatLng
    meters: float  # from the point asked about
    name: str  # the street's name; "" when it has none


async def nearest_road(p: LatLng) -> RoadPoint | None:
    """The nearest point on the road map to `p`, or None if nothing is within MAX_ROUTE_SNAP_M."""
    if not settings.osrm_url:
        raise RoutingUnavailable
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{settings.osrm_url}/nearest/v1/driving/{p[1]},{p[0]}", params={"number": 1})
            data = resp.json()
    except (httpx.HTTPError, ValueError) as err:
        log.warning("OSRM unavailable: %s", err)
        raise RoutingUnavailable from err
    if data.get("code") != "Ok" or not data.get("waypoints") or data["waypoints"][0]["distance"] > MAX_ROUTE_SNAP_M:
        return None
    w = data["waypoints"][0]
    return RoadPoint((w["location"][1], w["location"][0]), w["distance"], w.get("name") or "")


async def road_path(a: LatLng, a_time: datetime, b: LatLng, b_time: datetime) -> list[LatLng] | None:
    """Points along the roads from fix `a` to fix `b`, or None if there's no believable road path."""
    if not settings.osrm_url:
        return None
    straight = straight_line_m(a, b)
    if straight < MIN_MOVE_M or not 1 <= (b_time - a_time).total_seconds() <= MAX_GAP_SECONDS:
        return None
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            resp = await client.get(
                f"{settings.osrm_url}/match/v1/driving/{a[1]},{a[0]};{b[1]},{b[0]}",
                params={
                    "timestamps": f"{int(a_time.timestamp())};{int(b_time.timestamp())}",
                    "radiuses": f"{SNAP_RADIUS_M};{SNAP_RADIUS_M}",
                    "geometries": "geojson",
                    "overview": "full",
                    "gaps": "ignore",
                },
            )
            data = resp.json()
    except (httpx.HTTPError, ValueError) as err:
        log.warning("OSRM unavailable: %s", err)
        return None
    # "NoMatch" etc. (e.g. a fix in a car park, far from any road) come back as code != "Ok"
    matchings = data.get("matchings") or []
    if data.get("code") != "Ok" or len(matchings) != 1:
        return None
    if matchings[0]["distance"] > straight * MAX_DETOUR + 100:
        return None
    return [(lat, lng) for lng, lat in matchings[0]["geometry"]["coordinates"]]
