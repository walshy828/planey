# Planey API Documentation

This document outlines the REST API endpoints available in Planey for integrating with external tools like N8N, Home Assistant, and custom scripts.

All endpoints are hosted at `http://<your-planey-host>:8070`.

---

## Webhooks (Automation Integration)

These endpoints are specifically designed for external automations (like N8N or Zapier) to push flight events to Planey.

> [!NOTE]
> If `WEBHOOK_TOKEN` is set in your `.env` file, you must pass the `X-Webhook-Token` header with all webhook requests.

### 1. File a Flight Plan
`POST /api/webhooks/flight-filed`

Called when a new flight plan is filed. This creates a `scheduled` flight in Planey. Planey will automatically geocode the airports to draw a planned route on the map, and it will deduplicate requests if the flight is already scheduled for that day.

**Request Payload:**
```json
{
  "tail_number": "N512WB",             // Required: The aircraft registration
  "flight_number": "P123",             // Optional: Airline flight number
  "callsign": "N512WB",                // Optional: ATC Callsign
  "departure_iata": "TPA",             // Optional: Departure Airport Code
  "arrival_iata": "ATL",               // Optional: Arrival Airport Code
  "scheduled_departure": "2026-05-21T10:00:00Z", // Optional: ISO 8601 Timestamp
  "scheduled_arrival": "2026-05-21T12:00:00Z",   // Optional: ISO 8601 Timestamp
  "expected_route": "ENDED WOUND EGEST"          // Optional: IFR Waypoint String
}
```

**Example Request:**
```bash
curl -X POST http://localhost:8070/api/webhooks/flight-filed \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Token: your_secret_token" \
  -d '{"tail_number": "N512WB", "departure_iata": "TPA", "arrival_iata": "ATL", "expected_route": "ENDED WOUND EGEST"}'
```

### 2. Mark Flight as Departed
`POST /api/webhooks/flight-departed`

Called when an aircraft takes off. This finds the active scheduled flight (or creates one dynamically if missing), marks it as `active`, and immediately triggers a Home Assistant alert.

**Request Payload:**
```json
{
  "tail_number": "N512WB",             // Required: The aircraft registration
  "flight_number": "P123",             // Optional
  "departure_iata": "TPA",             // Optional
  "arrival_iata": "ATL",               // Optional
  "actual_departure": "2026-05-21T10:05:00Z" // Optional: defaults to current time
}
```

---

## Flight Management

Endpoints to manage individual flights and their statuses.

### 1. List Flights
`GET /api/flights`

Returns a list of flights. You can filter by status.

**Query Parameters:**
- `status` (string, optional): Filter by status (e.g., `active`, `scheduled`, `landed`)
- `limit` (int, default=100)

**Example Request:**
```bash
curl http://localhost:8070/api/flights?status=active
```

### 2. Reconcile Stuck Flight
`POST /api/flights/{id}/reconcile`

If a flight was tracked but the landing wasn't captured, this endpoint will scrape FlightAware to find the actual arrival time and location, forcefully closing the flight and moving the aircraft to the ground.

**Example Request:**
```bash
curl -X POST http://localhost:8070/api/flights/623bb849-d5ec-45d7-8ae7-da9d4606607b/reconcile
```

### 3. Get Flight Details & Trail
`GET /api/flights/{id}`

Returns the full flight details along with the entire array of GPS positions (the trail) recorded during the flight.

---

## Aircraft Management

Endpoints to manage the fleet of aircraft you are tracking.

### 1. List Tracked Aircraft
`GET /api/aircraft`

Returns a list of all tracked aircraft, including their latest known position and active flight details.

### 2. Add Aircraft
`POST /api/aircraft`

Add a new aircraft to the tracking database.

**Request Payload:**
```json
{
  "tail_number": "N12345",     // Required
  "icao24_hex": "A01234",      // Optional: Helps OpenSky tracking accuracy
  "aircraft_type": "C172",     // Optional
  "airline": "Private"         // Optional
}
```

### 3. FlightRadar24 Lookup
`POST /api/aircraft/lookup`

Searches FlightRadar24 to automatically find the `icao24_hex` and aircraft type for a given tail number.

**Request Payload:**
```json
{
  "tail_number": "N12345"
}
```

---

## Position Tracking

Read-only endpoints to access raw ADS-B GPS coordinates.

### 1. Get Latest Positions
`GET /api/positions/latest`

Returns a dictionary of aircraft IDs to their single most recent GPS position.

### 2. Get Position History
`GET /api/positions/{aircraft_id}/history`

Returns the breadcrumb trail of coordinates for a specific aircraft over a timeframe.

**Query Parameters:**
- `hours` (int, default=4): How many hours of history to fetch.

**Example Request:**
```bash
curl http://localhost:8070/api/positions/ab5b0771-d3d1-4ba5-8c12-e2f28013713e/history?hours=24
```
