"""Pytest fixtures.

We stub firebase_admin BEFORE the FastAPI app module is imported anywhere,
because app.main calls init_firebase() at import time.
"""
import os
import sys
from unittest.mock import MagicMock

# --- Environment stubs (before any app imports) -----------------------------
os.environ.setdefault("DUFFEL_ACCESS_TOKEN", "test-token")
os.environ.setdefault("ENVIRONMENT", "development")

# --- Firebase stubs (before any app imports) --------------------------------
import firebase_admin  # noqa: E402
from firebase_admin import auth as _fb_auth, firestore as _fb_firestore  # noqa: E402

firebase_admin.initialize_app = MagicMock()
firebase_admin.get_app = MagicMock(return_value=MagicMock())
_fb_firestore.client = MagicMock(return_value=MagicMock())

# Also ensure app.core.firebase.init_firebase is a no-op.
sys.modules.setdefault  # touch to appease linters

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class _FakeDoc:
    def __init__(self, data):
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return self._data


class _FakeDocRef:
    def __init__(self, store, path):
        self._store = store
        self._path = path

    def set(self, data, merge=False):
        if merge and self._path in self._store:
            self._store[self._path] = {**self._store[self._path], **data}
        else:
            self._store[self._path] = dict(data)

    def create(self, data):
        # Firestore's create() fails if the doc already exists — the semantic
        # our fulfillment code relies on for event-id idempotency.
        if self._path in self._store:
            raise Exception("AlreadyExists")
        self._store[self._path] = dict(data)

    def get(self):
        data = self._store.get(self._path)
        return _FakeDoc(dict(data) if data is not None else None)


class _FakeCollection:
    def __init__(self, store, name):
        self._store = store
        self._name = name

    def document(self, doc_id):
        return _FakeDocRef(self._store, f"{self._name}/{doc_id}")

    def where(self, field, op, value):
        return _FakeQuery(self._store, self._name, [(field, op, value)])


class _FakeQuery:
    def __init__(self, store, name, filters, order=None):
        self._store = store
        self._name = name
        self._filters = list(filters)
        self._order = order  # (field, direction)

    def where(self, field, op, value):
        return _FakeQuery(self._store, self._name, self._filters + [(field, op, value)], self._order)

    def order_by(self, field, direction="ASCENDING"):
        return _FakeQuery(self._store, self._name, self._filters, (field, direction))

    def stream(self):
        prefix = f"{self._name}/"
        rows = []
        for path, doc in self._store.items():
            if not path.startswith(prefix):
                continue
            ok = True
            for field, op, val in self._filters:
                actual = doc.get(field)
                if op == "==" and actual != val:
                    ok = False
                    break
            if ok:
                rows.append(doc)
        if self._order:
            field, direction = self._order
            reverse = str(direction).upper().startswith("DESC")
            rows.sort(key=lambda d: d.get(field) or "", reverse=reverse)
        return [_FakeDoc(dict(r)) for r in rows]


class FakeDB:
    def __init__(self):
        self._store = {}

    def collection(self, name):
        return _FakeCollection(self._store, name)


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def app_and_client(fake_db, monkeypatch):
    # Patch init/get_db in app.core.firebase before importing app.main.
    from app.core import firebase as fb_core
    monkeypatch.setattr(fb_core, "init_firebase", lambda: None)
    monkeypatch.setattr(fb_core, "get_db", lambda: fake_db)

    from app.main import app
    from app.core.auth import verified_uid
    from app.routes.bookings import (
        get_bookings_db,
        get_duffel_service,
    )
    from app.routes.payments import get_stripe_service

    # Default authenticated identity.
    app.dependency_overrides[verified_uid] = lambda: "user-a"
    app.dependency_overrides[get_bookings_db] = lambda: fake_db

    class _FakeDuffel:
        # Default: a valid one-way, single-adult offer priced 100.00 USD.
        offer_data = {
            "id": "off_valid",
            "total_amount": "100.00",
            "total_currency": "USD",
            "expires_at": "2026-08-27T00:00:00Z",
            "passengers": [{"id": "pas_1", "type": "adult"}],
            "slices": [{"origin": "YYZ", "destination": "JFK"}],
        }
        offer_error = None
        # Order-creation fakes:
        order_data = {"id": "ord_test_1", "booking_reference": "PNR123"}
        order_error = None

        def __init__(self):
            self.order_calls = []

        async def get_offer(self, offer_id: str) -> dict:
            if self.offer_error is not None:
                raise self.offer_error
            return dict(self.offer_data)

        async def create_order(self, *, offer_id, amount, currency, passengers, idempotency_key):
            self.order_calls.append({
                "offer_id": offer_id,
                "amount": amount,
                "currency": currency,
                "passengers": [dict(p) for p in passengers],
                "idempotency_key": idempotency_key,
            })
            if self.order_error is not None:
                raise self.order_error
            return dict(self.order_data)

    fake_duffel = _FakeDuffel()
    app.dependency_overrides[get_duffel_service] = lambda: fake_duffel

    class _FakeStripeIntent:
        def __init__(self, id, client_secret, status, amount, currency, metadata):
            self.id = id
            self.client_secret = client_secret
            self.status = status
            self.amount = amount
            self.currency = currency
            self.metadata = metadata

    class _FakeStripe:
        def __init__(self):
            # intents keyed by intent_id
            self.intents: dict = {}
            # log of every call: ("create"|"retrieve"|"cancel"|"verify_webhook", args)
            self.calls: list = []
            self._auto_id = 0
            # Set by tests to force retrieve() to return a specific intent.
            self.force_retrieve = None
            self.raise_on_create = None
            self.raise_on_refund = None
            self.raise_on_verify = None
            self.refunds: list = []
            self.verify_return = None

        def _new_id(self):
            self._auto_id += 1
            return f"pi_test_{self._auto_id}"

        def create_payment_intent(self, *, amount_minor, currency, metadata, idempotency_key):
            self.calls.append((
                "create",
                {
                    "amount_minor": amount_minor,
                    "currency": currency,
                    "metadata": dict(metadata),
                    "idempotency_key": idempotency_key,
                },
            ))
            if self.raise_on_create is not None:
                raise self.raise_on_create
            # Simulate Stripe idempotency: same key -> same intent.
            for pi in self.intents.values():
                if pi.metadata.get("__idem_key__") == idempotency_key:
                    return pi
            pid = self._new_id()
            meta_stored = dict(metadata)
            meta_stored["__idem_key__"] = idempotency_key
            intent = _FakeStripeIntent(
                id=pid,
                client_secret=f"{pid}_secret_abc",
                status="requires_payment_method",
                amount=amount_minor,
                currency=currency.lower(),
                metadata=meta_stored,
            )
            self.intents[pid] = intent
            return intent

        def retrieve_payment_intent(self, intent_id):
            self.calls.append(("retrieve", {"intent_id": intent_id}))
            if self.force_retrieve is not None:
                return self.force_retrieve
            return self.intents.get(intent_id)

        def cancel_payment_intent(self, intent_id):
            self.calls.append(("cancel", {"intent_id": intent_id}))
            pi = self.intents.get(intent_id)
            if pi:
                pi.status = "canceled"
            return pi

        def create_refund(self, *, payment_intent_id, idempotency_key, reason="requested_by_customer"):
            self.refunds.append({
                "payment_intent_id": payment_intent_id,
                "idempotency_key": idempotency_key,
                "reason": reason,
            })
            if self.raise_on_refund is not None:
                raise self.raise_on_refund
            return {"id": f"re_{payment_intent_id}", "status": "pending"}

        def verify_webhook(self, payload, sig_header):
            self.calls.append(("verify_webhook", {"sig_header": sig_header}))
            if self.raise_on_verify is not None:
                raise self.raise_on_verify
            if self.verify_return is not None:
                return self.verify_return
            import json
            return json.loads(payload.decode("utf-8"))

    fake_stripe = _FakeStripe()
    app.dependency_overrides[get_stripe_service] = lambda: fake_stripe

    client = TestClient(app)
    try:
        yield app, client, fake_duffel, fake_db, fake_stripe
    finally:
        app.dependency_overrides.clear()
