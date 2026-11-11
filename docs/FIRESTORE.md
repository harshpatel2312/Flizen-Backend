# Firestore data model

Firestore is the persistent store for profiles, bookings, and payment history.
Stripe stays authoritative for payment state and Duffel for the airline order;
Firestore mirrors both through backend writes and verified Stripe webhooks.

All reads and writes go through the FastAPI backend (Firebase Admin SDK). The
frontend never talks to Firestore directly; `firestore.rules` denies all client
writes and only allows a signed-in user to read their own documents.

```
users/{uid}                                       profile (GET / PUT /profile)
bookings/{bookingId}                              one booking (draft -> confirmed / refunded)
bookings/{bookingId}/payments/{paymentIntentId}   one Stripe payment attempt
stripe_events/{eventId}                           webhook idempotency lock
```

## `bookings/{bookingId}`

Created by `POST /api/bookings/draft`, updated by `POST /api/payments/intents`
and the Stripe webhook. `bookingId` is a UUID.

| Field | Type | Written by | Notes |
|---|---|---|---|
| `booking_id`, `uid` | string | draft | `uid` is the Firebase Auth uid (ownership) |
| `status` | string | draft, webhook | see status flow below |
| `offer_id`, `offer_expires_at` | string | draft | Duffel offer and its expiry |
| `amount`, `currency` | string | draft | from Duffel, never from the client |
| `slice_count`, `passenger_count` | int | draft | 2 slices = round trip |
| `passengers[]` | map | draft | type, given/family name, born_on, gender (sent to Duffel) |
| `contact` | map | draft | email, phone (E.164) |
| `flight_summary` | map | draft | route, airline, dates, logo: used by list views |
| `itinerary` | map | draft, refreshed on confirm | slices -> segments (see below) |
| `seat_preferences[]` | map | draft | local UI picks, `confirmed_with_duffel: false` |
| `draft_fingerprint` | string | draft | hash of the checkout; reuses an identical open draft |
| `payment_intent_id`, `payment_status`, `payment_amount_minor`, `payment_currency` | | intents | latest PaymentIntent |
| `stripe_charge_id` | string | webhook | |
| `duffel_order_id`, `duffel_booking_reference` | string | webhook | only after Duffel confirms |
| `failure_reason`, `refund_state` | string | webhook | `refund_state`: initiated, completed, partial, failed, failed_to_initiate, not_applicable |
| `created_at`, `updated_at`, `payment_received_at`, `payment_failed_at`, `confirmed_at`, `canceled_at`, `refund_initiated_at`, `refunded_at` | ISO-8601 string | | timeline |

`itinerary`:

```
{ airline, airline_logo_url,
  slices: [ { origin, origin_city, destination, destination_city,
              departing_at, arriving_at, duration, stops, fare_brand,
              segments: [ { origin, origin_city, destination, destination_city,
                            departing_at, arriving_at, duration, airline, airline_code,
                            flight_number, operating_airline, aircraft, cabin } ] } ] }
```

Times are the airports' local times as returned by Duffel; durations are ISO-8601.

### Status flow

```
draft_awaiting_payment -> fulfilling -> confirmed
draft_awaiting_payment <-> payment_failed      (declined card; retry on the same PaymentIntent)
draft_awaiting_payment | payment_failed -> canceled
fulfilling -> failed_refunding -> refunded     (Duffel order failed; automatic refund)
```

## `bookings/{bookingId}/payments/{paymentIntentId}`

One document per Stripe PaymentIntent. Written when the intent is created,
reused, or cancelled (`POST /api/payments/intents`) and by every relevant
webhook. Stores **no** client secret and **no** card data.

| Field | Notes |
|---|---|
| `payment_intent_id`, `booking_id`, `uid` | `uid` lets security rules check ownership without a lookup |
| `status` | `requires_payment_method`, `requires_action`, `processing`, `payment_failed`, `succeeded`, `canceled`, `partially_refunded`, `refunded` |
| `amount` (decimal string), `amount_minor`, `currency` | |
| `failure_code` | Stripe decline / error code, e.g. `insufficient_funds` |
| `refund_id`, `refund_status`, `amount_refunded_minor` | |
| `created_at`, `updated_at`, `succeeded_at`, `failed_at`, `canceled_at`, `refunded_at` | |

Status never moves backwards (`succeeded` cannot become `payment_failed`; a
refund cannot be undone), so late or out-of-order webhooks are harmless.

## Duplicate protection

| Risk | Protection |
|---|---|
| Stripe re-delivers an event | `stripe_events/{eventId}` is created atomically; repeats return `duplicate` |
| Two different events for one payment | booking `status` is checked before calling Duffel; Duffel order and Stripe refund use idempotency keys |
| Page refresh / retry on the payment page | PaymentIntent idempotency key per booking + price; payment records keyed by PaymentIntent id |
| User goes Back and checks out again | identical open draft is reused (`draft_fingerprint`) |
| Payment succeeds after a decline | `payment_failed` bookings can still be fulfilled and re-opened for payment |

## Indexes and rules

Defined in `firestore.indexes.json` and `firestore.rules`, wired up in
`firebase.json`. Deploy with the [Firebase CLI](https://firebase.google.com/docs/cli):

```bash
npm install -g firebase-tools
firebase login
firebase deploy --only firestore:rules,firestore:indexes --project <your-project-id>
```

| Index | Used by |
|---|---|
| `bookings`: `uid` ASC, `created_at` DESC | `GET /api/bookings` (newest first). If missing, the backend logs a warning and falls back to an unordered query sorted in Python. |
| `bookings`: `uid` ASC, `draft_fingerprint` ASC | draft reuse lookup (best-effort; skipped on error) |

Lookups by `bookings.payment_intent_id` use Firestore's automatic single-field index.
