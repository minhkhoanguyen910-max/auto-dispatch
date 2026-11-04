from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth import get_current_user
from app.schemas import RouteOut
from app.services import roads

router = APIRouter(prefix="/routes", tags=["routes"], dependencies=[Depends(get_current_user)])


@router.get("", response_model=RouteOut)
async def get_route(
    from_lat: float = Query(ge=-90, le=90),
    from_lng: float = Query(ge=-180, le=180),
    to_lat: float = Query(ge=-90, le=90),
    to_lng: float = Query(ge=-180, le=180),
) -> RouteOut:
    """Map's Route tool: the driving route between two points (OSRM, without traffic)."""
    try:
        found = await roads.route((from_lat, from_lng), (to_lat, to_lng))
    except roads.RoutingUnavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Routing isn't available right now (the osrm service).")
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No route found here. Both points must be near a road in the map area.")
    return RouteOut(path=[list(p) for p in found.path], distance_m=found.meters, duration_s=found.seconds)
