"""Focused tests for POST /api/payments/intents.

Covers:
- authentication required
- ownership enforced (404, not 403, to avoid leaking existence)
- currency conversion (USD 2-decimal, JPY zero-decimal, KWD refused)
- new PaymentIntent creation with sanitized metadata + idempotency key
- reuse of an eligible existing PaymentIntent on refresh
- already-paid booking → 409
- price change since draft → 409
- only client_secret / booking_id / display_amount / currency in response
"""
from datetime import datetime, timezone
import uuid

from app.core.auth import verified_uid
from app.services.stripe_service import to_stripe_minor_units, InvalidAmountError, UnsupportedCurrencyError


def _make_booking(db, *, uid="user-a", amount="100.00", currency="USD",
                  offer_id="off_valid", status="draft_awaiting_payment",
                  payment_intent_id=None, payment_status=None):
    booking_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "booking_id": booking_id,
        "uid": uid,
        "status": status,
        "offer_id": offer_id,
        "amount": amount,
        "currency": currency,
        "slice_count": 1,
        "passenger_count": 1,
        "passengers": [{"given_name": "Jane", "family_name": "Doe"}],
        "contact": {"email": "jane@example.com"},
        "seat_preferences": [],
        "created_at": now,
        "updated_at": now,
    }
    if payment_intent_id:
        doc["payment_intent_id"] = payment_intent_id
    if payment_status:
        doc["payment_status"] = payment_status
    db.collection("bookings").document(booking_id).set(doc)
    return booking_id


# --- Currency conversion (pure unit) ---------------------------------------

def test_minor_units_usd():
    assert to_stripe_minor_units("742.55", "USD") == 74255
    assert to_stripe_minor_units("1.00", "usd") == 100


def test_minor_units_zero_decimal_jpy():
    assert to_stripe_minor_units("1500", "JPY") == 1500


def test_minor_units_rejects_over_precision():
    import pytest
    with pytest.raises(InvalidAmountError):
        to_stripe_minor_units("10.005", "USD")


def test_minor_units_rejects_jpy_with_decimals():
    import pytest
    with pytest.raises(InvalidAmountError):
        to_stripe_minor_units("1500.50", "JPY")


def test_minor_units_rejects_unsupported_three_decimal():
    import pytest
    with pytest.raises(UnsupportedCurrencyError):
        to_stripe_minor_units("10.500", "KWD")


# --- Auth ------------------------------------------------------------------

def test_intents_requires_authentication(app_and_client):
    app, client, _duffel, _db, _stripe = app_and_client
    app.dependency_overrides.pop(verified_uid, None)

    r = client.post("/api/payments/intents", json={"booking_id": "x"})
    assert r.status_code in (401, 403)


# --- Ownership -------------------------------------------------------------

def test_intents_returns_404_for_other_users_booking(app_and_client):
    app, client, _duffel, db, _stripe = app_and_client
    booking_id = _make_booking(db, uid="someone-else")

    r = client.post("/api/payments/intents", json={"booking_id": booking_id})
    assert r.status_code == 404


def test_intents_returns_404_for_unknown_booking(app_and_client):
    _app, client, _duffel, _db, _stripe = app_and_client
    r = client.post("/api/payments/intents", json={"booking_id": "does-not-exist"})
    assert r.status_code == 404


# --- Booking status --------------------------------------------------------

def test_intents_rejects_non_payable_status(app_and_client):
    _app, client, _duffel, db, _stripe = app_and_client
    booking_id = _make_booking(db, status="canceled")

    r = client.post("/api/payments/intents", json={"booking_id": booking_id})
    assert r.status_code == 409


# --- Reject client-controlled fields (extra=forbid) ------------------------

def test_intents_rejects_client_supplied_amount(app_and_client):
    _app, client, _duffel, db, _stripe = app_and_client
    booking_id = _make_booking(db)

    r = client.post(
        "/api/payments/intents",
        json={"booking_id": booking_id, "amount": "1.00", "currency": "USD"},
    )
    assert r.status_code == 422


# --- Happy path: new PaymentIntent -----------------------------------------

def test_intents_creates_new_and_returns_only_safe_fields(app_and_client):
    _app, client, duffel, db, stripe_svc = app_and_client
    booking_id = _make_booking(db, amount="742.55", currency="USD")
    duffel.offer_data = {**duffel.offer_data, "total_amount": "742.55", "total_currency": "USD"}

    r = client.post("/api/payments/intents", json={"booking_id": booking_id})
    assert r.status_code == 200, r.text
    body = r.json()

    # Response shape — no PII, no Stripe internals.
    assert set(body.keys()) == {"booking_id", "client_secret", "display_amount", "currency", "status"}
    assert body["booking_id"] == booking_id
    assert body["display_amount"] == "742.55"
    assert body["currency"] == "USD"
    assert body["client_secret"].startswith("pi_test_") and "_secret_" in body["client_secret"]

    # Stripe was called with correct minor units and safe metadata only.
    create_calls = [c for c in stripe_svc.calls if c[0] == "create"]
    assert len(create_calls) == 1
    args = create_calls[0][1]
    assert args["amount_minor"] == 74255
    assert args["currency"] == "USD"
    assert args["idempotency_key"] == f"booking:{booking_id}:74255:USD"

    md = args["metadata"]
    # Metadata contains only sanitized identifiers.
    assert md == {
        "flizen_booking_id": booking_id,
        "flizen_uid": "user-a",
        "flizen_offer_id": "off_valid",
    }
    # And crucially not any PII fields.
    for key in md:
        assert "passenger" not in key.lower()
        assert "passport" not in key.lower()
        assert "email"    not in key.lower()
        assert "phone"    not in key.lower()

    # Booking doc was updated with the intent ID + payment status (never client_secret).
    stored = db._store[f"bookings/{booking_id}"]
    assert stored["payment_intent_id"].startswith("pi_test_")
    assert stored["payment_status"] == "requires_payment_method"
    assert stored["payment_amount_minor"] == 74255
    assert stored["payment_currency"] == "USD"
    assert "client_secret" not in stored


# --- Reuse existing intent (page refresh) ----------------------------------

def test_intents_reuses_existing_eligible_intent(app_and_client):
    _app, client, duffel, db, stripe_svc = app_and_client
    booking_id = _make_booking(db, amount="742.55", currency="USD")
    duffel.offer_data = {**duffel.offer_data, "total_amount": "742.55", "total_currency": "USD"}

    r1 = client.post("/api/payments/intents", json={"booking_id": booking_id})
    assert r1.status_code == 200
    first_secret = r1.json()["client_secret"]
    first_id = db._store[f"bookings/{booking_id}"]["payment_intent_id"]

    # Simulate a refresh — same booking, same price.
    stripe_svc.calls.clear()
    r2 = client.post("/api/payments/intents", json={"booking_id": booking_id})
    assert r2.status_code == 200
    assert r2.json()["client_secret"] == first_secret

    # No new PaymentIntent was created; only a retrieve.
    kinds = [c[0] for c in stripe_svc.calls]
    assert "retrieve" in kinds
    assert "create" not in kinds
    assert db._store[f"bookings/{booking_id}"]["payment_intent_id"] == first_id


# --- Already-paid booking blocks new intent creation -----------------------

def test_intents_rejects_when_already_succeeded(app_and_client):
    _app, client, duffel, db, stripe_svc = app_and_client
    booking_id = _make_booking(db, amount="10.00", currency="USD")
    duffel.offer_data = {**duffel.offer_data, "total_amount": "10.00", "total_currency": "USD"}

    # First call creates an intent; then force retrieve() to return succeeded.
    r1 = client.post("/api/payments/intents", json={"booking_id": booking_id})
    assert r1.status_code == 200

    existing = list(stripe_svc.intents.values())[0]
    existing.status = "succeeded"
    stripe_svc.force_retrieve = existing

    r2 = client.post("/api/payments/intents", json={"booking_id": booking_id})
    assert r2.status_code == 409
    assert "already been paid" in r2.json()["detail"].lower()


# --- Price changed since draft --------------------------------------------

def test_intents_rejects_when_offer_price_changed(app_and_client):
    _app, client, duffel, db, _stripe = app_and_client
    booking_id = _make_booking(db, amount="100.00", currency="USD")
    # Duffel now returns a different price than what's stored on the draft.
    duffel.offer_data = {**duffel.offer_data, "total_amount": "150.00"}

    r = client.post("/api/payments/intents", json={"booking_id": booking_id})
    assert r.status_code == 409
    assert "price" in r.json()["detail"].lower()


# --- Reject KWD (three-decimal) at the currency layer ---------------------

def test_intents_rejects_unsupported_currency(app_and_client):
    _app, client, _duffel, db, _stripe = app_and_client
    booking_id = _make_booking(db, amount="10.500", currency="KWD")

    r = client.post("/api/payments/intents", json={"booking_id": booking_id})
    assert r.status_code == 400
    assert "kwd" in r.json()["detail"].lower()
