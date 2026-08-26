"""Round-trip tests: one authoritative Duffel offer with two slices.

The invariants under test:
- Search with return_date builds a 2-slice offer_request payload.
- The parsed offer exposes both slices under `slices`, sharing one offer id
  and one Duffel-authoritative `total_amount`.
- Booking draft, PaymentIntent, and Duffel order creation all treat that one
  offer id as the single bookable unit. There is never a browser-side
  combination of two prices.
- Webhook idempotency and Duffel-failure refund flow all continue to work
  for round-trip bookings exactly as for one-way bookings.
- One-way bookings continue working.
"""
from datetime import datetime, timezone
import json
import uuid

import pytest

from app.services.duffel_service import DuffelService, DuffelOfferNotFoundError, DuffelServiceError
from app.services.fulfillment_service import STATUS_CONFIRMED, STATUS_FAILED_REFUNDING


# ─── Duffel service unit tests ────────────────────────────────────────────

def test_offer_request_payload_has_two_slices_for_round_trip():
    svc = DuffelService()
    payload = svc._build_offer_request(
        origin="YYZ", destination="JFK",
        departure_date="2026-05-20",
        adults=1, children=0, infants=0,
        cabin_class="economy",
        currency="USD",
        return_date="2026-05-27",
    )
    slices = payload["data"]["slices"]
    assert len(slices) == 2
    assert slices[0] == {"origin": "YYZ", "destination": "JFK", "departure_date": "2026-05-20"}
    assert slices[1] == {"origin": "JFK", "destination": "YYZ", "departure_date": "2026-05-27"}


def test_offer_request_payload_has_one_slice_for_one_way():
    svc = DuffelService()
    payload = svc._build_offer_request(
        origin="YYZ", destination="JFK",
        departure_date="2026-05-20",
        adults=1, children=0, infants=0,
        cabin_class="economy",
    )
    assert len(payload["data"]["slices"]) == 1


def _duffel_offer_two_slices():
    """Raw Duffel-shaped offer payload used to exercise the parser."""
    return {
        "id": "off_rt_1",
        "total_amount": "742.55",
        "total_currency": "USD",
        "passenger_identity_documents_required": False,
        "passengers": [{"id": "pas_1", "type": "adult"}],
        "slices": [
            {
                "fare_brand_name": "Basic",
                "segments": [{
                    "operating_carrier": {"name": "Delta"},
                    "operating_carrier_flight_number": "100",
                    "origin": {"iata_code": "YYZ"},
                    "destination": {"iata_code": "JFK"},
                    "departing_at": "2026-05-20T09:00:00",
                    "arriving_at":  "2026-05-20T11:00:00",
                    "duration": "PT2H",
                    "aircraft": {"name": "Boeing 737"},
                    "passengers": [{"cabin_class": "economy"}],
                }],
            },
            {
                "segments": [{
                    "operating_carrier": {"name": "Delta"},
                    "operating_carrier_flight_number": "101",
                    "origin": {"iata_code": "JFK"},
                    "destination": {"iata_code": "YYZ"},
                    "departing_at": "2026-05-27T18:00:00",
                    "arriving_at":  "2026-05-27T20:00:00",
                    "duration": "PT2H",
                    "aircraft": {"name": "Boeing 737"},
                    "passengers": [{"cabin_class": "economy"}],
                }],
            },
        ],
    }


def test_parsed_offer_exposes_two_slices_and_one_total():
    svc = DuffelService()
    parsed = svc._parse_single_offer(_duffel_offer_two_slices())
    assert parsed is not None
    # ONE offer id, ONE authoritative total across the whole itinerary.
    assert parsed.id == "off_rt_1"
    assert parsed.price == "742.55"
    assert parsed.currency == "USD"
    assert parsed.slice_count == 2
    assert len(parsed.slices) == 2
    outbound, inbound = parsed.slices
    assert (outbound.origin, outbound.destination) == ("YYZ", "JFK")
    assert (inbound.origin,  inbound.destination)  == ("JFK", "YYZ")
    # Top-level fields still describe the outbound slice for legacy display.
    assert parsed.origin == "YYZ" and parsed.destination == "JFK"


# ─── Booking draft: two-slice offer ────────────────────────────────────────

def _rt_body(**overrides):
    body = {
        "offer_id": "off_rt_valid",
        "passengers": [{
            "type": "adult",
            "given_name": "Jane", "family_name": "Doe",
            "born_on": "1990-01-01", "gender": "female",
        }],
        "contact_email": "jane@example.com",
        "seat_preferences": [],
    }
    body.update(overrides)
    return body


def _seed_rt_offer(duffel, *, amount="742.55", currency="USD", expired=False):
    """Configure the fake Duffel offer as a 2-slice round-trip."""
    if expired:
        duffel.offer_error = DuffelOfferNotFoundError("gone")
        return
    duffel.offer_data = {
        "id": "off_rt_valid",
        "total_amount": amount,
        "total_currency": currency,
        "expires_at": "2026-08-27T00:00:00Z",
        "passengers": [{"id": "pas_1", "type": "adult"}],
        "slices": [
            {
                "segments": [{
                    "operating_carrier": {"name": "Delta"},
                    "origin": {"iata_code": "YYZ"},
                    "destination": {"iata_code": "JFK"},
                    "departing_at": "2026-05-20T09:00:00",
                    "arriving_at":  "2026-05-20T11:00:00",
                }],
            },
            {
                "segments": [{
                    "operating_carrier": {"name": "Delta"},
                    "origin": {"iata_code": "JFK"},
                    "destination": {"iata_code": "YYZ"},
                    "departing_at": "2026-05-27T18:00:00",
                    "arriving_at":  "2026-05-27T20:00:00",
                }],
            },
        ],
    }


def test_draft_accepts_two_slice_offer_and_stores_round_trip_summary(app_and_client):
    _app, client, duffel, db, _stripe = app_and_client
    _seed_rt_offer(duffel)

    r = client.post("/api/bookings/draft", json=_rt_body())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["amount"] == "742.55"
    assert body["currency"] == "USD"
    assert body["slice_count"] == 2

    stored = db._store[f"bookings/{body['booking_id']}"]
    fs = stored["flight_summary"]
    assert fs["is_round_trip"] is True
    assert fs["from_airport"] == "YYZ" and fs["to_airport"] == "JFK"
    assert fs["return_from_airport"] == "JFK" and fs["return_to_airport"] == "YYZ"
    assert fs["departure_date"] == "2026-05-20"
    assert fs["return_departure_date"] == "2026-05-27"


def test_draft_still_rejects_legacy_two_offer_round_trip(app_and_client):
    """Belt-and-braces: the old return_offer_id path stays rejected."""
    _app, client, duffel, _db, _stripe = app_and_client
    _seed_rt_offer(duffel)

    r = client.post("/api/bookings/draft", json=_rt_body(return_offer_id="off_other"))
    assert r.status_code == 400


def test_draft_rejects_expired_round_trip_offer(app_and_client):
    _app, client, duffel, _db, _stripe = app_and_client
    _seed_rt_offer(duffel, expired=True)
    r = client.post("/api/bookings/draft", json=_rt_body())
    assert r.status_code == 400
    assert "no longer available" in r.json()["detail"].lower()


def test_draft_price_is_from_duffel_not_client(app_and_client):
    """The request has no price field. Server derives amount from Duffel."""
    _app, client, duffel, db, _stripe = app_and_client
    _seed_rt_offer(duffel, amount="1234.56", currency="USD")

    r = client.post("/api/bookings/draft", json=_rt_body())
    assert r.status_code == 201
    stored = db._store[f"bookings/{r.json()['booking_id']}"]
    assert stored["amount"] == "1234.56"


# ─── PaymentIntent + summary shape carry the RT total unchanged ──────────

def test_payment_intent_uses_authoritative_combined_price(app_and_client):
    _app, client, duffel, db, stripe_svc = app_and_client
    _seed_rt_offer(duffel, amount="742.55", currency="USD")

    r = client.post("/api/bookings/draft", json=_rt_body())
    assert r.status_code == 201
    booking_id = r.json()["booking_id"]

    r2 = client.post("/api/payments/intents", json={"booking_id": booking_id})
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["display_amount"] == "742.55"
    assert body["currency"] == "USD"

    create_call = next(c for c in stripe_svc.calls if c[0] == "create")
    # 742.55 USD -> 74255 minor units, single PaymentIntent for the whole trip.
    assert create_call[1]["amount_minor"] == 74255
    assert create_call[1]["currency"] == "USD"


# ─── Webhook / Duffel order for round-trip ────────────────────────────────

def _seed_rt_booking(db, duffel, *, amount="742.55", currency="USD"):
    """Draft + attach a payment_intent_id, mirroring what /intents does."""
    _seed_rt_offer(duffel, amount=amount, currency=currency)
    booking_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "booking_id": booking_id,
        "uid": "user-a",
        "status": "draft_awaiting_payment",
        "offer_id": "off_rt_valid",
        "amount": amount,
        "currency": currency,
        "slice_count": 2,
        "passenger_count": 1,
        "passengers": [{
            "type": "adult",
            "given_name": "Jane", "family_name": "Doe",
            "born_on": "1990-01-01", "gender": "female",
        }],
        "contact": {"email": "jane@example.com", "phone": "+15551234567"},
        "flight_summary": {
            "from_airport": "YYZ", "to_airport": "JFK",
            "return_from_airport": "JFK", "return_to_airport": "YYZ",
            "is_round_trip": True,
            "departure_date": "2026-05-20",
            "return_departure_date": "2026-05-27",
        },
        "payment_intent_id": "pi_rt_1",
        "payment_status": "requires_payment_method",
        "created_at": now,
        "updated_at": now,
    }
    db.collection("bookings").document(booking_id).set(doc)
    return booking_id


def _pi_succeeded(booking_id, event_id):
    return {
        "id": event_id,
        "type": "payment_intent.succeeded",
        "data": {"object": {
            "id": "pi_rt_1",
            "latest_charge": "ch_rt_1",
            "metadata": {"flizen_booking_id": booking_id},
        }},
    }


def _post_webhook(client, event):
    return client.post(
        "/api/payments/webhook",
        content=json.dumps(event).encode("utf-8"),
        headers={"stripe-signature": "t=1,v1=x", "content-type": "application/json"},
    )


def test_round_trip_success_creates_one_order_with_one_offer_id(app_and_client):
    _app, client, duffel, db, _stripe = app_and_client
    booking_id = _seed_rt_booking(db, duffel)

    r = _post_webhook(client, _pi_succeeded(booking_id, "evt_rt_ok"))
    assert r.status_code == 200
    assert r.json()["result"]["status"] == "confirmed"

    # ONE Duffel order call, selected_offers implicitly is [offer_id]:
    assert len(duffel.order_calls) == 1
    call = duffel.order_calls[0]
    assert call["offer_id"] == "off_rt_valid"
    # Duffel amount matches the authoritative total we stored — no combination.
    assert call["amount"] == "742.55"
    assert call["currency"] == "USD"
    # Idempotency key is per booking, not per slice.
    assert call["idempotency_key"] == f"flizen-order-{booking_id}"

    stored = db._store[f"bookings/{booking_id}"]
    assert stored["status"] == STATUS_CONFIRMED
    assert stored["duffel_order_id"] == "ord_test_1"


def test_round_trip_duplicate_webhook_creates_one_order(app_and_client):
    _app, client, duffel, db, _stripe = app_and_client
    booking_id = _seed_rt_booking(db, duffel)

    r1 = _post_webhook(client, _pi_succeeded(booking_id, "evt_rt_dup"))
    r2 = _post_webhook(client, _pi_succeeded(booking_id, "evt_rt_dup"))
    assert r1.json()["result"]["status"] == "confirmed"
    assert r2.json()["result"]["status"] == "duplicate"
    assert len(duffel.order_calls) == 1


def test_round_trip_duffel_failure_refunds_the_combined_amount(app_and_client):
    _app, client, duffel, db, stripe_svc = app_and_client
    booking_id = _seed_rt_booking(db, duffel)
    duffel.order_error = DuffelServiceError("airline offline")

    r = _post_webhook(client, _pi_succeeded(booking_id, "evt_rt_fail"))
    assert r.status_code == 200
    body = r.json()["result"]
    assert body["status"] == "failed_refund_initiated"
    stored = db._store[f"bookings/{booking_id}"]
    assert stored["status"] == STATUS_FAILED_REFUNDING
    # One refund, referencing the single PaymentIntent for the whole trip.
    assert len(stripe_svc.refunds) == 1
    assert stripe_svc.refunds[0]["payment_intent_id"] == "pi_rt_1"


# ─── One-way still works ──────────────────────────────────────────────────

def test_one_way_flow_still_works(app_and_client):
    """Regression guard: one-slice offers continue producing single-slice drafts."""
    _app, client, _duffel, db, _stripe = app_and_client
    # Default fake Duffel offer is a one-slice, 100.00 USD offer.
    r = client.post("/api/bookings/draft", json=_rt_body(offer_id="off_valid"))
    assert r.status_code == 201
    stored = db._store[f"bookings/{r.json()['booking_id']}"]
    assert stored["slice_count"] == 1
    assert stored["flight_summary"].get("is_round_trip") is False
