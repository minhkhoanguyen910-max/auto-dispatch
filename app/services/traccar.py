"""Traccar REST API client (admin account), for managing driver GPS devices."""

import httpx

from app.config import settings


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=settings.traccar_url,
        auth=(settings.traccar_admin_email, settings.traccar_admin_password),
        timeout=10,
    )


async def ensure_device(unique_id: str, name: str) -> None:
    """Create the Traccar device for a driver, or rename it if it already exists."""
    async with _client() as client:
        resp = await client.get("/api/devices", params={"uniqueId": unique_id})
        resp.raise_for_status()
        existing = resp.json()
        if existing:
            device = existing[0]
            if device["name"] != name:
                device["name"] = name
                (await client.put(f"/api/devices/{device['id']}", json=device)).raise_for_status()
            return
        (await client.post("/api/devices", json={"name": name, "uniqueId": unique_id})).raise_for_status()


async def change_device_id(old_unique_id: str, new_unique_id: str, name: str) -> None:
    """Point a driver's Traccar device at a new identifier (e.g. after a phone change)."""
    async with _client() as client:
        resp = await client.get("/api/devices", params={"uniqueId": old_unique_id})
        resp.raise_for_status()
        existing = resp.json()
    if not existing:
        await ensure_device(new_unique_id, name)
        return
    device = existing[0] | {"uniqueId": new_unique_id, "name": name}
    async with _client() as client:
        (await client.put(f"/api/devices/{device['id']}", json=device)).raise_for_status()
