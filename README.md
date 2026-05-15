# Planey ✈ — Flight Tracking Platform

Real-time flight tracking with interactive maps, altitude-colored routes,
Home Assistant integration, and historical flight replay.

## Features

- **Track Aircraft** — Add by tail number, auto-lookup from FlightRadar24
- **Live Map** — Leaflet dark map with animated aircraft markers and dashed planned routes
- **Automation Webhooks** — Exposes an API for tools like N8N to push flight schedules and departures
- **Expected Route Visualization** — Displays expected IFR waypoints and plots straight-line origin-to-destination paths
- **Flight Management & Reconciliation** — Auto-detects departures/landings and reconciles stuck flights via direct payload extraction
- **Timeline Scrubber** — Replay historical flight paths
- **Home Assistant** — Sensors for each aircraft with lat/lon/alt/speed/heading attributes
- **OpenSky Network** — Real-time ADS-B position tracking every 60 seconds
- **FlightRadar24 & FlightAware** — Flight schedule lookups, tracking fallback, and metadata enrichment
- **PostgreSQL** — Persistent storage with auto-cleanup of old positions

## Quick Start

### 1. Configure

```bash
cp .env.example .env
# Edit .env with your settings
```

Key settings:
| Variable | Description |
|----------|-------------|
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | Your PostgreSQL connection |
| `OPENSKY_USERNAME`, `OPENSKY_PASSWORD` | OpenSky account (free, improves rate limits) |
| `HA_URL` | Home Assistant URL (e.g., `http://homeassistant.local:8123`) |
| `HA_TOKEN` | HA Long-Lived Access Token |
| `HA_ENABLED` | Set `true` to enable HA sync |
| `WEBHOOK_TOKEN` | Optional security token for N8N Webhook endpoints (`X-Webhook-Token`) |
| `POLLING_INTERVAL_SECONDS` | Position polling interval (default: 60) |
| `POSITION_RETENTION_DAYS` | Days to keep position data (default: 90) |

### 2. Create Database

Create the `planey` database on your PostgreSQL server:

```sql
CREATE DATABASE planey;
CREATE USER planey WITH PASSWORD 'changeme';
GRANT ALL PRIVILEGES ON DATABASE planey TO planey;
```

### 3. Deploy

```bash
docker compose up -d --build
```

The app will be available at `http://your-server:8070`

### 4. Nginx Reverse Proxy

See `nginx/planey.conf.example` for proxy configuration.

## Architecture

```
┌──────────────────────────────────────────┐
│           Docker: planey                 │
│  ┌────────────────────────────────────┐  │
│  │  FastAPI (uvicorn:8000)            │  │
│  │  ├── REST API (/api/*)             │  │
│  │  ├── WebSocket (/ws)               │  │
│  │  ├── Static Files (/)              │  │
│  │  ├── APScheduler                   │  │
│  │  │   ├── OpenSky poll (60s)        │  │
│  │  │   ├── Cleanup (daily 3AM)       │  │
│  │  │   └── Downsample (weekly)       │  │
│  │  └── HA Sync Service               │  │
│  └────────────────────────────────────┘  │
│              ↕ port 8070                 │
└──────────────────────────────────────────┘
         ↕               ↕            ↕
   PostgreSQL      OpenSky API    Home Assistant
   (external)      (ADS-B data)   (sensor push)
```

## Home Assistant Sensors

Each tracked aircraft creates a sensor: `sensor.planey_<tail_number>`

**State values:**
- `ground - KJFK` — On ground at airport
- `planned - KLAX, 14:30` — Scheduled flight
- `flight - KLAX` — Currently airborne

**Attributes:**
- `latitude`, `longitude` — GPS position
- `altitude_ft` — Barometric altitude in feet
- `ground_speed_kts` — Ground speed in knots
- `heading` — True track in degrees
- `vertical_rate_fpm` — Climb/descent rate in ft/min
- `on_ground` — Boolean
- `flight_number`, `departure_airport`, `arrival_airport`
- `scheduled_departure`, `scheduled_arrival`
- `aircraft_type`, `airline`

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Health check |
| GET | `/api/stats` | System statistics |
| GET | `/api/aircraft` | List tracked aircraft |
| POST | `/api/aircraft` | Add aircraft |
| DELETE | `/api/aircraft/{id}` | Remove aircraft |
| POST | `/api/aircraft/lookup` | FR24 lookup |
| GET | `/api/flights` | List flights |
| POST | `/api/flights` | Add flight |
| GET | `/api/flights/active` | Active flights with positions |
| GET | `/api/flights/{id}` | Flight with full trail |
| POST | `/api/flights/{id}/reconcile`| Force close a stuck flight |
| GET | `/api/positions/latest` | Latest positions |
| GET | `/api/positions/{id}/history` | Position history |
| POST | `/api/webhooks/flight-filed` | Automation (N8N) — Submit filed flight plan |
| POST | `/api/webhooks/flight-departed`| Automation (N8N) — Mark flight as active |
| WS | `/ws` | Real-time updates |
