"""Where a typed drop-off address is (Google).

With ADDRESS_AREA set (the delivery area: centre and radius), the lookup prefers that area and never lands outside
it: "1 Sussex Road" is the one in St Leonards, not Tonbridge. When the address lookup finds nothing there, or only
the street while the text also names a place ("Tesco Fernside Avenue"), places are searched by name in the area
(pubs, shops: "Railway pub"). The result says whether it's an exact place or only the street.
"""

import logging
import math
import re
from dataclasses import dataclass
from typing import Literal

import httpx

from app.config import settings

log = logging.getLogger(__name__)

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
TOO_VAGUE = {"country", "administrative_area_level_1", "administrative_area_level_2"}
# A house, a building or a named place, rather than a whole street or town
PRECISE = {"street_address", "premise", "subpremise", "establishment", "point_of_interest"}
# Words that don't name a place ("Bottom of Kenilworth Road"), and street-type words
NOT_A_NAME = {
    "the", "of", "at", "by", "to", "on", "in", "and", "near", "next", "bottom", "top", "end", "corner", "meet",
    "flat", "house", "road", "rd", "street", "st", "avenue", "ave", "close", "cl", "lane", "ln", "drive", "dr",
    "terrace", "place", "pl", "way", "hill", "gardens", "gdns", "crescent", "square", "court", "parade", "park",
}

Precision = Literal["exact", "street", "place"]


@dataclass
class GeocodeResult:
    formatted_address: str
    latitude: float
    longitude: float
    # exact: a house or building; street: only the street was found; place: found by name (a pub, a shop)
    precision: Precision = "exact"


def delivery_area() -> tuple[float, float, float] | None:
    """ADDRESS_AREA as (lat, lng, km), or None when not set."""
    if not settings.address_area.strip():
        return None
    lat, lng, km = (float(v) for v in settings.address_area.split(","))
    return lat, lng, km


def _in_area(lat: float, lng: float, area: tuple[float, float, float]) -> bool:
    dlat = math.radians(lat - area[0])
    dlng = math.radians(lng - area[1]) * math.cos(math.radians(area[0]))
    return 6371 * math.hypot(dlat, dlng) <= area[2]


def _box(area: tuple[float, float, float]) -> tuple[float, float, float, float]:
    """South, west, north, east of the square around the area."""
    dlat = area[2] / 111.2
    dlng = area[2] / (111.2 * math.cos(math.radians(area[0])))
    return area[0] - dlat, area[1] - dlng, area[0] + dlat, area[1] + dlng


async def geocode(address: str) -> GeocodeResult | None:
    """Look up an address with the Google Geocoding API. Returns None if not found (or not in the delivery area)."""
    area = delivery_area()
    params = {
        "address": address,
        "components": f"country:{settings.address_country}",
        "region": settings.address_country.lower(),
        "key": settings.google_maps_api_key,
    }
    if area:
        s, w, n, e = _box(area)
        params["bounds"] = f"{s},{w}|{n},{e}"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(GEOCODE_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    if data["status"] == "ZERO_RESULTS":
        return None
    if data["status"] != "OK":
        raise RuntimeError(f"Geocoding failed: {data['status']} {data.get('error_message', '')}")

    for result in data["results"]:
        # Google answers an unknown address with the whole country (or a region): no use for a delivery
        if TOO_VAGUE.intersection(result["types"]):
            continue
        loc = result["geometry"]["location"]
        if area and not _in_area(loc["lat"], loc["lng"], area):
            continue
        precision: Precision = "street" if not PRECISE.intersection(result["types"]) else "exact"
        return GeocodeResult(result["formatted_address"], loc["lat"], loc["lng"], precision)
    return None


async def find_place(text: str) -> GeocodeResult | None:
    """A named place (pub, shop, ...) in the delivery area, with Places API (New) text search."""
    area = delivery_area()
    if not area:
        return None
    s, w, n, e = _box(area)
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            PLACES_URL,
            headers={
                "X-Goog-Api-Key": settings.google_maps_api_key,
                "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location",
            },
            json={
                "textQuery": text,
                "regionCode": settings.address_country,
                "locationRestriction": {"rectangle": {"low": {"latitude": s, "longitude": w}, "high": {"latitude": n, "longitude": e}}},
                # Google only restricts to a rectangle, whose corners reach past the area: take the best one inside it
                "pageSize": 5,
            },
        )
        resp.raise_for_status()
        places = resp.json().get("places") or []
    p = next((p for p in places if _in_area(p["location"]["latitude"], p["location"]["longitude"], area)), None)
    if p is None:
        return None
    name = p.get("displayName", {}).get("text", "")
    address = p.get("formattedAddress", "")
    full = f"{name}, {address}" if name and not address.startswith(name) else address
    return GeocodeResult(full, p["location"]["latitude"], p["location"]["longitude"], "place")


def _names(text: str) -> set[str]:
    """The words of a text that could name a place."""
    return {w for w in re.findall(r"[a-z]{3,}", text.lower()) if w not in NOT_A_NAME}


async def find(address: str) -> GeocodeResult | None:
    """The address lookup, then (in the delivery area) a place found by name when that's better."""
    found = await geocode(address)
    if not delivery_area():
        return found
    if found is not None and found.precision == "exact":
        return found
    # Words the street didn't explain may name a place on it ("Tesco" in "Tesco Fernside Avenue"); use the place only
    # if its name has one of them, so "Kenilworth Road" doesn't become a random shop on Kenilworth Road
    extra = _names(address) - (_names(found.formatted_address) if found else set())
    if found is None or extra:
        place = await find_place(address)
        if place is not None and (found is None or extra & _names(place.formatted_address.split(",")[0])):
            return place
    return found


async def locate_detail(address: str) -> GeocodeResult | None:
    """Like `locate`, with the address Google found and how precise it is."""
    if not settings.google_maps_api_key:
        return None
    try:
        return await find(address)
    except (httpx.HTTPError, RuntimeError, KeyError, ValueError) as err:
        log.warning("Couldn't geocode %r: %s", address, err)
        return None


async def locate(address: str) -> tuple[float, float] | None:
    """(latitude, longitude) of an address, or None when Google isn't set up, doesn't know the
    address, or fails. Saving an order or customer never depends on Google answering."""
    found = await locate_detail(address)
    return (found.latitude, found.longitude) if found else None


def point(latlng: tuple[float, float] | None) -> str | None:
    """Value for a Geography column."""
    return f"SRID=4326;POINT({latlng[1]} {latlng[0]})" if latlng else None
