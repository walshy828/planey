"""
Terrain elevation lookup service.

Fetches ground elevation at a lat/lon using the Open-Meteo Elevation API
(free, no key required, based on Copernicus DEM at ~30 m resolution).

Caches results on a ~2 km grid so repeated calls for nearby coordinates
(e.g. points along a flight path) rarely hit the network after warmup.
"""

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

METERS_TO_FEET = 3.28084
_GRID = 0.02  # ~2 km at mid-latitudes
_ELEVATION_API = "https://api.open-meteo.com/v1/elevation"
_BATCH_LIMIT = 100  # Open-Meteo max per request

# In-memory cache: grid-snapped (lat, lon) → elevation in feet
_cache: dict[tuple[float, float], float] = {}


def _snap(lat: float, lon: float) -> tuple[float, float]:
    return (round(round(lat / _GRID) * _GRID, 4),
            round(round(lon / _GRID) * _GRID, 4))


async def get_elevation_ft(lat: float, lon: float) -> Optional[float]:
    """Return terrain elevation in feet for a single point, using cache."""
    key = _snap(lat, lon)
    if key in _cache:
        return _cache[key]

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                _ELEVATION_API,
                params={"latitude": key[0], "longitude": key[1]},
            )
            resp.raise_for_status()
            elev_m = resp.json()["elevation"][0]
            elev_ft = round(elev_m * METERS_TO_FEET, 1)
            _cache[key] = elev_ft
            return elev_ft
    except Exception as exc:
        logger.warning("Elevation lookup failed for (%.4f, %.4f): %s", lat, lon, exc)
        return None


async def get_elevations_ft(
    points: list[tuple[float, float]]
) -> list[Optional[float]]:
    """
    Batch-fetch terrain elevations for a list of (lat, lon) pairs.

    Returns a list of the same length; entries are None when the lookup
    failed (network error, out-of-range coordinate, etc.).

    Cache hits are returned immediately; only truly uncached grid cells
    are sent to the API, in chunks of up to 100 per request.
    """
    results: list[Optional[float]] = [None] * len(points)

    # Identify which indices need a network call
    uncached: list[tuple[int, tuple[float, float]]] = []
    for i, (lat, lon) in enumerate(points):
        key = _snap(lat, lon)
        if key in _cache:
            results[i] = _cache[key]
        else:
            uncached.append((i, key))

    if not uncached:
        return results

    # Deduplicate grid keys while preserving all original indices
    unique_keys = list(dict.fromkeys(k for _, k in uncached))

    # Fetch in chunks
    fetched: dict[tuple[float, float], float] = {}
    for chunk_start in range(0, len(unique_keys), _BATCH_LIMIT):
        chunk = unique_keys[chunk_start : chunk_start + _BATCH_LIMIT]
        lats = ",".join(str(k[0]) for k in chunk)
        lons = ",".join(str(k[1]) for k in chunk)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    _ELEVATION_API,
                    params={"latitude": lats, "longitude": lons},
                )
                resp.raise_for_status()
                elevations_m = resp.json()["elevation"]
                for key, elev_m in zip(chunk, elevations_m):
                    elev_ft = round(elev_m * METERS_TO_FEET, 1)
                    _cache[key] = elev_ft
                    fetched[key] = elev_ft
        except Exception as exc:
            logger.warning("Batch elevation lookup failed (%d points): %s", len(chunk), exc)

    # Populate results for previously uncached indices
    for i, key in uncached:
        if key in fetched:
            results[i] = fetched[key]
        elif key in _cache:
            results[i] = _cache[key]

    return results
