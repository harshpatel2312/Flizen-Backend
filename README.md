# Flizen Backend

FastAPI backend for the Flizen flight booking platform. Provides airport search (via Firebase Firestore) and real-time flight search (via Duffel Air API).

## Structure

```
app/
  main.py              # FastAPI app, CORS, routing, CLI arg parsing
  core/
    config.py          # Settings loaded from .env
    firebase.py        # Firebase Admin SDK initialisation
    logging.py         # Centralised logging setup
  models/
    airport.py         # Airport Pydantic models
    flight.py          # Flight search request/response models
    health.py          # Health check model
  routes/
    airports.py        # GET /airports/search, GET /airports/{iata}
    flights.py         # GET /api/flights/search (Duffel), POST /flights/search (legacy)
    health.py          # GET /health
  schemas/
    flight_schema.py   # Duffel offer response models
  services/
    airport_service.py # Airport lookup via Firestore
    duffel_service.py  # Duffel API integration
    flight_service.py  # Legacy flight search logic
  data/
    airports.json      # Airport data used to populate Firestore
logs/                  # Log output directory (auto-created)
requirements.txt
```

## Setup

```bash
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

Create a `.env` file:

```
DUFFEL_ACCESS_TOKEN=duffel_test_...
FIREBASE_CREDENTIALS_PATH=firebase-admin-key.json
FIREBASE_PROJECT_ID=your-project-id
CORS_ORIGINS=http://localhost:3000
ENVIRONMENT=development
```

Place your Firebase service-account JSON at the path specified by `FIREBASE_CREDENTIALS_PATH`.

## Running

```bash
uvicorn app.main:app --reload
```

Add `--log-terminal` to also print logs to stdout (logs always go to `logs/flizen.log` regardless):

```bash
uvicorn app.main:app --reload --log-terminal
```

Interactive API docs: `http://localhost:8000/docs`

## Key endpoints

| Method | Path                    | Description                                      |
|--------|-------------------------|--------------------------------------------------|
| GET    | `/health`               | Health check                                     |
| GET    | `/airports/search?q=`   | Search airports by name, city, IATA, or country  |
| GET    | `/airports/{iata}`      | Get a single airport by IATA code                |
| GET    | `/api/flights/search`   | Search live flight offers via Duffel             |

### `GET /api/flights/search` query parameters

| Parameter      | Required | Default   | Notes                                              |
|----------------|----------|-----------|----------------------------------------------------|
| `origin`       | Yes      | —         | 3-letter IATA code                                 |
| `destination`  | Yes      | —         | 3-letter IATA code                                 |
| `departure_date` | Yes    | —         | `YYYY-MM-DD`                                       |
| `adults`       | No       | `1`       | Minimum 1                                          |
| `children`     | No       | `0`       |                                                    |
| `infants`      | No       | `0`       |                                                    |
| `cabin_class`  | No       | `economy` | `economy`, `premium_economy`, `business`, `first`  |
| `currency`     | No       | —         | ISO 4217 code (e.g. `CAD`, `GBP`). Passed directly to Duffel so offers are returned in that currency. Defaults to Duffel's own default (USD) if omitted. |

## Duffel integration

- `duffel_service.py` sends a `POST /air/offer_requests` to Duffel and parses the offers from the response.
- Each offer is mapped to a `DuffelFlightOffer` schema containing: airline, flight number, origin/destination, departure/arrival times, duration, stops, segments (with per-segment details), baggage allowance, fare conditions, and total price.
- **Test mode**: tokens prefixed `duffel_test_` return synthetic offers. These cannot be booked.

## Logging

Configured in `app/core/logging.py`.

- Always writes to `logs/flizen.log` with `RotatingFileHandler` (10 MB per file, 5 backups).
- Pass `--log-terminal` at startup to also stream to stdout.
- Log level is `DEBUG` when `ENVIRONMENT=development`, `INFO` otherwise.
- All modules obtain a logger via `get_logger(__name__)`.
