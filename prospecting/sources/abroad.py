"""Real-estate photographers outside France: Belgium, Luxembourg, Switzerland, Germany
and Quebec. Free sources only, as in France.

1. OpenStreetMap photographers (craft=photographer) with a website. Most are wedding or
   portrait studios, so they are kept only if their site shows real-estate work
   (`prune` drops the others once enrichment has read the sites).
2. A slow DuckDuckGo search in each country's region and languages, town by town
   ("photographe immobilier Liège", "vastgoedfotograaf Gent", "Immobilienfotograf
   München"). These come from real-estate searches, so they are kept like in France.

Every lead gets its `country`: the site's own country domain when it has one (a .de site
found from Zurich is German), otherwise the country searched.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from sqlalchemy import String, cast, select

from prospecting.countries import COUNTRIES, country_of_url
from prospecting.db import Activity, Lead, SearchQuery, session
from prospecting.leads import distance_from_center, find_match
from prospecting.sources import osm
from prospecting.sources.search import DuckDuckGo, SearchBlocked, already_ran
from prospecting.text import (
    NOT_OWN_SITE,
    SOCIAL,
    ascii_lower,
    domain_in,
    find_emails,
    find_phones,
    host,
    network_of,
    pretty_name,
)

UNCONFIRMED = "check_real_estate"  # an OSM photographer whose site we haven't read yet
_PHOTO = re.compile(r"photo|foto|fotograf|valokuv|kuvaaja")
# In ASCII (accents dropped): French, German, Dutch, Italian, Spanish, Portuguese, English,
# Swedish, Danish, Norwegian, Finnish, Polish, Czech.
_REAL_ESTATE = re.compile(
    r"immo|vastgoed|real estate|makelaar|immeubl|architect|architekt|inmobiliari|imobili|"
    r"property|estate agent|maklar|fastighet|bolig|ejendom|eiendom|asunto|kiinteist|"
    r"nieruchomo|nemovitost"
)
# Words of the search itself: a result title made of them says nothing about who it is.
_GENERIC = re.compile(
    r"photographe|photographie|immobili|vastgoed|fotograf|fotografo|startseite|accueil|home\b|"
    r"willkommen|bienvenue|welkom|^\W*$|photographer|photography|inmobiliari|imobili|"
    r"property|estate agent|maklar|fastighet|bolig|ejendom|eiendom|asuntokuva|kiinteist|"
    r"nieruchomo|nemovitost|valokuva|inicio|benvenut|welcome"
)
# Country-code domains we don't prospect (Austria, Italy, the Netherlands, UK…).
_OURS = {"." + t.rsplit(".", 1)[-1] for c in COUNTRIES.values() for t in c.tlds}
# City and regional domains.
_CITY_TLDS = {".berlin": "DE", ".hamburg": "DE", ".koeln": "DE", ".bayern": "DE",
              ".nrw": "DE", ".saarland": "DE", ".ruhr": "DE", ".brussels": "BE",
              ".vlaanderen": "BE", ".gent": "BE", ".swiss": "CH", ".zuerich": "CH",
              ".quebec": "CA", ".wien": "AT", ".tirol": "AT", ".amsterdam": "NL",
              ".frl": "NL", ".london": "GB", ".scot": "GB", ".wales": "GB", ".cymru": "GB",
              ".madrid": "ES", ".barcelona": "ES", ".eus": "ES", ".gal": "ES",
              ".stockholm": "SE", ".helsinki": "FI", ".koeln": "DE"}  # fmt: skip


def _site_country(url: str, searched: str) -> str | None:
    """The lead's country from its domain, or the one searched for a generic domain.
    None for a country we don't prospect (and for France, done separately)."""
    h = host(url)
    for tld, code in _CITY_TLDS.items():
        if h.endswith(tld):
            return code
    code = country_of_url(url)
    if code:
        return None if code == "FR" else code
    tld = "." + h.rsplit(".", 1)[-1]
    if len(tld) == 3 and tld not in _OURS:
        return None
    return searched


def _name(title: str, url: str) -> str:
    for part in re.split(r"\s+[-|–—•·:]\s+|\s+\|\s*", title):
        part = part.strip()
        if part and len(part) > 2 and not _GENERIC.search(ascii_lower(part)):
            return part[:120]
    base = host(url).split(".")[0].replace("-", " ")
    return pretty_name(base.title())[:120]


# --- OpenStreetMap ------------------------------------------------------------------


def collect_osm(code: str, progress: Callable[[str], None] = print) -> int:
    c = COUNTRIES[code]
    key = f"photographers {code}"
    if already_ran("osm", key):
        progress(f"osm {code}: already imported")
        return 0
    q = f"""[out:json][timeout:180];
area[{c.osm_area}]->.z;
(
  nwr["craft"="photographer"](area.z);
  nwr["shop"="photo"]["photographer"](area.z);
);
out tags center;"""
    elements = osm._fetch(q)
    if elements is None:
        progress(f"osm {code}: Overpass unavailable, try again later")
        return 0
    created = 0
    with session() as s:
        for e in elements:
            t = e.get("tags", {})
            name = t.get("name") or t.get("brand")
            website = osm._tag(t, "website", "contact:website", "url")
            if not name or not website:
                continue  # without a site we can't tell a real-estate photographer
            if not website.startswith("http"):
                website = "https://" + website
            if domain_in(website, NOT_OWN_SITE):
                continue
            osm_id = f"{e['type']}/{e['id']}"
            lat = e.get("lat") or e.get("center", {}).get("lat")
            lon = e.get("lon") or e.get("center", {}).get("lon")
            if s.scalar(select(Lead).where(Lead.osm_id == osm_id)) or find_match(
                s, name, lat, lon, t.get("addr:city"), website
            ):
                continue
            lead = Lead(kind="photographer", name=pretty_name(name), country=code)
            lead.osm_id = osm_id
            lead.lat, lead.lon = lat, lon
            lead.distance_km = distance_from_center(lat, lon)
            lead.postcode = t.get("addr:postcode")
            lead.city = t.get("addr:city")
            street = " ".join(filter(None, [t.get("addr:housenumber"), t.get("addr:street")]))
            lead.address = " ".join(filter(None, [street, lead.postcode, lead.city])) or None
            src = f"https://www.openstreetmap.org/{osm_id}"
            lead.website, lead.website_source = website, src
            lead.add_source("osm", src)
            phones = osm._tag(t, "phone", "contact:phone", "contact:mobile") or ""
            for p in find_phones(phones, code):
                lead.add_contact("phones", p, src)
            for m in find_emails(osm._tag(t, "email", "contact:email") or ""):
                lead.add_contact("emails", m, src)
            lead.add_tags({UNCONFIRMED})
            s.add(lead)
            s.flush()  # so find_match sees it for the next element
            created += 1
        s.add(SearchQuery(engine="osm", query=key, results=len(elements)))
        s.commit()
    progress(f"osm {code}: {len(elements)} photographers mapped, {created} new with a website")
    return created


# --- DuckDuckGo ---------------------------------------------------------------------


def _social(s, url: str, title: str, text: str, code: str, query: str) -> int:
    m = re.match(r"^https?://(?:[a-z]+\.)?(instagram|facebook)\.com/([A-Za-z0-9_.\-]+)/?$",
                 url.split("?")[0])  # fmt: skip
    if not m or m.group(2).lower() in {"p", "reel", "explore", "pages", "groups", "watch", "tags"}:
        return 0
    if not (_PHOTO.search(text) and _REAL_ESTATE.search(text)):
        return 0
    profile = m.group(0).rstrip("/") + "/"
    if s.scalar(select(Lead.id).where(cast(Lead.socials, String).like(f"%/{m.group(2)}/%"))):
        return 0
    name = re.split(r"\s[-|•(@]\s?|\s\(@", title)[0].strip() or m.group(2)
    lead = Lead(kind="photographer", name=name[:120], socials={m.group(1): profile}, country=code)
    lead.add_tags({"social_only", "real_estate"})
    lead.add_source(f"duckduckgo: {query}", profile)
    s.add(lead)
    s.flush()
    return 1


def collect_search(code: str, progress: Callable[[str], None] = print) -> tuple[int, bool]:
    """(new leads, finished). Stops at DuckDuckGo's first captcha; rerun to continue."""
    ddg = DuckDuckGo()
    created = 0
    for region, queries in COUNTRIES[code].searches:
        for q in queries:
            if already_ran(ddg.name, q):
                continue
            try:
                results = ddg.search(q, region)
            except SearchBlocked as e:
                progress(f"search {code}: stopped ({e})")
                return created, False
            with session() as s:
                for r in results:
                    text = ascii_lower(r.title + " " + r.snippet)
                    if (social := domain_in(r.url, SOCIAL)) and social != "linkedin.com":
                        created += _social(s, r.url, r.title, text, code, q)
                        continue
                    if domain_in(r.url, NOT_OWN_SITE) or network_of(r.url):
                        continue
                    country = _site_country(r.url, code)
                    if country is None or not _PHOTO.search(text + " " + host(r.url)):
                        continue
                    site = "https://" + host(r.url) + "/"
                    name = _name(r.title, r.url)
                    if s.scalar(select(Lead.id).where(Lead.website == site)) or find_match(
                        s, name, website=site
                    ):
                        continue
                    lead = Lead(kind="photographer", name=name, website=site, country=country)
                    lead.website_source = f"duckduckgo: {q}"
                    lead.add_source("duckduckgo", r.url)
                    s.add(lead)
                    s.flush()
                    created += 1
                s.commit()
            progress(f"search {code}: '{q}' -> {len(results)} results, {created} new so far")
    return created, True


# --- after enrichment ---------------------------------------------------------------


def prune(progress: Callable[[str], None] = print) -> int:
    """Drop OSM photographers whose site shows no real-estate work (wedding, portrait…),
    unless you already did something with them. Confirmed ones lose the marker."""
    dropped = kept = 0
    with session() as s:
        q = select(Lead).where(cast(Lead.tags, String).like(f"%{UNCONFIRMED}%"))
        for lead in s.scalars(q):
            if lead.enrich_status is None:
                continue  # not read yet
            tags = set(lead.tags or [])
            touched = (
                lead.status != "new"
                or lead.labels
                or lead.notes
                or s.scalar(select(Activity.id).where(Activity.lead_id == lead.id).limit(1))
            )
            if "real_estate" in tags or touched:
                lead.tags = sorted(tags - {UNCONFIRMED})
                kept += 1
            else:
                s.delete(lead)
                dropped += 1
        s.commit()
    progress(f"osm abroad: {kept} real-estate photographers kept, {dropped} others dropped")
    return dropped
