"""Focused tests for GET /api/bookings (list) and the safe summary shape."""
from datetime import datetime, timezone, timedelta

from app.core.auth import verified_uid


def _seed(db, *, booking_id, uid="user-a", status="draft_awaiting_payment",
          created_offset_seconds=0, **overrides):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=created_offset_seconds)
    doc = {
        "booking_id": booking_id,
        "uid": uid,
        "status": status,
        "offer_id": "off_x",
        "amount": "100.00",
        "currency": "USD",
        "slice_count": 1,
        "passenger_count": 1,
        "passengers": [{"given_name": "Jane", "family_name": "Doe"}],
        "contact": {"email": "jane@example.com", "phone": "+15551234567"},
        "flight_summary": {
            "from_airport": "YYZ",
            "to_airport": "JFK",
            "airline": "Delta",
            "departure_date": "2026-05-20",
        },
        "payment_intent_id": "pi_secret_should_not_leak",
        "stripe_charge_id": "ch_secret_should_not_leak",
        "payment_amount_minor": 10000,
        "payment_currency": "USD",
        "created_at": base.isoformat(),
        "updated_at": base.isoformat(),
    }
    doc.update(overrides)
    db.collection("bookings").document(booking_id).set(doc)


# --- Auth ------------------------------------------------------------------

def test_list_requires_authentication(app_and_client):
    app, client, _duffel, _db, _stripe = app_and_client
    app.dependency_overrides.pop(verified_uid, None)
    r = client.get("/api/bookings")
    assert r.status_code in (401, 403)


# --- Isolation between users ----------------------------------------------

def test_list_only_returns_own_bookings(app_and_client):
    _app, client, _duffel, db, _stripe = app_and_client
    _seed(db, booking_id="b1", uid="user-a")
    _seed(db, booking_id="b2", uid="user-a")
    _seed(db, booking_id="b3", uid="someone-else")

    r = client.get("/api/bookings")
    assert r.status_code == 200
    body = r.json()
    ids = {b["booking_id"] for b in body["bookings"]}
    assert ids == {"b1", "b2"}
    assert body["count"] == 2


# --- Ordering (newest first) ----------------------------------------------

def test_list_orders_newest_first(app_and_client):
    _app, client, _duffel, db, _stripe = app_and_client
    _seed(db, booking_id="old",   created_offset_seconds=0)
    _seed(db, booking_id="mid",   created_offset_seconds=60)
    _seed(db, booking_id="fresh", created_offset_seconds=120)

    r = client.get("/api/bookings")
    assert r.status_code == 200
    order = [b["booking_id"] for b in r.json()["bookings"]]
    assert order == ["fresh", "mid", "old"]


# --- Safe projection: no secrets, no PII ----------------------------------

_ALLOWED_KEYS = {
    "booking_id", "status", "amount", "currency", "slice_count",
    "passenger_count", "payment_status", "refund_state", "failure_reason",
    "duffel_booking_reference", "flight_summary", "created_at",
    "updated_at", "confirmed_at", "offer_expires_at",
}

_FORBIDDEN_KEYS = {
    "payment_intent_id", "stripe_charge_id", "payment_amount_minor",
    "payment_currency", "passengers", "contact", "seat_preferences",
    "offer_id", "duffel_order_id", "uid",
}


def test_list_projects_only_safe_fields(app_and_client):
    _app, client, _duffel, db, _stripe = app_and_client
    _seed(db, booking_id="b1")

    r = client.get("/api/bookings")
    assert r.status_code == 200
    (item,) = r.json()["bookings"]
    keys = set(item.keys())
    assert keys.issubset(_ALLOWED_KEYS), f"Unexpected keys leaked: {keys - _ALLOWED_KEYS}"
    assert not (keys & _FORBIDDEN_KEYS), f"Sensitive keys leaked: {keys & _FORBIDDEN_KEYS}"


# --- Duffel reference is hidden until Duffel confirms ---------------------

def test_duffel_reference_hidden_until_confirmed(app_and_client):
    _app, client, _duffel, db, _stripe = app_and_client
    _seed(db, booking_id="pending", status="fulfilling",
          duffel_booking_reference="PNR-XYZ")
    _seed(db, booking_id="done", status="confirmed",
          duffel_booking_reference="PNR-OK", confirmed_at="2026-01-02T00:00:00+00:00")

    r = client.get("/api/bookings")
    by_id = {b["booking_id"]: b for b in r.json()["bookings"]}
    assert by_id["pending"]["duffel_booking_reference"] is None
    assert by_id["done"]["duffel_booking_reference"] == "PNR-OK"


# --- Detail endpoint returns the same safe shape --------------------------

def test_get_single_booking_returns_summary_shape(app_and_client):
    _app, client, _duffel, db, _stripe = app_and_client
    _seed(db, booking_id="only")

    r = client.get("/api/bookings/only")
    assert r.status_code == 200
    keys = set(r.json().keys())
    assert keys.issubset(_ALLOWED_KEYS)
    assert not (keys & _FORBIDDEN_KEYS)
