"""Real-estate photographers, and Google Places leads when a key is set.

The register cannot tell a real-estate photographer from a wedding one, so we search.
Each result becomes a lead with its website; enrichment then confirms the real-estate
work (the `real_estate` tag) from the site itself.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from sqlalchemy import String, cast, select

from prospecting.areas import FOREIGN_TLDS, RINGS
from prospecting.config import settings
from prospecting.db import Lead, session
from prospecting.leads import distance_from_center, find_match
from prospecting.sources.search import (
    DuckDuckGo,
    GooglePlaces,
    SearchBlocked,
    already_ran,
)
from prospecting.text import (
    NOT_OWN_SITE,
    SOCIAL,
    ascii_lower,
    domain_in,
    find_phones,
    host,
    network_of,
)

TOWNS = RINGS[0].towns
PHOTO_TERMS = [
    "photographe immobilier",
    "photographe architecture intérieur",
    "visite virtuelle immobilier",
    "photo drone immobilier",
]
PLACES_TERMS = ["agence immobilière", "photographe immobilier", "mandataire immobilier"]


NEWCOMER_QUERIES = [
    "photographe immobilier indépendant",
    "photographe immobilier auto-entrepreneur",
    "photographe immobilier débutant tarifs",
    "jeune photographe immobilier",
    "photographe immobilier instagram",
    "photographe immobilier freelance france",
    "lancement activité photographe immobilier",
]


def _photo_queries(towns: list[str] | None) -> list[str]:
    if towns is not None:
        return NEWCOMER_QUERIES + [f"photographe immobilier {t}" for t in towns]
    qs = [f"{term} {settings.center_name}" for term in PHOTO_TERMS]
    qs += [f"photographe immobilier {t}" for t in TOWNS if t != settings.center_name]
    qs += ["photographe immobilier Alsace", "photographe immobilier Bas-Rhin"]
    return qs


_SOCIAL_PROFILE = re.compile(
    r"^https?://(?:[a-z]+\.)?(?:instagram|facebook)\.com/([A-Za-z0-9_.\-]+)/?$"
)


def _social_lead(s, r, network: str, text: str, query: str) -> int:
    """A photographer known only by an Instagram or Facebook page. 1 if new."""
    m = _SOCIAL_PROFILE.match(r.url.split("?")[0])
    if not m or m.group(1).lower() in {"p", "reel", "explore", "pages", "groups", "watch", "tags"}:
        return 0
    if "photo" not in text or "immo" not in text:
        return 0
    url = m.group(0).rstrip("/") + "/"
    if any(
        url in (l.socials or {}).values()
        for l in s.scalars(
            select(Lead).where(Lead.kind == "photographer", cast(Lead.socials, String).like(f"%{m.group(1)}%"))
        )
    ):
        return 0
    name = re.split(r"\s[-|•(@]\s?|\s\(@", r.title)[0].strip() or m.group(1)
    lead = Lead(kind="photographer", name=name[:120], socials={network: url})
    lead.add_tags({"social_only", "real_estate"})
    lead.add_source(f"duckduckgo: {query}", url)
    s.add(lead)
    return 1


def collect_photographers(
    towns: list[str] | None = None, progress: Callable[[str], None] = print
) -> int:
    """Search for real-estate photographers; `towns=None` is the Strasbourg area."""
    ddg = DuckDuckGo()
    created = 0
    for q in _photo_queries(towns):
        if already_ran(ddg.name, q):
            continue
        try:
            results = ddg.search(q)
        except SearchBlocked as e:
            progress(f"photographers: stopped ({e}); rerun later to continue")
            break
        with session() as s:
            for r in results:
                text = ascii_lower(r.title + " " + r.snippet)
                if (social := domain_in(r.url, SOCIAL)) and social != "linkedin.com":
                    # Many new photographers only have Instagram or Facebook: keep them.
                    created += _social_lead(s, r, SOCIAL[social], text, q)
                    continue
                if domain_in(r.url, NOT_OWN_SITE) or network_of(r.url):
                    continue
                if host(r.url).endswith(FOREIGN_TLDS):  # France only
                    continue
                site = "https://" + host(r.url) + "/"
                title = r.title.split(" - ")[0].split(" | ")[0].strip() or host(r.url)
                if find_match(s, title, website=site) or s.scalar(
                    select(Lead).where(Lead.website == site)
                ):
                    continue
                if "photo" not in text and "visite virtuelle" not in text:
                    continue
                lead = Lead(kind="photographer", name=title[:120], website=site)
                lead.website_source = f"duckduckgo: {q}"
                lead.add_source("duckduckgo", r.url)
                s.add(lead)
                created += 1
            s.commit()
        progress(f"photographers: '{q}' -> {len(results)} results, {created} new so far")
    return created


def collect_places(progress: Callable[[str], None] = print) -> tuple[int, int]:
    if not settings.google_places_key:
        progress("places: no PROSPECT_GOOGLE_PLACES_KEY, skipped")
        return 0, 0
    gp = GooglePlaces(settings.google_places_key)
    created = matched = 0
    for term in PLACES_TERMS:
        for town in TOWNS:
            q = f"{term} {town}"
            if already_ran(gp.name, q):
                continue
            places = gp.search(q)
            with session() as s:
                for p in places:
                    if (
                        p.lat is None
                        or (distance_from_center(p.lat, p.lon) or 0) > settings.radius_km
                    ):
                        continue
                    lead = s.scalar(select(Lead).where(Lead.places_id == p.id)) or find_match(
                        s, p.name, p.lat, p.lon, website=p.website
                    )
                    if lead is None:
                        kind = "photographer" if "photograph" in ascii_lower(term) else "agency"
                        lead = Lead(kind=kind, name=p.name, address=p.address)
                        lead.lat, lead.lon = p.lat, p.lon
                        lead.distance_km = distance_from_center(p.lat, p.lon)
                        s.add(lead)
                        created += 1
                    else:
                        matched += 1
                    lead.places_id = p.id
                    src = f"https://www.google.com/maps/place/?q=place_id:{p.id}"
                    lead.add_source("google_places", src)
                    if p.website and not lead.website and not domain_in(p.website, NOT_OWN_SITE):
                        lead.website, lead.website_source = p.website, src
                    for ph in find_phones(p.phone or ""):
                        lead.add_contact("phones", ph, src)
                    lead.rating, lead.rating_count = p.rating, p.rating_count
                    lead.network = lead.network or network_of(p.website, p.name)
                s.commit()
            progress(f"places: '{q}' -> {len(places)}; {created} new, {matched} matched")
    return created, matched
