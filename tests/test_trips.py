"""My Trips persistence: itinerary, draft reuse, trip details, payment records.

Covers:
- drafts store a full itinerary and reuse an identical open draft (no duplicates)
- GET /api/bookings/{id}/details: ownership, itinerary, traveller names only,
  payment history, no secrets / ids / sensitive PII
- payment records: written on intent creation and by webhooks, keyed by
  PaymentIntent id, never regress, never store the client secret
- a payment that succeeds after a decline is fulfilled (retry on the same intent)
- refunds: refund id/status recorded, partial vs full, refund.* events,
  lookup by PaymentIntent when metadata is missing
- list falls back to an unordered query when the Firestore index is missing
"""
from datetime import datetime, timedelta, timezone
import json
import uuid

from google.api_core.exceptions import FailedPrecondition

from app.core.auth import verified_uid
from app.services.duffel_service import DuffelServiceError
from app.services.fulfillment_service import (
    STATUS_CONFIRMED,
    STATUS_DRAFT,
    STATUS_FAILED_REFUNDING,
    STATUS_PAYMENT_FAILED,
    STATUS_REFUNDED,
)


FUTURE = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat().replace("+00:00", "Z")

DRAFT_BODY = {
    "offer_id": "off_valid",
    "passengers": [{
        "type": "adult", "given_name": "Jane", "family_name": "Doe",
        "born_on": "1990-01-01", "gender": "female",
    }],
    "contact_email": "jane@example.com",
    "contact_phone": "+1 416 555 0123",
    "seat_preferences": [],
}


def _segment(origin, dest, dep, arr, number):
    return {
        "origin": {"iata_code": origin, "city_name": f"{origin}-city"},
        "destination": {"iata_code": dest, "city_name": f"{dest}-city"},
        "departing_at": dep,
        "arriving_at": arr,
        "duration": "PT2H",
        "marketing_carrier": {"name": "Duffel Airways", "iata_code": "ZZ"},
        "marketing_carrier_flight_number": number,
        "operating_carrier": {"name": "Duffel Airways", "iata_code": "ZZ"},
        "aircraft": {"name": "Airbus A320"},
        "passengers": [{"cabin_class_marketing_name": "Economy"}],
    }


def _rich_offer(**overrides):
    offer = {
        "id": "off_valid",
        "total_amount": "100.00",
        "total_currency": "USD",
        "expires_at": FUTURE,
        "owner": {"name": "Duffel Airways", "logo_symbol_url": "https://assets.duffel.com/zz.svg"},
        "passengers": [{"id": "pas_1", "type": "adult"}],
        "slices": [
            {"duration": "PT2H", "segments": [_segment("YYZ", "ORD", "2026-12-10T08:00:00", "2026-12-10T09:30:00", "100")]},
            {"duration": "PT2H", "segments": [_segment("ORD", "YYZ", "2026-12-17T18:00:00", "2026-12-17T21:00:00", "101")]},
        ],
    }
    offer.update(overrides)
    return offer


def _seed(db, **overrides):
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
            "type": "adult", "given_name": "Jane", "family_name": "Doe",
            "born_on": "1990-01-01", "gender": "female",
        }],
        "contact": {"email": "jane@example.com", "phone": "+14165550123"},
        "payment_intent_id": "pi_test_1",
        "created_at": now,
        "updated_at": now,
    }
    doc.update(overrides)
    db.collection("bookings").document(booking_id).set(doc)
    return booking_id


def _payment(db, booking_id, pi="pi_test_1"):
    return db._store.get(f"bookings/{booking_id}/payments/{pi}")


def _post_webhook(client, event):
    return client.post(
        "/api/payments/webhook",
        content=json.dumps(event).encode("utf-8"),
        headers={"stripe-signature": "t=1,v1=fake", "content-type": "application/json"},
    )


def _event(event_id, event_type, obj):
    return {"id": event_id, "type": event_type, "data": {"object": obj}}


# ─── Drafts ─────────────────────────────────────────────────────────────────

def test_draft_stores_full_itinerary(app_and_client):
    _app, client, duffel, db, _stripe = app_and_client
    duffel.offer_data = _rich_offer()

    r = client.post("/api/bookings/draft", json=DRAFT_BODY)
    assert r.status_code == 201, r.text
    stored = db._store[f"bookings/{r.json()['booking_id']}"]

    itin = stored["itinerary"]
    assert itin["airline"] == "Duffel Airways"
    assert [s["origin"] for s in itin["slices"]] == ["YYZ", "ORD"]
    seg = itin["slices"][0]["segments"][0]
    assert seg["flight_number"] == "ZZ100"
    assert seg["origin_city"] == "YYZ-city"
    assert seg["cabin"] == "Economy"
    assert stored["flight_summary"]["airline_logo_url"].endswith("zz.svg")
    assert stored["draft_fingerprint"]


def test_identical_checkout_reuses_open_draft(app_and_client):
    _app, client, duffel, db, _stripe = app_and_client
    duffel.offer_data = _rich_offer()

    first = client.post("/api/bookings/draft", json=DRAFT_BODY).json()["booking_id"]
    second = client.post("/api/bookings/draft", json=DRAFT_BODY).json()["booking_id"]
    assert first == second
    assert len([k for k in db._store if k.count("/") == 1 and k.startswith("bookings/")]) == 1


def test_changed_checkout_creates_new_draft(app_and_client):
    _app, client, duffel, _db, _stripe = app_and_client
    duffel.offer_data = _rich_offer()

    first = client.post("/api/bookings/draft", json=DRAFT_BODY).json()["booking_id"]
    changed = {**DRAFT_BODY, "seat_preferences": [
        {"segment_key": "outbound-0", "passenger_index": 0, "seat_id_local": "3F"},
    ]}
    second = client.post("/api/bookings/draft", json=changed).json()["booking_id"]
    assert first != second


def test_paid_or_expired_draft_is_not_reused(app_and_client):
    _app, client, duffel, db, _stripe = app_and_client
    duffel.offer_data = _rich_offer()

    first = client.post("/api/bookings/draft", json=DRAFT_BODY).json()["booking_id"]
    db._store[f"bookings/{first}"]["status"] = STATUS_CONFIRMED
    second = client.post("/api/bookings/draft", json=DRAFT_BODY).json()["booking_id"]
    assert second != first

    db._store[f"bookings/{second}"]["offer_expires_at"] = "2020-01-01T00:00:00Z"
    third = client.post("/api/bookings/draft", json=DRAFT_BODY).json()["booking_id"]
    assert third not in (first, second)


# ─── Trip details ───────────────────────────────────────────────────────────

_DETAIL_FORBIDDEN = {
    "payment_intent_id", "stripe_charge_id", "client_secret", "offer_id",
    "duffel_order_id", "uid", "born_on", "passport", "phone", "contact",
    "passengers", "seat_preferences", "draft_fingerprint", "refund_id",
}


def _all_keys(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _all_keys(v)
    elif isinstance(value, list):
        for item in value:
            yield from _all_keys(item)


def test_details_returns_trip_and_payment_history_without_secrets(app_and_client):
    _app, client, duffel, db, _stripe = app_and_client
    duffel.offer_data = _rich_offer()
    booking_id = client.post("/api/bookings/draft", json=DRAFT_BODY).json()["booking_id"]
    assert client.post("/api/payments/intents", json={"booking_id": booking_id}).status_code == 200

    r = client.get(f"/api/bookings/{booking_id}/details")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["itinerary"]["slices"][1]["destination"] == "YYZ"
    assert body["travellers"] == [{"given_name": "Jane", "family_name": "Doe", "type": "adult"}]
    assert body["contact_email"] == "jane@example.com"
    assert len(body["payments"]) == 1
    assert body["payments"][0]["status"] == "requires_payment_method"
    assert body["payments"][0]["amount"] == "100.00"

    leaked = set(_all_keys(body)) & _DETAIL_FORBIDDEN
    assert not leaked, f"Sensitive keys leaked: {leaked}"
    assert "_secret_" not in r.text


def test_details_enforces_ownership(app_and_client):
    _app, client, _duffel, db, _stripe = app_and_client
    booking_id = _seed(db, uid="someone-else")
    assert client.get(f"/api/bookings/{booking_id}/details").status_code == 404
    assert client.get("/api/bookings/does-not-exist/details").status_code == 404


def test_details_requires_authentication(app_and_client):
    app, client, _duffel, db, _stripe = app_and_client
    booking_id = _seed(db)
    app.dependency_overrides.pop(verified_uid, None)
    assert client.get(f"/api/bookings/{booking_id}/details").status_code in (401, 403)


def test_details_handles_legacy_booking_without_itinerary(app_and_client):
    _app, client, _duffel, db, _stripe = app_and_client
    booking_id = _seed(db, flight_summary={"from_airport": "YYZ", "to_airport": "ORD"})
    body = client.get(f"/api/bookings/{booking_id}/details").json()
    assert body["itinerary"] is None
    assert body["flight_summary"]["from_airport"] == "YYZ"
    assert body["payments"] == []


# ─── Payment records ────────────────────────────────────────────────────────

def test_intent_creation_writes_payment_record_without_secret(app_and_client):
    _app, client, _duffel, db, _stripe = app_and_client
    booking_id = _seed(db, payment_intent_id=None)

    r = client.post("/api/payments/intents", json={"booking_id": booking_id})
    assert r.status_code == 200
    record = _payment(db, booking_id)
    assert record["status"] == "requires_payment_method"
    assert record["amount_minor"] == 10000
    assert record["currency"] == "USD"
    assert record["uid"] == "user-a"
    assert not any("secret" in str(v) for v in record.values())

    # Refreshing the payment page reuses the intent and the same record.
    client.post("/api/payments/intents", json={"booking_id": booking_id})
    payments = [k for k in db._store if k.startswith(f"bookings/{booking_id}/payments/")]
    assert payments == [f"bookings/{booking_id}/payments/pi_test_1"]


def test_webhooks_update_payment_record_and_replays_are_harmless(app_and_client):
    _app, client, duffel, db, _stripe = app_and_client
    booking_id = _seed(db)
    event = _event("evt_ok", "payment_intent.succeeded", {
        "id": "pi_test_1", "amount": 10000, "currency": "usd",
        "latest_charge": "ch_1", "metadata": {"flizen_booking_id": booking_id},
    })

    assert _post_webhook(client, event).json()["result"]["status"] == "confirmed"
    assert _post_webhook(client, event).json()["result"]["status"] == "duplicate"
    assert len(duffel.order_calls) == 1

    record = _payment(db, booking_id)
    assert record["status"] == "succeeded"
    assert record["succeeded_at"]

    # A late decline for the same intent must not regress the record or booking.
    late = _event("evt_late", "payment_intent.payment_failed", {
        "id": "pi_test_1", "metadata": {"flizen_booking_id": booking_id},
    })
    assert _post_webhook(client, late).json()["result"]["status"] == "no_op_terminal"
    assert _payment(db, booking_id)["status"] == "succeeded"
    assert db._store[f"bookings/{booking_id}"]["status"] == STATUS_CONFIRMED


def test_success_after_decline_is_fulfilled(app_and_client):
    _app, client, duffel, db, _stripe = app_and_client
    booking_id = _seed(db)

    declined = _event("evt_decline", "payment_intent.payment_failed", {
        "id": "pi_test_1", "metadata": {"flizen_booking_id": booking_id},
        "last_payment_error": {"code": "card_declined", "decline_code": "insufficient_funds"},
    })
    assert _post_webhook(client, declined).json()["result"]["status"] == STATUS_PAYMENT_FAILED
    assert _payment(db, booking_id)["failure_code"] == "insufficient_funds"

    retried = _event("evt_retry_ok", "payment_intent.succeeded", {
        "id": "pi_test_1", "latest_charge": "ch_2", "metadata": {"flizen_booking_id": booking_id},
    })
    assert _post_webhook(client, retried).json()["result"]["status"] == "confirmed"
    assert len(duffel.order_calls) == 1
    assert _payment(db, booking_id)["status"] == "succeeded"


def test_payment_failed_booking_can_reopen_checkout(app_and_client):
    _app, client, _duffel, db, stripe_svc = app_and_client
    booking_id = _seed(db, payment_intent_id=None, status=STATUS_PAYMENT_FAILED)
    r = client.post("/api/payments/intents", json={"booking_id": booking_id})
    assert r.status_code == 200, r.text


def test_confirmation_refreshes_itinerary_from_duffel_order(app_and_client):
    _app, client, duffel, db, _stripe = app_and_client
    booking_id = _seed(db, itinerary={"airline": "Old", "slices": []})
    duffel.order_data = {
        "id": "ord_1", "booking_reference": "ABC123",
        "owner": {"name": "Duffel Airways"},
        "slices": [{"duration": "PT2H", "segments": [
            _segment("YYZ", "ORD", "2026-12-10T08:15:00", "2026-12-10T09:45:00", "100"),
        ]}],
    }
    event = _event("evt_c1", "payment_intent.succeeded", {
        "id": "pi_test_1", "metadata": {"flizen_booking_id": booking_id},
    })
    assert _post_webhook(client, event).json()["result"]["status"] == "confirmed"
    itin = db._store[f"bookings/{booking_id}"]["itinerary"]
    assert itin["airline"] == "Duffel Airways"
    assert itin["slices"][0]["departing_at"] == "2026-12-10T08:15:00"


# ─── Refunds ────────────────────────────────────────────────────────────────

def test_failed_order_refund_is_recorded_with_metadata(app_and_client):
    _app, client, duffel, db, stripe_svc = app_and_client
    booking_id = _seed(db)
    duffel.order_error = DuffelServiceError("boom")

    event = _event("evt_f", "payment_intent.succeeded", {
        "id": "pi_test_1", "metadata": {"flizen_booking_id": booking_id},
    })
    assert _post_webhook(client, event).json()["result"]["status"] == "failed_refund_initiated"
    assert stripe_svc.refunds[0]["metadata"] == {"flizen_booking_id": booking_id}
    record = _payment(db, booking_id)
    assert record["refund_id"] == "re_pi_test_1"
    assert record["refund_status"] == "pending"


def test_full_charge_refund_marks_booking_and_record_refunded(app_and_client):
    _app, client, _duffel, db, _stripe = app_and_client
    booking_id = _seed(db, status=STATUS_FAILED_REFUNDING, refund_state="initiated")
    event = _event("evt_r", "charge.refunded", {
        "id": "ch_1", "payment_intent": "pi_test_1", "amount": 10000,
        "amount_refunded": 10000, "refunded": True,
        "metadata": {"flizen_booking_id": booking_id},
    })
    assert _post_webhook(client, event).json()["result"]["status"] == STATUS_REFUNDED
    record = _payment(db, booking_id)
    assert record["status"] == "refunded"
    assert record["amount_refunded_minor"] == 10000

    details = client.get(f"/api/bookings/{booking_id}/details").json()
    assert details["payments"][0]["amount_refunded"] == "100.00"
    assert details["refunded_at"]


def test_partial_refund_does_not_mark_booking_refunded(app_and_client):
    _app, client, _duffel, db, _stripe = app_and_client
    booking_id = _seed(db, status=STATUS_CONFIRMED)
    event = _event("evt_pr", "charge.refunded", {
        "id": "ch_1", "payment_intent": "pi_test_1", "amount": 10000,
        "amount_refunded": 2500, "refunded": False,
        "metadata": {"flizen_booking_id": booking_id},
    })
    assert _post_webhook(client, event).json()["result"]["status"] == "partially_refunded"
    assert db._store[f"bookings/{booking_id}"]["status"] == STATUS_CONFIRMED
    assert _payment(db, booking_id)["status"] == "partially_refunded"


def test_refund_event_without_metadata_is_resolved_by_payment_intent(app_and_client):
    _app, client, _duffel, db, _stripe = app_and_client
    booking_id = _seed(db, status=STATUS_FAILED_REFUNDING, refund_state="initiated")
    event = _event("evt_rf", "refund.updated", {
        "id": "re_1", "payment_intent": "pi_test_1", "status": "failed", "metadata": {},
    })
    assert _post_webhook(client, event).json()["result"]["status"] == "refund_failed"
    assert _payment(db, booking_id)["refund_status"] == "failed"
    assert db._store[f"bookings/{booking_id}"]["refund_state"] == "failed"


# ─── History list ───────────────────────────────────────────────────────────

def test_list_falls_back_when_firestore_index_is_missing(app_and_client, monkeypatch):
    _app, client, _duffel, db, _stripe = app_and_client
    _seed(db, booking_id="older", created_at="2026-01-01T00:00:00+00:00")
    _seed(db, booking_id="newer", created_at="2026-02-01T00:00:00+00:00")

    from tests import conftest

    original_stream = conftest._FakeQuery.stream

    def stream_without_index(self):
        if self._order:
            raise FailedPrecondition("The query requires an index.")
        return original_stream(self)

    monkeypatch.setattr(conftest._FakeQuery, "stream", stream_without_index)

    r = client.get("/api/bookings")
    assert r.status_code == 200
    assert [b["booking_id"] for b in r.json()["bookings"]] == ["newer", "older"]


def test_list_does_not_include_payment_subcollection_docs(app_and_client):
    _app, client, _duffel, db, _stripe = app_and_client
    booking_id = _seed(db, payment_intent_id=None)
    client.post("/api/payments/intents", json={"booking_id": booking_id})

    body = client.get("/api/bookings").json()
    assert [b["booking_id"] for b in body["bookings"]] == [booking_id]
