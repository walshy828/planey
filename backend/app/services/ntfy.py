"""
ntfy Push Notification Service

Sends push notifications to an ntfy server (ntfy.sh or self-hosted)
when aircraft events occur (scheduled, departed, landed).
"""

import logging
from typing import Optional

import httpx

from app.models import Aircraft, Flight

logger = logging.getLogger(__name__)

_DEFAULT_SERVER = "https://ntfy.sh"


async def _send(server: str, topic: str, title: str, message: str, tags: str = "") -> None:
    url = f"{server.rstrip('/')}/"
    payload: dict = {"topic": topic, "title": title, "message": message}
    if tags:
        payload["tags"] = tags.split(",")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            logger.info(f"ntfy notification sent to {server} topic={topic!r}: {title!r}")
    except Exception as exc:
        logger.warning(f"ntfy notification failed for {server}/{topic}: {exc}")


def _route(flight: Flight) -> str:
    dep = flight.departure_iata or flight.departure_name or "?"
    arr = flight.arrival_iata or flight.arrival_name or "?"
    return f"{dep} → {arr}"


async def notify_scheduled(aircraft: Aircraft, flight: Flight) -> None:
    if not aircraft.ntfy_on_scheduled or not aircraft.ntfy_topic:
        return
    server = aircraft.ntfy_server or _DEFAULT_SERVER
    tail = aircraft.tail_number
    route = _route(flight)
    fn = f" ({flight.flight_number})" if flight.flight_number else ""
    dep_str = ""
    if flight.scheduled_departure:
        dep_str = f" at {flight.scheduled_departure.strftime('%H:%M UTC')}"
    await _send(
        server,
        aircraft.ntfy_topic,
        title=f"✈️ Flight filed: {tail}",
        message=f"{tail}{fn} filed {route}{dep_str}",
        tags="airplane",
    )


async def notify_departed(aircraft: Aircraft, flight: Flight) -> None:
    if not aircraft.ntfy_on_departed or not aircraft.ntfy_topic:
        return
    server = aircraft.ntfy_server or _DEFAULT_SERVER
    tail = aircraft.tail_number
    route = _route(flight)
    fn = f" ({flight.flight_number})" if flight.flight_number else ""
    await _send(
        server,
        aircraft.ntfy_topic,
        title=f"🛫 Departed: {tail}",
        message=f"{tail}{fn} departed {route}",
        tags="airplane,white_check_mark",
    )


async def notify_landed(aircraft: Aircraft, flight: Flight) -> None:
    if not aircraft.ntfy_on_landed or not aircraft.ntfy_topic:
        return
    server = aircraft.ntfy_server or _DEFAULT_SERVER
    tail = aircraft.tail_number
    dest = flight.arrival_iata or flight.arrival_name or "destination"
    fn = f" ({flight.flight_number})" if flight.flight_number else ""
    duration_str = ""
    if flight.actual_departure and flight.actual_arrival:
        mins = int((flight.actual_arrival - flight.actual_departure).total_seconds() / 60)
        duration_str = f" after {mins // 60}h {mins % 60}m"
    await _send(
        server,
        aircraft.ntfy_topic,
        title=f"🛬 Landed: {tail}",
        message=f"{tail}{fn} landed at {dest}{duration_str}",
        tags="airplane_arriving",
    )


async def send_test(server: str, topic: str, tail_number: str) -> None:
    await _send(
        server or _DEFAULT_SERVER,
        topic,
        title=f"🔔 Test: {tail_number}",
        message=f"ntfy notifications are working for {tail_number}",
        tags="bell",
    )
