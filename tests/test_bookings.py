"""Focused tests for the booking-draft foundation.

Covers:
- authentication is required
- ownership is enforced on GET
- offer revalidation (missing/expired -> 400; provider error -> 502)
- frontend-controlled pricing is rejected / ignored
- round-trip via two offers is rejected
- passenger-count mismatch is rejected
"""
from app.core.auth import verified_uid
from app.services.duffel_service import (
    DuffelOfferNotFoundError,
    DuffelServiceError,
)


VALID_BODY = {
    "offer_id": "off_valid",
    "passengers": [
        {
            "type": "adult",
            "given_name": "Jane",
            "family_name": "Doe",
            "born_on": "1990-01-01",
            "gender": "female",
        }
    ],
    "contact_email": "jane@example.com",
    "contact_phone": "+1 555 123 4567",
    "seat_preferences": [
        {"segment_key": "outbound-0", "passenger_index": 0, "seat_id_local": "12A"}
    ],
}


# --- 1. Authentication -------------------------------------------------------

def test_post_draft_requires_authentication(app_and_client):
    app, client, _duffel, _db, _stripe = app_and_client
    # Remove the auth override to simulate an anonymous request.
    app.dependency_overrides.pop(verified_uid, None)

    r = client.post("/api/bookings/draft", json=VALID_BODY)
    assert r.status_code in (401, 403)


def test_get_booking_requires_authentication(app_and_client):
    app, client, _duffel, _db, _stripe = app_and_client
    app.dependency_overrides.pop(verified_uid, None)

    r = client.get("/api/bookings/some-id")
    assert r.status_code in (401, 403)


# --- 2. Happy path uses Duffel-derived amount/currency ----------------------

def test_draft_uses_duffel_amount_and_currency(app_and_client):
    _app, client, duffel, db, _stripe = app_and_client
    duffel.offer_data = {
        **duffel.offer_data,
        "total_amount": "742.55",
        "total_currency": "gbp",
    }

    r = client.post("/api/bookings/draft", json=VALID_BODY)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["amount"] == "742.55"
    assert data["currency"] == "GBP"
    assert data["status"] == "draft_awaiting_payment"
    assert data["slice_count"] == 1

    # And Firestore stored the same authoritative values.
    stored = db._store[f"bookings/{data['booking_id']}"]
    assert stored["amount"] == "742.55"
    assert stored["currency"] == "GBP"
    assert stored["uid"] == "user-a"
    # Seat picks stored explicitly as unconfirmed local preferences.
    assert stored["seat_preferences"][0]["confirmed_with_duffel"] is False


# --- 3. Reject frontend-controlled pricing ----------------------------------

def test_draft_rejects_client_supplied_amount(app_and_client):
    _app, client, _duffel, _db, _stripe = app_and_client
    body = {**VALID_BODY, "amount": "1.00", "currency": "USD", "price": "1.00"}

    r = client.post("/api/bookings/draft", json=body)
    # extra='forbid' -> 422 unprocessable entity from pydantic.
    assert r.status_code == 422


def test_draft_ignores_matching_but_unrequested_price_field(app_and_client):
    # Even a "safe" duplicate price field is rejected — the schema is strict.
    _app, client, duffel, _db, _stripe = app_and_client
    duffel.offer_data = {**duffel.offer_data, "total_amount": "50.00"}
    body = {**VALID_BODY, "total_amount": "50.00"}

    r = client.post("/api/bookings/draft", json=body)
    assert r.status_code == 422


# --- 4. Offer revalidation --------------------------------------------------

def test_draft_rejects_expired_or_missing_offer(app_and_client):
    _app, client, duffel, _db, _stripe = app_and_client
    duffel.offer_error = DuffelOfferNotFoundError("gone")

    r = client.post("/api/bookings/draft", json=VALID_BODY)
    assert r.status_code == 400
    assert "no longer available" in r.json()["detail"].lower()


def test_draft_returns_502_on_provider_error(app_and_client):
    _app, client, duffel, _db, _stripe = app_and_client
    duffel.offer_error = DuffelServiceError("boom")

    r = client.post("/api/bookings/draft", json=VALID_BODY)
    assert r.status_code == 502


def test_draft_rejects_passenger_count_mismatch(app_and_client):
    _app, client, duffel, _db, _stripe = app_and_client
    # Offer expects 2 passengers, request has 1.
    duffel.offer_data = {
        **duffel.offer_data,
        "passengers": [{"type": "adult"}, {"type": "child"}],
    }

    r = client.post("/api/bookings/draft", json=VALID_BODY)
    assert r.status_code == 400
    assert "passenger count" in r.json()["detail"].lower()


# --- 5. Round-trip via two offers is refused --------------------------------

def test_draft_rejects_two_offer_round_trip(app_and_client):
    _app, client, _duffel, _db, _stripe = app_and_client
    body = {**VALID_BODY, "return_offer_id": "off_return"}

    r = client.post("/api/bookings/draft", json=body)
    assert r.status_code == 400
    assert "round-trip" in r.json()["detail"].lower()


# --- 6. Ownership on GET ----------------------------------------------------

def test_get_booking_enforces_ownership(app_and_client):
    app, client, _duffel, _db, _stripe = app_and_client

    # user-a creates a draft
    r = client.post("/api/bookings/draft", json=VALID_BODY)
    assert r.status_code == 201
    booking_id = r.json()["booking_id"]

    # user-a can read their own
    r = client.get(f"/api/bookings/{booking_id}")
    assert r.status_code == 200
    assert r.json()["booking_id"] == booking_id

    # Switch identity to user-b and try to read user-a's booking
    app.dependency_overrides[verified_uid] = lambda: "user-b"
    r = client.get(f"/api/bookings/{booking_id}")
    # Do not leak existence — should look like a normal 404.
    assert r.status_code == 404


def test_get_booking_returns_404_for_unknown_id(app_and_client):
    _app, client, _duffel, _db, _stripe = app_and_client
    r = client.get("/api/bookings/does-not-exist")
    assert r.status_code == 404
