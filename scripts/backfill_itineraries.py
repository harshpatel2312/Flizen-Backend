"""Backfill the stored itinerary, fare details, and airline logo on older bookings.

Older bookings may lack the itinerary, fare details, or logo My Trips shows. This
re-reads the booked flights from Duffel (the order when one exists, otherwise
the offer) and writes `itinerary` (also refreshing ones stored before the
current fields), `order_details` (fare breakdown, conditions, airline references,
tickets), and a missing `flight_summary.airline_logo_url`.
When Duffel no longer returns the order/offer, the logo is still filled in by
matching the stored airline name against Duffel's airline directory.
Nothing else on the booking changes.

Dry run (default):  python -m scripts.backfill_itineraries
Write changes:      python -m scripts.backfill_itineraries --apply
"""
import argparse
import asyncio
from typing import Awaitable, Callable, Optional

import httpx

from app.services.itinerary import (
    ITINERARY_VERSION,
    airline_logo_url,
    extract_itinerary,
    extract_order_details,
)

Fetch = Callable[[str, str], Awaitable[Optional[dict]]]


def _itinerary_outdated(doc: dict) -> bool:
    """Missing, or stored before the current itinerary fields (terminals, seats, baggage)."""
    itinerary = doc.get("itinerary")
    if not (isinstance(itinerary, dict) and itinerary.get("slices")):
        return True
    return int(itinerary.get("version") or 1) < ITINERARY_VERSION


def _with_logo(summary: dict, logo: Optional[str]) -> Optional[dict]:
    """Updated flight_summary when it lacks a logo and one is known, else None."""
    if not isinstance(summary, dict) or summary.get("airline_logo_url") or not logo:
        return None
    return {**summary, "airline_logo_url": logo}


async def backfill(db, fetch: Fetch, *, apply: bool, logos_by_name: Optional[dict] = None) -> dict:
    """Return counts {"updated", "logo_only", "unavailable", "skipped"}; writes only when `apply`.

    `logos_by_name` maps lower-cased airline names to logo URLs (Duffel's directory).
    """
    logos_by_name = logos_by_name or {}
    counts = {"updated": 0, "logo_only": 0, "unavailable": 0, "skipped": 0}
    for snap in db.collection("bookings").stream():
        doc = snap.to_dict() or {}
        booking_id = doc.get("booking_id")
        summary = doc.get("flight_summary")
        needs_itinerary = _itinerary_outdated(doc)
        needs_details = not doc.get("order_details")
        needs_logo = isinstance(summary, dict) and not summary.get("airline_logo_url")
        if not booking_id or not (needs_itinerary or needs_details or needs_logo):
            counts["skipped"] += 1
            continue

        update: dict = {}
        payload = None
        if needs_itinerary or needs_details:
            for kind, key in (("order", "duffel_order_id"), ("offer", "offer_id")):
                if doc.get(key):
                    payload = await fetch(kind, doc[key])
                    if payload:
                        break
        if payload and needs_itinerary:
            itinerary = extract_itinerary(payload)
            if itinerary:
                update["itinerary"] = itinerary
        if payload and needs_details:
            details = extract_order_details(payload)
            if details:
                update["order_details"] = details

        name = (summary or {}).get("airline") if isinstance(summary, dict) else None
        logo = (airline_logo_url(payload) if payload else None) or logos_by_name.get((name or "").strip().lower())
        new_summary = _with_logo(summary, logo)
        if new_summary:
            update["flight_summary"] = new_summary

        if not update:
            counts["unavailable"] += 1
            print(f"- {booking_id}: nothing available from Duffel, left unchanged")
            continue

        mark = "+" if apply else "~"
        if "itinerary" in update or "order_details" in update:
            counts["updated"] += 1
            first = (update.get("itinerary") or doc.get("itinerary") or {}).get("slices", [{}])[0]
            print(f"{mark} {booking_id}: {first.get('origin')} -> {first.get('destination')} {first.get('departing_at')}")
        else:
            counts["logo_only"] += 1
            print(f"{mark} {booking_id}: logo for {name}")
        if apply:
            db.collection("bookings").document(booking_id).set(update, merge=True)
    return counts


def _duffel_fetcher(client: httpx.AsyncClient, svc) -> Fetch:
    async def fetch(kind: str, resource_id: str) -> Optional[dict]:
        r = await client.get(f"{svc.base_url}/air/{kind}s/{resource_id}", headers=svc.headers)
        return (r.json().get("data") or None) if r.status_code == 200 else None

    return fetch


async def _airline_logos(client: httpx.AsyncClient, svc) -> dict:
    """Lower-cased airline name -> logo URL from Duffel's airline directory."""
    logos, after = {}, None
    while True:
        params = {"limit": 200, **({"after": after} if after else {})}
        r = await client.get(f"{svc.base_url}/air/airlines", headers=svc.headers, params=params)
        if r.status_code != 200:
            print(f"Duffel airline directory unavailable (HTTP {r.status_code}); skipping name lookup")
            return logos
        body = r.json()
        for airline in body.get("data") or []:
            if airline.get("name") and airline.get("logo_symbol_url"):
                logos.setdefault(airline["name"].strip().lower(), airline["logo_symbol_url"])
        after = (body.get("meta") or {}).get("after")
        if not after:
            return logos


async def _main(apply: bool) -> None:
    from app.core.firebase import get_db
    from app.services.duffel_service import DuffelService

    svc = DuffelService()
    async with httpx.AsyncClient(timeout=30.0) as client:
        logos = await _airline_logos(client, svc)
        counts = await backfill(get_db(), _duffel_fetcher(client, svc), apply=apply, logos_by_name=logos)
    print(
        f"\n{'Updated' if apply else 'Would update'}: {counts['updated']} with itinerary, "
        f"{counts['logo_only']} logo only. Unavailable {counts['unavailable']}, "
        f"already complete {counts['skipped']}."
    )
    if not apply and (counts["updated"] or counts["logo_only"]):
        print("Run again with --apply to write the changes.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write changes (default is a dry run)")
    asyncio.run(_main(parser.parse_args().apply))
