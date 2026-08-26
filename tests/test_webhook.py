"""Focused tests for POST /api/payments/webhook and fulfillment.

Covers:
- invalid signature -> 400
- happy path payment_intent.succeeded -> Duffel order created + booking confirmed
- duplicate webhook delivery -> Duffel called only once (event-id lock)
- duplicate order prevention across two DIFFERENT event ids for same booking
- Duffel failure -> booking marked failed_refunding + Stripe refund initiated
- refund failure -> refund_state = failed_to_initiate
- payment_intent.payment_failed / canceled / charge.refunded transitions
"""
from datetime import datetime, timezone
import json
import uuid

from app.services.duffel_service import DuffelServiceError
from app.services.fulfillment_service import (
    STATUS_CANCELED,
    STATUS_CONFIRMED,
    STATUS_DRAFT,
    STATUS_FAILED_REFUNDING,
    STATUS_PAYMENT_FAILED,
    STATUS_REFUNDED,
)
from app.services.stripe_service import (
    InvalidSignatureError,
    StripeServiceError,
)


# ─── Helpers ────────────────────────────────────────────────────────────────

def _seed_booking(db, **overrides):
    booking_id = overrides.pop("booking_id", str(uuid.uuid4()))
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "booking_id": booking_id,
        "uid": "user-a",
        "status": STATUS_DRAFT,
        "offer_id": "off_valid",
        "amount": "100.00",
        "currency": "USD",
        "slice_count": 1,
        "passenger_count": 1,
        "passengers": [{
            "type": "adult",
            "given_name": "Jane",
            "family_name": "Doe",
            "born_on": "1990-01-01",
            "gender": "female",
        }],
        "contact": {"email": "jane@example.com", "phone": "+15551234567"},
        "seat_preferences": [
            {"segment_key": "outbound-0", "passenger_index": 0,
             "seat_id_local": "12A", "confirmed_with_duffel": False},
        ],
        "payment_intent_id": "pi_test_1",
        "payment_status": "requires_payment_method",
        "created_at": now,
        "updated_at": now,
    }
    doc.update(overrides)
    db.collection("bookings").document(booking_id).set(doc)
    return booking_id


def _pi_succeeded_event(booking_id, event_id="evt_1", intent_id="pi_test_1"):
    return {
        "id": event_id,
        "type": "payment_intent.succeeded",
        "data": {"object": {
            "id": intent_id,
            "latest_charge": "ch_test_1",
            "metadata": {"flizen_booking_id": booking_id},
        }},
    }


def _post_webhook(client, event):
    return client.post(
        "/api/payments/webhook",
        content=json.dumps(event).encode("utf-8"),
        headers={"stripe-signature": "t=123,v1=fake", "content-type": "application/json"},
    )


# ─── Tests ──────────────────────────────────────────────────────────────────

def test_webhook_rejects_invalid_signature(app_and_client):
    _app, client, _duffel, _db, stripe_svc = app_and_client
    stripe_svc.raise_on_verify = InvalidSignatureError("bad sig")

    r = client.post(
        "/api/payments/webhook",
        content=b"{}",
        headers={"stripe-signature": "bad"},
    )
    assert r.status_code == 400
    assert "invalid signature" in r.json()["detail"].lower()


def test_webhook_returns_500_when_secret_not_configured(app_and_client):
    _app, client, _duffel, _db, stripe_svc = app_and_client
    stripe_svc.raise_on_verify = StripeServiceError("no secret")

    r = client.post(
        "/api/payments/webhook",
        content=b"{}",
        headers={"stripe-signature": "x"},
    )
    assert r.status_code == 500


def test_webhook_success_creates_duffel_order_and_confirms(app_and_client):
    _app, client, duffel, db, _stripe = app_and_client
    booking_id = _seed_booking(db)

    r = _post_webhook(client, _pi_succeeded_event(booking_id))
    assert r.status_code == 200, r.text
    assert r.json()["result"]["status"] == "confirmed"

    # Duffel was called exactly once, with sanitized passenger fields and an
    # idempotency key. Never with seat_services.
    assert len(duffel.order_calls) == 1
    call = duffel.order_calls[0]
    assert call["offer_id"] == "off_valid"
    assert call["amount"] == "100.00"
    assert call["currency"] == "USD"
    assert call["idempotency_key"] == f"flizen-order-{booking_id}"
    assert call["passengers"][0]["id"] == "pas_1"
    assert call["passengers"][0]["given_name"] == "Jane"
    assert call["passengers"][0]["gender"] == "f"

    stored = db._store[f"bookings/{booking_id}"]
    assert stored["status"] == STATUS_CONFIRMED
    assert stored["duffel_order_id"] == "ord_test_1"
    assert stored["duffel_booking_reference"] == "PNR123"
    # Stripe-event lock recorded.
    assert f"stripe_events/evt_1" in db._store


def test_webhook_is_idempotent_on_duplicate_delivery(app_and_client):
    _app, client, duffel, db, _stripe = app_and_client
    booking_id = _seed_booking(db)

    r1 = _post_webhook(client, _pi_succeeded_event(booking_id, event_id="evt_2"))
    assert r1.status_code == 200
    assert r1.json()["result"]["status"] == "confirmed"

    r2 = _post_webhook(client, _pi_succeeded_event(booking_id, event_id="evt_2"))
    assert r2.status_code == 200
    assert r2.json()["result"]["status"] == "duplicate"

    # Duffel was called only once, even though Stripe delivered twice.
    assert len(duffel.order_calls) == 1


def test_webhook_prevents_duplicate_order_across_different_events(app_and_client):
    _app, client, duffel, db, _stripe = app_and_client
    booking_id = _seed_booking(db)

    r1 = _post_webhook(client, _pi_succeeded_event(booking_id, event_id="evt_A"))
    assert r1.status_code == 200
    assert r1.json()["result"]["status"] == "confirmed"

    # A second, DIFFERENT event id for the same booking (e.g., manual replay or
    # a second payment_intent.succeeded arriving for the same PI). The booking
    # is already confirmed, so we must not create a second Duffel order.
    r2 = _post_webhook(client, _pi_succeeded_event(booking_id, event_id="evt_B"))
    assert r2.status_code == 200
    assert r2.json()["result"]["status"] == "already_advanced"
    assert len(duffel.order_calls) == 1


def test_webhook_duffel_failure_triggers_refund(app_and_client):
    _app, client, duffel, db, stripe_svc = app_and_client
    booking_id = _seed_booking(db)
    duffel.order_error = DuffelServiceError("airline offline")

    r = _post_webhook(client, _pi_succeeded_event(booking_id))
    assert r.status_code == 200
    body = r.json()["result"]
    assert body["status"] == "failed_refund_initiated"
    assert body["reason"] == "duffel_order_creation_failed"

    stored = db._store[f"bookings/{booking_id}"]
    assert stored["status"] == STATUS_FAILED_REFUNDING
    assert stored["refund_state"] == "initiated"
    assert stored["failure_reason"] == "duffel_order_creation_failed"

    # Stripe refund was called with the expected idempotency key.
    assert len(stripe_svc.refunds) == 1
    refund = stripe_svc.refunds[0]
    assert refund["payment_intent_id"] == "pi_test_1"
    assert refund["idempotency_key"] == f"flizen-refund-{booking_id}"


def test_webhook_marks_refund_failed_to_initiate_when_stripe_errors(app_and_client):
    _app, client, duffel, db, stripe_svc = app_and_client
    booking_id = _seed_booking(db)
    duffel.order_error = DuffelServiceError("airline offline")
    stripe_svc.raise_on_refund = StripeServiceError("stripe down")

    r = _post_webhook(client, _pi_succeeded_event(booking_id))
    assert r.status_code == 200
    assert r.json()["result"]["status"] == "failed_refund_failed"

    stored = db._store[f"bookings/{booking_id}"]
    assert stored["status"] == STATUS_FAILED_REFUNDING
    assert stored["refund_state"] == "failed_to_initiate"


def test_webhook_price_change_after_payment_triggers_refund(app_and_client):
    _app, client, duffel, db, stripe_svc = app_and_client
    booking_id = _seed_booking(db)
    duffel.offer_data = {**duffel.offer_data, "total_amount": "150.00"}

    r = _post_webhook(client, _pi_succeeded_event(booking_id))
    assert r.status_code == 200
    body = r.json()["result"]
    assert body["status"] == "failed_refund_initiated"
    assert body["reason"] == "offer_price_changed"
    assert len(duffel.order_calls) == 0
    assert len(stripe_svc.refunds) == 1
    stored = db._store[f"bookings/{booking_id}"]
    assert stored["status"] == STATUS_FAILED_REFUNDING


def test_webhook_payment_failed_marks_booking_failed(app_and_client):
    _app, client, duffel, db, _stripe = app_and_client
    booking_id = _seed_booking(db)

    event = {
        "id": "evt_pf",
        "type": "payment_intent.payment_failed",
        "data": {"object": {
            "id": "pi_test_1",
            "metadata": {"flizen_booking_id": booking_id},
        }},
    }
    r = _post_webhook(client, event)
    assert r.status_code == 200
    assert r.json()["result"]["status"] == STATUS_PAYMENT_FAILED
    stored = db._store[f"bookings/{booking_id}"]
    assert stored["status"] == STATUS_PAYMENT_FAILED
    assert len(duffel.order_calls) == 0


def test_webhook_payment_canceled(app_and_client):
    _app, client, _duffel, db, _stripe = app_and_client
    booking_id = _seed_booking(db)

    event = {
        "id": "evt_c",
        "type": "payment_intent.canceled",
        "data": {"object": {
            "id": "pi_test_1",
            "metadata": {"flizen_booking_id": booking_id},
        }},
    }
    r = _post_webhook(client, event)
    assert r.status_code == 200
    assert r.json()["result"]["status"] == STATUS_CANCELED
    assert db._store[f"bookings/{booking_id}"]["status"] == STATUS_CANCELED


def test_webhook_charge_refunded_marks_refunded(app_and_client):
    _app, client, _duffel, db, _stripe = app_and_client
    booking_id = _seed_booking(db, status=STATUS_FAILED_REFUNDING,
                               refund_state="initiated")

    event = {
        "id": "evt_r",
        "type": "charge.refunded",
        "data": {"object": {
            "id": "ch_test_1",
            "payment_intent": "pi_test_1",
            "metadata": {"flizen_booking_id": booking_id},
        }},
    }
    r = _post_webhook(client, event)
    assert r.status_code == 200
    assert r.json()["result"]["status"] == STATUS_REFUNDED
    stored = db._store[f"bookings/{booking_id}"]
    assert stored["status"] == STATUS_REFUNDED
    assert stored["refund_state"] == "completed"


def test_webhook_no_booking_metadata_is_noop(app_and_client):
    _app, client, duffel, _db, _stripe = app_and_client
    event = {
        "id": "evt_x",
        "type": "payment_intent.succeeded",
        "data": {"object": {"id": "pi_unknown", "metadata": {}}},
    }
    r = _post_webhook(client, event)
    assert r.status_code == 200
    assert r.json()["result"]["status"] == "no_booking"
    assert len(duffel.order_calls) == 0


def test_webhook_unknown_booking_id_is_noop(app_and_client):
    _app, client, duffel, _db, _stripe = app_and_client
    event = _pi_succeeded_event("does-not-exist", event_id="evt_missing")
    r = _post_webhook(client, event)
    assert r.status_code == 200
    assert r.json()["result"]["status"] == "booking_not_found"
    assert len(duffel.order_calls) == 0
