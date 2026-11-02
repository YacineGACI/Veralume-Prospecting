"""Estate agents and photographers mapped in OpenStreetMap (Overpass API, free).

OSM often has the website and phone the register lacks. Matching entries enrich the
register's lead; the rest become new leads.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx
from sqlalchemy import select

from prospecting.config import settings
from prospecting.db import Lead, session
from prospecting.leads import distance_from_center, find_match
from prospecting.text import find_emails, find_phones, network_of, pretty_name

OVERPASS_SERVERS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]


def _query(departement: str | None, radius_km: float | None) -> str:
    """A circle (clipped to France: it crosses the Rhine) or a whole département."""
    if departement:
        scope = "(area.zone)"
        zone = f'area["ref:INSEE"="{departement}"]["admin_level"="6"]->.zone;'
    else:
        r = int((radius_km or settings.radius_km) * 1000)
        scope = f"(around:{r},{settings.center_lat},{settings.center_lon})(area.zone)"
        zone = 'area["ISO3166-1"="FR"][admin_level=2]->.zone;'
    return f"""[out:json][timeout:180];
{zone}
(
  nwr["office"="estate_agent"]{scope};
  nwr["shop"="estate_agent"]{scope};
  nwr["craft"="photographer"]{scope};
  nwr["shop"="photo"]["photographer"]{scope};
);
out tags center;"""


def _commune(lat: float, lon: float) -> tuple[str | None, str | None]:
    """Town and postcode from the official geo API, for OSM entries without an address."""
    try:
        r = httpx.get(
            "https://geo.api.gouv.fr/communes",
            params={"lat": lat, "lon": lon, "fields": "nom,codesPostaux"},
            timeout=10,
        )
        r.raise_for_status()
        hits = r.json()
    except (httpx.HTTPError, ValueError):
        return None, None
    if not hits:
        return None, None
    return hits[0]["nom"], (hits[0].get("codesPostaux") or [None])[0]


def _fetch(query: str) -> list[dict] | None:
    """Elements from the first Overpass server that answers; None if all fail."""
    for attempt, url in enumerate([*OVERPASS_SERVERS, *OVERPASS_SERVERS]):
        try:
            r = httpx.post(
                url, data={"data": query}, headers={"User-Agent": settings.user_agent}, timeout=200
            )
            r.raise_for_status()
            return r.json()["elements"]
        except (httpx.HTTPError, ValueError, KeyError):
            time.sleep(10 * (attempt + 1))
    return None


def _tag(t: dict, *keys: str) -> str | None:
    for k in keys:
        if t.get(k):
            return t[k]
    return None


def collect(
    departement: str | None = None,
    radius_km: float | None = None,
    progress: Callable[[str], None] = print,
) -> tuple[int, int]:
    elements = _fetch(_query(departement, radius_km))
    if elements is None:
        progress(
            f"osm {departement or 'circle'}: Overpass unavailable, skipped (register leads kept)"
        )
        return 0, 0
    created = matched = 0
    with session() as s:
        for e in elements:
            t = e.get("tags", {})
            name = t.get("name") or t.get("brand")
            if not name:
                continue
            osm_id = f"{e['type']}/{e['id']}"
            lat = e.get("lat") or e.get("center", {}).get("lat")
            lon = e.get("lon") or e.get("center", {}).get("lon")
            city = t.get("addr:city")
            website = _tag(t, "website", "contact:website", "url")
            if website and not website.startswith("http"):
                website = "https://" + website
            is_photo = t.get("craft") == "photographer" or t.get("shop") == "photo"

            lead = s.scalar(select(Lead).where(Lead.osm_id == osm_id)) or find_match(
                s, name, lat, lon, city, website
            )
            if lead is None:
                lead = Lead(kind="photographer" if is_photo else "agency", name=pretty_name(name))
                lead.lat, lead.lon = lat, lon
                lead.distance_km = distance_from_center(lat, lon)
                lead.postcode = t.get("addr:postcode")
                if not city and lat is not None:
                    city, lead.postcode = _commune(lat, lon)
                lead.city = city
                street = " ".join(filter(None, [t.get("addr:housenumber"), t.get("addr:street")]))
                lead.address = " ".join(filter(None, [street, lead.postcode, city])) or None
                s.add(lead)
                created += 1
            else:
                matched += 1
            lead.osm_id = osm_id
            if not lead.city and lat is not None:
                lead.city, postcode = _commune(lat, lon)
                lead.postcode = lead.postcode or postcode
            src = f"https://www.openstreetmap.org/{osm_id}"
            lead.add_source("osm", src)
            if website and not lead.website:
                lead.website, lead.website_source = website, src
            for p in find_phones(_tag(t, "phone", "contact:phone", "contact:mobile") or ""):
                lead.add_contact("phones", p, src)
            for m in find_emails(_tag(t, "email", "contact:email") or ""):
                lead.add_contact("emails", m, src)
            lead.network = lead.network or network_of(website, name, t.get("brand"))
        s.commit()
    progress(
        f"osm {departement or 'circle'}: {len(elements)} mapped, {matched} matched existing leads, {created} new"
    )
    return created, matched
