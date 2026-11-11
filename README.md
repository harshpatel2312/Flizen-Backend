# Flizen Backend

FastAPI backend for the Flizen flight booking platform. It provides airport search, real-time flight search via the [Duffel Air API](https://duffel.com), Firebase-authenticated user profiles, booking drafts, Stripe payments, and webhook-driven booking fulfillment with automatic refunds.

## Structure

```
app/
  main.py                  # FastAPI app, CORS, routing, CLI arg parsing (--log-terminal)
  core/
    auth.py                # verified_uid dependency (Firebase ID token -> uid)
    config.py              # Settings loaded from .env
    firebase.py            # Firebase Admin SDK + Firestore initialisation
    logging.py             # Centralised logging setup
  models/
    airport.py             # Airport models
    booking.py             # Booking draft request/response, phone normalisation (E.164)
    flight.py              # Flight search request/response models
    health.py              # Health check model
    payment.py             # PaymentIntent request/response models
    profile.py             # User profile models
  routes/
    airports.py            # GET /airports/search, GET /airports/{iata}
    auth.py                # POST /auth/verify
    bookings.py            # drafts, booking history, booking summary, trip details
    flights.py             # GET /api/flights/search
    health.py              # GET /health
    payments.py            # POST /api/payments/intents, POST /api/payments/webhook
    profile.py             # GET /profile, PUT /profile
  schemas/
    flight_schema.py       # Duffel offer response models
  services/
    airport_service.py     # Airport lookup from the JSON cache
    booking_records.py     # Payment records: bookings/{id}/payments/{payment_intent_id}
    duffel_service.py      # Duffel offers / orders integration
    fulfillment_service.py # Stripe webhook -> Duffel order -> refund on failure
    itinerary.py           # Duffel offer/order -> stored itinerary (slices, segments)
    stripe_service.py      # Stripe PaymentIntents, refunds, webhook verification
  data/
    airports.json          # Airport database used by airport_service
    airports.csv           # Source CSV (not loaded at runtime)
docs/
  FIRESTORE.md             # Firestore data model, status flow, duplicate protection
tests/                     # pytest suite (bookings, payments, webhook, round trip, trips)
logs/                      # Log output directory (auto-created)
firebase.json              # Firebase CLI config (Firestore rules + indexes)
firestore.rules            # Security rules: owner-only reads, no client writes
firestore.indexes.json     # Composite indexes
requirements.txt
```

## Setup

```bash
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in the values:

| Variable                    | Purpose                                                                                   |
|-----------------------------|-------------------------------------------------------------------------------------------|
| `FIREBASE_CREDENTIALS_PATH` | Path to the Firebase Admin service-account JSON (default `firebase-admin-key.json`).      |
| `FIREBASE_PROJECT_ID`       | Firebase project id.                                                                      |
| `CORS_ORIGINS`              | Comma-separated allowed origins. Default: `http://localhost:5173,http://localhost:3000,http://localhost:3001`. |
| `ENVIRONMENT`               | `development` (DEBUG logging, auto-reload) or `production`.                               |
| `DUFFEL_ACCESS_TOKEN`       | Duffel API token (`duffel_test_...` locally).                                             |
| `STRIPE_SECRET_KEY`         | Stripe secret key (`sk_test_...` locally).                                                |
| `STRIPE_WEBHOOK_SECRET`     | Signing secret for `/api/payments/webhook` (`whsec_...`).                                 |
| `FRONTEND_URL`              | Frontend origin. Reserved: the Stripe return URL is currently built by the frontend.     |

Place your Firebase service-account JSON at `FIREBASE_CREDENTIALS_PATH`. Do **not** commit `.env` or `firebase-admin-key.json`; both are in `.gitignore`.

### Firestore

| Collection / path                                 | Contents                                                        |
|---------------------------------------------------|-----------------------------------------------------------------|
| `users/{uid}`                                     | User profiles (`GET` / `PUT /profile`).                         |
| `bookings/{bookingId}`                            | Booking, travellers, itinerary, and payment / fulfillment state. |
| `bookings/{bookingId}/payments/{paymentIntentId}` | One record per Stripe payment attempt (ids, amounts, statuses, refunds). |
| `stripe_events/{eventId}`                         | Processed Stripe event ids (webhook idempotency lock).          |

All reads and writes go through this backend (Firebase Admin SDK); the frontend never accesses Firestore directly. Full field reference, status flow, and duplicate protection: [docs/FIRESTORE.md](docs/FIRESTORE.md).

Security rules (`firestore.rules`: owner-only reads, no client writes) and composite indexes (`firestore.indexes.json`) are deployed with the [Firebase CLI](https://firebase.google.com/docs/cli):

```bash
npm install -g firebase-tools
firebase login
firebase deploy --only firestore:rules,firestore:indexes --project <your-project-id>
```

`GET /api/bookings` sorts by `created_at` using the `bookings` index (`uid` asc, `created_at` desc). Until that index is deployed, the endpoint logs a warning and falls back to an unordered query sorted in Python, so it keeps working.

## Running

```bash
uvicorn app.main:app --reload
```

API: `http://localhost:8000`. Interactive docs: `http://localhost:8000/docs`.

Logs always go to `logs/flizen.log`. To also print them to the terminal, start the app through `app.main` (the uvicorn CLI rejects unknown flags such as `--log-terminal`):

```bash
python -m app.main --log-terminal
```

This runs on port 8000, with auto-reload when `ENVIRONMENT=development`.

### Stripe webhooks (local)

Bookings are only fulfilled when Stripe calls `POST /api/payments/webhook`. Stripe cannot reach `localhost`, so forward events with the [Stripe CLI](https://docs.stripe.com/stripe-cli) while developing:

```bash
stripe login
stripe listen --forward-to http://localhost:8000/api/payments/webhook
# Copy the whsec_... it prints into STRIPE_WEBHOOK_SECRET, then restart the backend.
```

If this is not running, Stripe shows the payment as succeeded but the booking stays on "Securing your booking". To re-deliver a missed event: `stripe events resend <evt_id>`.

Useful test triggers:

```bash
stripe trigger payment_intent.succeeded
stripe trigger payment_intent.payment_failed
stripe trigger charge.refunded
```

## Endpoints

| Method | Path                         | Auth | Description                                                     |
|--------|------------------------------|------|-----------------------------------------------------------------|
| GET    | `/health`                    | -    | Health check                                                    |
| GET    | `/airports/search?q=`        | -    | Search airports by name, city, IATA, or country                 |
| GET    | `/airports/{iata}`           | -    | Get a single airport by IATA code                               |
| GET    | `/api/flights/search`        | -    | Search live flight offers via Duffel                            |
| POST   | `/auth/verify`               | -    | Verify a Firebase ID token from the frontend                    |
| GET    | `/profile`                   | Yes  | Fetch the signed-in user's profile                              |
| PUT    | `/profile`                   | Yes  | Save the signed-in user's profile                               |
| POST   | `/api/bookings/draft`        | Yes  | Create a booking draft from one Duffel offer                    |
| GET    | `/api/bookings`              | Yes  | List the user's bookings (newest first, up to 200)              |
| GET    | `/api/bookings/{booking_id}` | Yes  | Booking summary (used for live status polling)                  |
| GET    | `/api/bookings/{booking_id}/details` | Yes | Trip details for My Trips: itinerary, traveller names, payment history |
| POST   | `/api/payments/intents`      | Yes  | Create or reuse a Stripe PaymentIntent for a draft              |
| POST   | `/api/payments/webhook`      | Stripe signature | Stripe webhook (signature-verified, idempotent)     |

"Auth: Yes" means the request must send `Authorization: Bearer <Firebase ID token>`. Users can only read or pay for their own bookings; other users' bookings return 404.

### `POST /auth/verify`

Accepts a Firebase ID token issued by the frontend after Google sign-in and verifies it with the Firebase Admin SDK. No passwords are handled by this endpoint.

```json
// Request
{ "id_token": "<Firebase ID token>" }
// Response
{ "uid": "...", "email": "...", "name": "...", "picture": "..." }
```

Returns `401` with a generic message for expired or invalid tokens; internal error details are never included.

### `GET /api/flights/search` query parameters

| Parameter        | Required | Default   | Notes                                                                 |
|------------------|----------|-----------|-----------------------------------------------------------------------|
| `origin`         | Yes      | -         | 3-letter IATA code                                                    |
| `destination`    | Yes      | -         | 3-letter IATA code                                                    |
| `departure_date` | Yes      | -         | `YYYY-MM-DD`                                                          |
| `adults`         | No       | `1`       | Minimum 1                                                             |
| `children`       | No       | `0`       |                                                                       |
| `infants`        | No       | `0`       |                                                                       |
| `cabin_class`    | No       | `economy` | `economy`, `premium_economy`, `business`, `first`                     |
| `currency`       | No       | -         | ISO 4217 code (e.g. `CAD`). Passed to Duffel; defaults to USD.        |

Round trips are returned as a single Duffel offer with two slices and one bundled `total_amount`.

### Airport search

`/airports/search` matches IATA code, name, city, and country. An exact 3-letter IATA match is always returned first, even when many other airports match the text.

## Booking and payment flow

1. **Draft:** `POST /api/bookings/draft` re-fetches the Duffel offer and stores a booking with the server-side price, passengers, contact details, seat preferences, and the full itinerary. The client never sends a price. Submitting an identical checkout again (e.g. after going Back) returns the same open draft instead of creating a duplicate.
2. **Payment:** `POST /api/payments/intents` creates (or reuses) a Stripe PaymentIntent for the draft and records it under `bookings/{id}/payments/{payment_intent_id}`. The offer is revalidated first; if the price changed, the request is rejected. Metadata contains only `booking_id`, `uid`, and `offer_id`, never passenger PII. A booking whose card was declined (`payment_failed`) can be paid again on the same PaymentIntent.
3. **Fulfillment:** on `payment_intent.succeeded`, the webhook creates the Duffel order. The booking becomes `confirmed` only after Duffel returns an order id and booking reference, and its itinerary is refreshed from the order.
4. **Failure / refund:** if the order fails (e.g. offer expired, price changed, or Duffel rejects the request), the booking moves to `failed_refunding` and a Stripe refund is issued automatically (tagged with the booking id). It becomes `refunded` when `charge.refunded` arrives for the full amount; partial refunds are recorded without changing the booking status.

Every handled webhook (`payment_intent.*`, `charge.refunded`, `refund.*`) also updates the payment record, which never moves backwards (a late decline cannot overwrite a success). Refund events without our metadata are matched to the booking through their PaymentIntent.

Status flow:

```
draft_awaiting_payment -> fulfilling -> confirmed
draft_awaiting_payment <-> payment_failed   (declined card; retry allowed)
draft_awaiting_payment | payment_failed -> canceled
fulfilling -> failed_refunding -> refunded
```

Webhook events are deduplicated through the `stripe_events` collection, and the booking status is checked before calling Duffel, so a re-delivered event never creates a second order.

### Contact phone validation

`contact_phone` is required on drafts. Formatting characters (spaces, dashes, dots, brackets) are stripped and the result must be E.164, e.g. `+14165551234`. The value is normalised again before it is sent to Duffel, which rejects invalid numbers with `invalid_phone_number`. The frontend additionally checks that the number is real for the selected country, since a number can be E.164-shaped and still be rejected (e.g. `+1 123 555 0123`).

### Seat selection limitation

The seat map is a local visualization only. Selected seats are stored as `seat_preferences` with `confirmed_with_duffel: false` and are never sent to Duffel's `/air/orders` or `/air/seat_maps`. Airlines assign real seats at check-in until a Duffel seat-map integration is added.

## Duffel integration

- Offers come from `POST /air/offer_requests`; each offer is mapped to `DuffelFlightOffer` (airline, flight number, times, duration, stops, segments, baggage, CO2 estimate, passport requirement, fare conditions, total price).
- Orders are created with `POST /air/orders` from the webhook.
- **Test mode:** `duffel_test_...` tokens return synthetic offers. Orders can be created against Duffel's test airline ("Duffel Airways"), but no real ticket is issued.
- On order failure, only Duffel's structured error fields (`title`, `type`, `code`, `message`) are logged. The raw response body is never logged because it may contain passenger PII.

## Logging

Configured in `app/core/logging.py`.

- Always writes to `logs/flizen.log` (`RotatingFileHandler`, 10 MB per file, 5 backups).
- `python -m app.main --log-terminal` also streams logs to stdout.
- Log level is `DEBUG` when `ENVIRONMENT=development`, `INFO` otherwise. At `DEBUG`, third-party libraries (including the Stripe SDK) log request and response bodies, so treat local log files as sensitive.
- All modules obtain a logger via `get_logger(__name__)`.

## Tests

```bash
pytest tests/
```

Firestore, Duffel, and Stripe are replaced with in-memory fakes via FastAPI dependency overrides (`tests/conftest.py`), so tests make no real Firestore, Duffel, or Stripe calls.

## Production deployment checklist

1. Rotate any secrets that were ever committed (`firebase-admin-key.json`, `.env`).
2. Set `ENVIRONMENT=production`.
3. Use a live Duffel token (`duffel_live_...`) and live Stripe keys.
4. Create the Stripe webhook endpoint for `/api/payments/webhook` and put its signing secret in `STRIPE_WEBHOOK_SECRET`.
5. Deploy `firestore.rules` and `firestore.indexes.json` (`firebase deploy --only firestore:rules,firestore:indexes`).
6. Lock `CORS_ORIGINS` to the production frontend URL(s).
7. Serve over HTTPS; never expose the webhook route over plain HTTP.
8. Load the Firebase service-account credentials from a secret manager, not the filesystem.
9. Make sure Stripe can reach `/api/payments/webhook` (no auth proxy, no CDN caching).
