"""Finding the same business across sources, so each one is a single lead."""

from __future__ import annotations

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from prospecting.config import settings
from prospecting.db import Lead
from prospecting.text import haversine_km, host, name_similarity


def distance_from_center(lat: float | None, lon: float | None) -> float | None:
    if lat is None or lon is None:
        return None
    return round(haversine_km(settings.center_lat, settings.center_lon, lat, lon), 1)


def find_match(
    s: Session,
    name: str,
    lat: float | None = None,
    lon: float | None = None,
    city: str | None = None,
    website: str | None = None,
) -> Lead | None:
    """Same website, or a similar name close by (or in the same town when unlocated)."""
    if website:
        h = host(website)
        for lead in s.scalars(select(Lead).where(Lead.website.ilike(f"%{h}%"))):
            if lead.website and host(lead.website) == h and name_similarity(lead.name, name) > 0:
                return lead
    if lat is not None and lon is not None:
        d = 0.01  # about 1 km
        near = select(Lead).where(
            or_(
                and_(
                    Lead.lat.between(lat - d, lat + d),
                    Lead.lon.between(lon - d * 1.5, lon + d * 1.5),
                ),
                Lead.lat.is_(None),
            )
        )
    elif city:
        near = select(Lead).where(Lead.city.ilike(city))
    else:
        return None
    best: tuple[float, Lead] | None = None
    for lead in s.scalars(near):
        sim = max(name_similarity(name, lead.name), name_similarity(name, lead.legal_name or ""))
        if sim < 0.5:
            continue
        if lat is not None and lead.lat is not None:
            if haversine_km(lat, lon, lead.lat, lead.lon) > 0.4:  # type: ignore[arg-type]
                continue
        elif not (city and lead.city and city.lower() == lead.city.lower()):
            continue
        if best is None or sim > best[0]:
            best = (sim, lead)
    return best[1] if best else None
