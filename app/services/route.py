"""Order of a driver's stops: nearest first, the way drivers deliver."""

from math import asin, cos, radians, sin, sqrt
from typing import TypeVar

T = TypeVar("T")
LatLng = tuple[float, float]


def distance_m(a: LatLng, b: LatLng) -> float:
    """Straight-line (great-circle) distance in meters."""
    lat1, lng1, lat2, lng2 = map(radians, (*a, *b))
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lng2 - lng1) / 2) ** 2
    return 2 * 6_371_000 * asin(sqrt(h))


def along(start: LatLng | None, stops: list[tuple[T, LatLng | None]]) -> list[tuple[T, float | None]]:
    """Stops in the given order, with the distance from the previous stop (from `start` for the
    first). A stop without a location has no distance and doesn't move the driver."""
    route: list[tuple[T, float | None]] = []
    here = start
    for stop, at in stops:
        route.append((stop, distance_m(here, at) if here is not None and at is not None else None))
        here = at if at is not None else here
    return route


def nearest_first(start: LatLng | None, stops: list[tuple[T, LatLng | None]]) -> list[tuple[T, float | None]]:
    """Stops in driving order with the distance from the previous stop (from `start` for the first).

    From where the driver is, go to the nearest stop, then the stop nearest to that one, and so on.
    Stops without a location come last, in the given order. Without a start position the order
    is unchanged and there are no distances.
    """
    if start is None:
        return [(stop, None) for stop, _ in stops]
    left = [(stop, at) for stop, at in stops if at is not None]
    route: list[tuple[T, float | None]] = []
    here = start
    while left:
        i = min(range(len(left)), key=lambda k: distance_m(here, left[k][1]))
        stop, at = left.pop(i)
        route.append((stop, distance_m(here, at)))
        here = at
    return route + [(stop, None) for stop, at in stops if at is None]
