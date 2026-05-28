# Planey API Documentation

This document outlines the REST API endpoints available in Planey for integrating with external tools like N8N, Home Assistant, and custom scripts.

All endpoints are hosted at `http://<your-planey-host>:8070`.

---

## Webhooks (Automation Integration)

These endpoints are designed for external automations (like N8N) to push flight events to Planey.

> [!NOTE]
> If `WEBHOOK_TOKEN` is set in your `.env` file, you must pass the `X-Webhook-Token` header with all webhook requests.

### 1. File a Flight Plan
`POST /api/webhooks/flight-filed`

Called when a new flight plan is filed. Creates a `scheduled` flight. Planey geocodes the airports to draw a planned route on the map. If a matching flight already exists for this aircraft within an 18-hour sliding window (same departure airport), the existing record is updated in-place rather than duplicated.

**Request Payload:**
```json
{
  "tail_number": "N512WB",                          // Required
  "flight_number": "P123",                          // Optional
  "callsign": "N512WB",                             // Optional
  "departure_iata": "TPA",                          // Optional
  "arrival_iata": "ATL",                            // Optional
  "scheduled_departure": "2026-05-21T10:00:00Z",   // Optional: ISO 8601
  "scheduled_arrival": "2026-05-21T12:00:00Z",     // Optional: ISO 8601
  "expected_route": "ENDED WOUND EGEST"             // Optional: IFR waypoint string
}
```

**Example:**
```bash
curl -X POST http://localhost:8070/api/webhooks/flight-filed \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Token: your_secret_token" \
  -d '{"tail_number": "N512WB", "departure_iata": "TPA", "arrival_iata": "ATL"}'
```

---

### 2. Mark Flight as Departed
`POST /api/webhooks/flight-departed`

Called when an aircraft takes off. Searches for a matching scheduled flight within ±18 hours (prioritizing airport/route matches) and promotes it to `active`. If no scheduled flight exists, one is created dynamically.

**Self-Healing:** Any stale active flight for the aircraft is automatically reconciled and closed to prevent dual-active conflicts. Fuel stops (same airport, gap < 45 min) are automatically detected and tagged.

**Request Payload:**
```json
{
  "tail_number": "N512WB",                          // Required
  "flight_number": "P123",                          // Optional
  "departure_iata": "TPA",                          // Optional
  "arrival_iata": "ATL",                            // Optional
  "actual_departure": "2026-05-21T10:05:00Z",      // Optional: defaults to now
  "scheduled_arrival": "2026-05-21T12:00:00Z"      // Optional
}
```

---

### 3. Mark Flight as Arrived
`POST /api/webhooks/flight-arrived`

Called when an aircraft lands. Finds the active flight and marks it `landed`, records actual arrival time, geocodes the arrival airport, places a grounded position on the map, calculates summary stats, and notifies Home Assistant.

**Resilience:** If the departure webhook was missed, promotes a matching scheduled flight directly to `landed`. If no flight exists at all, creates a landed record dynamically.

**Request Payload:**
```json
{
  "tail_number": "N512WB",                          // Required
  "flight_number": "P123",                          // Optional
  "arrival_iata": "ATL",                            // Optional
  "actual_arrival": "2026-05-21T12:00:00Z"         // Optional: defaults to now
}
```

**Example:**
```bash
curl -X POST http://localhost:8070/api/webhooks/flight-arrived \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Token: your_secret_token" \
  -d '{"tail_number": "N512WB", "arrival_iata": "ATL", "actual_arrival": "2026-05-21T12:00:00Z"}'
```

---

### 4. Flight Spotted In-Flight
`POST /api/webhooks/flight-spotted`

Called when FlightAware detects an aircraft airborne with no filed flight plan (the _"N982LF spotted in flight near Worcester, MA"_ alert). Use this for VFR and helicopter flights that will never generate filed/departed/arrived emails.

This endpoint:
1. Immediately triggers a position poll for the aircraft (OpenSky → FR24 fallback) so GPS coordinates are captured right away instead of waiting up to 5 minutes for the passive poll cycle.
2. Activates fast polling mode (airborne interval, default 60s) for up to 30 minutes.

**Returns** `200` with the active flight if a position was captured, or `202 Accepted` if no ADS-B data is available yet (fast polling will capture it on the next cycle).

If an active flight already exists for the aircraft, returns it immediately without triggering a redundant poll.

**Request Payload:**
```json
{
  "tail_number": "N982LF",                          // Required
  "spotted_time": "2026-05-27T21:06:00Z",          // Optional: ISO 8601
  "location": "Worcester, MA"                       // Optional: approximate location string
}
```

**Example:**
```bash
curl -X POST http://localhost:8070/api/webhooks/flight-spotted \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Token: your_secret_token" \
  -d '{"tail_number": "N982LF", "spotted_time": "2026-05-27T21:06:00Z", "location": "Worcester, MA"}'
```

---

### 5. Flight Tracking Stopped
`POST /api/webhooks/flight-tracking-stopped`

Called when FlightAware stops tracking an aircraft (the _"N982LF tracking stopped near Orange, MA from Worcester, MA"_ alert). Marks the active flight as `landed`, uses the last known GPS telemetry position for arrival coordinates (more accurate than forward-geocoding a city name), calculates summary stats, and notifies Home Assistant.

Deduplicates against recent landed flights (within 4 hours). Creates a dynamic landed record if no active flight is found.

**Request Payload:**
```json
{
  "tail_number": "N982LF",                          // Required
  "tracking_stopped_time": "2026-05-27T21:23:00Z", // Optional: ISO 8601, defaults to now
  "location": "Orange, MA",                         // Optional: where tracking stopped
  "from_location": "Worcester, MA"                  // Optional: origin location
}
```

**Example:**
```bash
curl -X POST http://localhost:8070/api/webhooks/flight-tracking-stopped \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Token: your_secret_token" \
  -d '{"tail_number": "N982LF", "tracking_stopped_time": "2026-05-27T21:23:00Z", "location": "Orange, MA", "from_location": "Worcester, MA"}'
```

---

## Flight Management

### List Flights
`GET /api/flights`

Returns a list of flights, optionally filtered by status.

**Query Parameters:**
- `status` (string, optional): `active`, `scheduled`, `landed`, `cancelled`
- `limit` (int, default=100)

```bash
curl http://localhost:8070/api/flights?status=active
```

### Get Active Flights
`GET /api/flights/active`

Returns all currently active flights with their recent position trail.

### Get Flight Details
`GET /api/flights/{flight_id}`

Returns full flight details plus the complete GPS position trail.

### Get Flight Positions
`GET /api/flights/{flight_id}/positions`

Returns only the position trail for a flight.

### Get Flight Change History
`GET /api/flights/{flight_id}/history`

Returns the audit trail of every field change recorded for a flight, including source (webhook, tracker, reconciliation, manual).

### Create Flight
`POST /api/flights`

Manually create a flight record.

**Request Payload:**
```json
{
  "aircraft_id": "uuid",
  "flight_number": "P123",
  "departure_iata": "TPA",
  "arrival_iata": "ATL",
  "scheduled_departure": "2026-05-21T10:00:00Z",
  "status": "scheduled"
}
```

### Update Flight
`PUT /api/flights/{flight_id}`

Update any field on a flight record.

### Delete Flight
`DELETE /api/flights/{flight_id}`

Delete a flight and all its associated position history.

### Reconcile Stuck Flight
`POST /api/flights/{flight_id}/reconcile`

Manually reconcile a flight that is stuck in `active` status. Scrapes FlightAware/FR24 to find the actual arrival time and closes the flight.

```bash
curl -X POST http://localhost:8070/api/flights/623bb849-d5ec-45d7-8ae7-da9d4606607b/reconcile
```

### Merge Flights
`POST /api/flights/{flight_id}/merge/{source_flight_id}`

Merges all positions from `source_flight_id` into `flight_id` and deletes the source. Useful for cleaning up duplicate flight records created when a restart interrupted an active leg.

---

## Aircraft Management

### List Tracked Aircraft
`GET /api/aircraft`

Returns all tracked aircraft with their latest position and active flight.

### Get Aircraft
`GET /api/aircraft/{aircraft_id}`

Returns a single aircraft with latest position and active flight.

### Add Aircraft
`POST /api/aircraft`

Add a new aircraft to the tracking database.

**Request Payload:**
```json
{
  "tail_number": "N12345",     // Required
  "icao24_hex": "A01234",      // Optional: needed for OpenSky ADS-B tracking
  "aircraft_type": "C172",     // Optional
  "airline": "Private",        // Optional
  "display_name": "My Plane",  // Optional
  "category": "plane"          // Optional: "plane" or "helicopter"
}
```

### Update Aircraft
`PUT /api/aircraft/{aircraft_id}`

Update any field on an aircraft record (tail number, ICAO hex, type, category, active status, etc.).

### Delete Aircraft
`DELETE /api/aircraft/{aircraft_id}`

Remove an aircraft and all its associated flights and positions.

### FlightRadar24 Lookup
`POST /api/aircraft/lookup`

Searches FlightRadar24 to find the `icao24_hex`, aircraft type, and other metadata for a given tail number.

**Request Payload:**
```json
{
  "tail_number": "N12345"
}
```

### Manual Position Poll
`POST /api/aircraft/{aircraft_id}/poll`

Triggers an immediate position poll for a single aircraft (OpenSky first, FR24 fallback). Useful for forcing a position update outside the normal poll cycle.

```bash
curl -X POST http://localhost:8070/api/aircraft/ab5b0771-d3d1-4ba5-8c12-e2f28013713e/poll
```

### FlightAware Schedule Sync
`POST /api/aircraft/{aircraft_id}/sync_fa`

Scrapes FlightAware to find and import upcoming scheduled flights for the aircraft. Deduplicates against existing records.

```bash
curl -X POST http://localhost:8070/api/aircraft/ab5b0771-d3d1-4ba5-8c12-e2f28013713e/sync_fa
```

---

## Position Tracking

### Get Latest Positions
`GET /api/positions/latest`

Returns the most recent GPS position for every active tracked aircraft.

### Get Position History
`GET /api/positions/{aircraft_id}/history`

Returns the breadcrumb trail of coordinates for an aircraft over a time window.

**Query Parameters:**
- `hours` (int, default=24, max=720): Hours of history to return.

```bash
curl http://localhost:8070/api/positions/ab5b0771-d3d1-4ba5-8c12-e2f28013713e/history?hours=24
```

### Update Position
`PUT /api/positions/{position_id}`

Edit a position record (coordinates, altitude, flight assignment, etc.). Automatically recalculates summary stats for any affected flights.

### Delete Position
`DELETE /api/positions/{position_id}`

Delete a single position report. Automatically recalculates summary stats for the affected flight.

---

## Settings

### Get Settings
`GET /api/settings`

Returns all current runtime settings as a key/value dictionary.

```bash
curl http://localhost:8070/api/settings
```

### Update Settings
`POST /api/settings`

Update one or more runtime settings. Changes take effect immediately (polling intervals are rescheduled without restart).

**Request Payload:**
```json
{
  "settings": {
    "polling_interval_seconds": "60",
    "polling_interval_passive_seconds": "300",
    "manual_airborne_mode": "true"
  }
}
```

**Common keys:**

| Key | Description |
|---|---|
| `polling_interval_seconds` | Airborne poll interval in seconds (default: 60) |
| `polling_interval_passive_seconds` | Ground/idle poll interval in seconds (default: 300) |
| `manual_airborne_mode` | `"true"` forces fast polling for 30 minutes regardless of active flights |

### Run Reconciliation Sweep
`POST /api/settings/reconcile`

Triggers an on-demand reconciliation pass for all aircraft with open/stuck active flights. Normally runs automatically every 5 minutes.

```bash
curl -X POST http://localhost:8070/api/settings/reconcile
```
