"""Normalise a raw Duffel offer / order payload into a stored itinerary.

The same shape is written when a draft is created (from the offer) and again
when the Duffel order is confirmed (from the order, which is authoritative).
Only display fields are kept; no passenger data, prices, or provider tokens.
"""
from typing import Any, Optional


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


def _segment(seg: dict) -> dict:
    marketing = seg.get("marketing_carrier") or {}
    operating = seg.get("operating_carrier") or {}
    flight_no = seg.get("marketing_carrier_flight_number") or ""
    cabin = None
    for pax in seg.get("passengers") or []:
        cabin = pax.get("cabin_class_marketing_name") or pax.get("cabin_class")
        if cabin:
            break
    return {
        "origin":            _code(seg.get("origin")),
        "origin_city":       _city(seg.get("origin")),
        "destination":       _code(seg.get("destination")),
        "destination_city":  _city(seg.get("destination")),
        "departing_at":      seg.get("departing_at"),
        "arriving_at":       seg.get("arriving_at"),
        "duration":          seg.get("duration"),
        "airline":           _name(marketing) or _name(operating),
        "airline_code":      (marketing.get("iata_code") or None) if isinstance(marketing, dict) else None,
        "flight_number":     f"{marketing.get('iata_code') or ''}{flight_no}" or None,
        "operating_airline": _name(operating),
        "aircraft":          _name(seg.get("aircraft")),
        "cabin":             cabin,
    }


def extract_itinerary(payload: Any) -> dict:
    """Return {"airline", "airline_logo_url", "slices": [...]} or {} on bad input."""
    if not isinstance(payload, dict):
        return {}
    try:
        slices_out = []
        for sl in payload.get("slices") or []:
            segments = [_segment(s) for s in (sl.get("segments") or []) if isinstance(s, dict)]
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
            "airline":          _name(owner) or slices_out[0]["segments"][0]["airline"],
            "airline_logo_url": owner.get("logo_symbol_url") if isinstance(owner, dict) else None,
            "slices":           slices_out,
        }
    except Exception:
        return {}
