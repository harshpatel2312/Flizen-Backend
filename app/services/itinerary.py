"""Normalise a raw Duffel offer / order payload into stored display data.

The same shapes are written when a draft is created (from the offer) and again
when the Duffel order is confirmed (from the order, which is authoritative).

- `extract_itinerary`: flights, airports, terminals, and per-passenger cabin,
  seat, and baggage allowance. Passengers are referenced by index into the
  booking's passenger list (same order as the Duffel offer); no names, dates of
  birth, or contact details are copied.
- `extract_order_details`: fare breakdown, paid extras, fare conditions,
  airline booking references, and e-ticket numbers.
"""
from typing import Any, Optional

# Bumped when the stored itinerary gains fields, so older bookings can be refreshed.
ITINERARY_VERSION = 2


def _code(place: Any) -> Optional[str]:
    if not isinstance(place, dict):
        return None
    code = (place.get("iata_code") or "").upper()
    return code or None


def _city(place: Any) -> Optional[str]:
    if not isinstance(place, dict):
        return None
    return place.get("city_name") or (place.get("city") or {}).get("name") or None


def _name(obj: Any) -> Optional[str]:
    return obj.get("name") if isinstance(obj, dict) else None


def _field(obj: Any, key: str) -> Optional[str]:
    return (obj.get(key) or None) if isinstance(obj, dict) else None


def _passenger_index(payload: dict) -> dict:
    """Duffel passenger id -> position in the booking's passenger list."""
    return {
        p.get("id"): i
        for i, p in enumerate(payload.get("passengers") or [])
        if isinstance(p, dict) and p.get("id")
    }


def _segment_passengers(seg: dict, pax_index: dict) -> list:
    out = []
    for pax in seg.get("passengers") or []:
        if not isinstance(pax, dict) or pax.get("passenger_id") not in pax_index:
            continue
        seat = pax.get("seat")
        out.append({
            "passenger_index": pax_index[pax["passenger_id"]],
            "cabin":           pax.get("cabin_class_marketing_name") or pax.get("cabin_class"),
            # Only present on orders where a seat was actually booked with the airline.
            "seat":            (seat.get("designator") if isinstance(seat, dict) else None) or None,
            "baggages": [
                {"type": b.get("type"), "quantity": int(b.get("quantity") or 0)}
                for b in (pax.get("baggages") or [])
                if isinstance(b, dict) and b.get("type")
            ],
        })
    return sorted(out, key=lambda p: p["passenger_index"])


def _segment(seg: dict, pax_index: dict) -> dict:
    marketing = seg.get("marketing_carrier") or {}
    operating = seg.get("operating_carrier") or {}
    flight_no = seg.get("marketing_carrier_flight_number") or ""
    passengers = _segment_passengers(seg, pax_index)
    cabin = next((p["cabin"] for p in passengers if p["cabin"]), None)
    if not cabin:
        for pax in seg.get("passengers") or []:
            cabin = pax.get("cabin_class_marketing_name") or pax.get("cabin_class")
            if cabin:
                break
    origin, destination = seg.get("origin"), seg.get("destination")
    return {
        "origin":               _code(origin),
        "origin_city":          _city(origin),
        "origin_name":          _name(origin),
        "origin_terminal":      seg.get("origin_terminal") or None,
        "origin_time_zone":     _field(origin, "time_zone"),
        "destination":          _code(destination),
        "destination_city":     _city(destination),
        "destination_name":     _name(destination),
        "destination_terminal": seg.get("destination_terminal") or None,
        "destination_time_zone": _field(destination, "time_zone"),
        "departing_at":         seg.get("departing_at"),
        "arriving_at":          seg.get("arriving_at"),
        "duration":             seg.get("duration"),
        "airline":              _name(marketing) or _name(operating),
        "airline_code":         (marketing.get("iata_code") or None) if isinstance(marketing, dict) else None,
        "airline_logo_url":     _field(marketing, "logo_symbol_url") or _field(operating, "logo_symbol_url"),
        "flight_number":        f"{marketing.get('iata_code') or ''}{flight_no}" or None,
        "operating_airline":    _name(operating),
        "aircraft":             _name(seg.get("aircraft")),
        "cabin":                cabin,
        "travellers":           passengers,
    }


def airline_logo_url(payload: Any) -> Optional[str]:
    """Logo of the selling airline, falling back to the first segment's carriers."""
    if not isinstance(payload, dict):
        return None
    candidates = [payload.get("owner")]
    try:
        first_seg = (payload.get("slices") or [{}])[0].get("segments")[0]
        candidates += [first_seg.get("marketing_carrier"), first_seg.get("operating_carrier")]
    except (AttributeError, IndexError, KeyError, TypeError):
        pass
    for carrier in candidates:
        if isinstance(carrier, dict) and carrier.get("logo_symbol_url"):
            return carrier["logo_symbol_url"]
    return None


def extract_itinerary(payload: Any) -> dict:
    """Return {"version", "airline", "airline_logo_url", "slices": [...]} or {} on bad input."""
    if not isinstance(payload, dict):
        return {}
    try:
        pax_index = _passenger_index(payload)
        slices_out = []
        for sl in payload.get("slices") or []:
            segments = [_segment(s, pax_index) for s in (sl.get("segments") or []) if isinstance(s, dict)]
            if not segments:
                continue
            first, last = segments[0], segments[-1]
            slices_out.append({
                "origin":           first["origin"],
                "origin_city":      first["origin_city"],
                "destination":      last["destination"],
                "destination_city": last["destination_city"],
                "departing_at":     first["departing_at"],
                "arriving_at":      last["arriving_at"],
                "duration":         sl.get("duration"),
                "stops":            len(segments) - 1,
                "fare_brand":       sl.get("fare_brand_name"),
                "segments":         segments,
            })
        if not slices_out:
            return {}
        owner = payload.get("owner") or {}
        return {
            "version":          ITINERARY_VERSION,
            "airline":          _name(owner) or slices_out[0]["segments"][0]["airline"],
            "airline_logo_url": airline_logo_url(payload),
            "slices":           slices_out,
        }
    except Exception:
        return {}


def _condition(raw: Any) -> Optional[dict]:
    if not isinstance(raw, dict) or raw.get("allowed") is None:
        return None
    return {
        "allowed":          bool(raw.get("allowed")),
        "penalty_amount":   raw.get("penalty_amount"),
        "penalty_currency": raw.get("penalty_currency"),
    }


def extract_order_details(payload: Any) -> dict:
    """Fare breakdown, extras, conditions, airline references, and tickets; {} on bad input.

    Offers yield the fare and conditions; references, tickets, and paid services
    only exist on orders.
    """
    if not isinstance(payload, dict):
        return {}
    try:
        currency = payload.get("total_currency")
        # A breakdown is only meaningful when every part is in the charged currency.
        same_currency = all(
            payload.get(k) in (None, currency) for k in ("base_currency", "tax_currency")
        )
        pax_index = _passenger_index(payload)

        services = [
            {
                "type":     s.get("type"),
                "quantity": int(s.get("quantity") or 1),
                "amount":   s.get("total_amount"),
                "currency": s.get("total_currency"),
            }
            for s in payload.get("services") or []
            if isinstance(s, dict) and s.get("type") and s.get("total_amount")
        ]

        references = [
            {
                "reference":                 ref.get("booking_reference"),
                "airline":                   _name(ref.get("carrier")),
                "airline_code":              _field(ref.get("carrier"), "iata_code"),
                "airline_logo_url":          _field(ref.get("carrier"), "logo_symbol_url"),
                "conditions_of_carriage_url": _field(ref.get("carrier"), "conditions_of_carriage_url"),
            }
            for ref in payload.get("booking_references") or []
            if isinstance(ref, dict) and ref.get("booking_reference")
        ]

        tickets = [
            {"passenger_index": pax_index[pid], "number": doc.get("unique_identifier")}
            for doc in payload.get("documents") or []
            if isinstance(doc, dict) and doc.get("type") == "electronic_ticket" and doc.get("unique_identifier")
            for pid in (doc.get("passenger_ids") or [])
            if pid in pax_index
        ]

        conditions = payload.get("conditions") or {}
        details = {
            "base_amount":   payload.get("base_amount") if same_currency else None,
            "tax_amount":    payload.get("tax_amount") if same_currency else None,
            "total_amount":  payload.get("total_amount"),
            "currency":      currency,
            "services":      services,
            "conditions": {
                "refund_before_departure": _condition(conditions.get("refund_before_departure")),
                "change_before_departure": _condition(conditions.get("change_before_departure")),
            },
            "airline_references": references,
            "tickets":       sorted(tickets, key=lambda t: t["passenger_index"]),
        }
        return details
    except Exception:
        return {}
