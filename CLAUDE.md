# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Planey is a real-time flight tracking platform. A single Docker container runs a FastAPI backend that serves the static frontend, polls OpenSky Network for ADS-B positions, and syncs aircraft state to Home Assistant. An external FlareSolverr container handles Cloudflare bypass for FlightAware scraping.

## Development Commands

### Running Locally (without Docker)

The `test-env/` directory contains a Python 3.14 virtualenv for local development.

```bash
# Activate the local virtualenv
source test-env/bin/activate

# Install/sync dependencies
pip install -r backend/requirements.txt

# Run the backend (from repo root, so static files resolve correctly)
cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The app expects `.env` in the working directory (or environment variables). Copy `.env.example` and fill in at minimum `DB_*` credentials.

### Docker

```bash
# Build and start all services
docker compose up -d --build

# View logs
docker compose logs -f planey

# Rebuild after backend changes
docker compose up -d --build planey
```

App is available at `http://localhost:8070` (maps to container port 8000).

### Backfill Script

```bash
# Run from repo root with virtualenv active
cd backend && python app/scripts/backfill_stats.py
```

## Architecture

### Backend (`backend/app/`)

- **`main.py`** — FastAPI app entry point. Wires up routers, mounts the static frontend at `/`, manages APScheduler lifecycle (position polling, cleanup, reconciliation jobs), and runs a startup reconciliation sweep.
- **`config.py`** — `Settings` class using pydantic-settings; reads from `.env`. All configuration flows through the singleton `settings` object.
- **`database.py`** — Async SQLAlchemy engine (`asyncpg`), session factory, and `init_db()` which runs `CREATE_ALL` plus self-healing `ALTER TABLE` migrations (no separate Alembic migration files; schema changes are idempotent SQL in `init_db`).
- **`models.py`** — Four ORM models: `Aircraft`, `Flight`, `Position`, `FlightChangeHistory`. `Flight` has a SQLAlchemy `@validates` hook that auto-corrects chronological anomalies in departure/arrival times.
- **`schemas.py`** — Pydantic request/response schemas.

**Routers** (`routers/`):
- `aircraft.py`, `flights.py`, `positions.py`, `settings.py` — Standard CRUD
- `webhooks.py` — External automation endpoints (`/api/webhooks/flight-filed`, `flight-departed`, `flight-arrived`). Secured by optional `X-Webhook-Token` header.

**Services** (`services/`):
- **`tracker.py`** — Core APScheduler job. Polls OpenSky, stores positions, auto-detects departures/landings (requires 3 consecutive on-ground readings to confirm landing), syncs HA, and broadcasts via WebSocket.
- **`reconciliation.py`** — Closes "stuck" active flights. Runs every 5 min via scheduler and on startup. Uses FlightAware/FR24 scraping to find actual arrival times.
- **`opensky.py`** — OpenSky Network REST client. Returns `StateVector` dataclasses (ICAO units auto-converted to ft/knots/fpm).
- **`flightradar.py`** — FlightRadar24 client for tail number lookup and schedule enrichment.
- **`flightaware.py`** — FlightAware AeroAPI v4 client (requires `AEROAPI_KEY`).
- **`fa_scraper.py`** — FlightAware HTML scraper via FlareSolverr when AeroAPI is unavailable.
- **`geocoder.py`** — Airport IATA/ICAO → lat/lon resolution using OpenStreetMap Nominatim.
- **`home_assistant.py`** — Pushes sensor updates to HA REST API.
- **`websocket.py`** — `ConnectionManager` that broadcasts JSON to all connected clients.
- **`cleanup.py`** — Purges positions older than `POSITION_RETENTION_DAYS`, purges `FlightChangeHistory`, and downsamples old position data.
- **`stats_calculator.py`** — Computes `summary_stats` JSON for landed flights.

### Frontend (`frontend/`)

Vanilla JS SPA — no build step, no framework. All files are served as static assets by FastAPI.

- **`index.html`** — Single HTML file containing all page markup. Uses Leaflet 1.9.4 for the map (loaded from CDN).
- **`js/app.js`** — Main controller. Initializes all modules, connects WebSocket, and coordinates data loading.
- **`js/api.js`** — Wrapper around `fetch` and WebSocket.
- **`js/map.js`** — Leaflet map, aircraft markers (altitude-colored polylines), planned route overlays.
- **`js/flights.js`** — Sidebar flight list, aircraft management UI, WebSocket message handler.
- **`js/timeline.js`** — Historical position scrubber/replay.
- **`js/auditor.js`** — Telemetry Auditor view for editing/deleting individual positions.
- **`js/utils.js`** — Shared helpers.

### Polling Modes

The tracker runs in two modes, dynamically switched based on active flights:
- **Airborne mode**: polls every `POLLING_INTERVAL_SECONDS` (default 60s)
- **Passive mode**: polls every `POLLING_INTERVAL_PASSIVE_SECONDS` (default 300s)

Both intervals can be overridden at runtime via the `/api/settings` endpoint (persisted in the `settings` DB table).

### Database Schema Notes

There are no Alembic migration files. Schema additions are handled as idempotent `ALTER TABLE ... IF NOT EXISTS` statements inside `init_db()`. Add new columns there, not in a migration file.

## Key Environment Variables

| Variable | Purpose |
|---|---|
| `DB_HOST/PORT/NAME/USER/PASSWORD` | PostgreSQL connection |
| `OPENSKY_USERNAME/PASSWORD` | ADS-B data source (improves rate limits) |
| `HA_URL`, `HA_TOKEN`, `HA_ENABLED` | Home Assistant integration |
| `FLARESOLVERR_URL` | Cloudflare bypass proxy (default: `http://flaresolverr:8191`) |
| `AEROAPI_KEY` | FlightAware AeroAPI v4 (optional, enables richer schedule data) |
| `WEBHOOK_TOKEN` | Shared secret for N8N/automation webhooks |
| `POLLING_INTERVAL_SECONDS` | Airborne poll interval (default: 60) |
| `POLLING_INTERVAL_PASSIVE_SECONDS` | Ground/idle poll interval (default: 300) |
