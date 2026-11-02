"""Real-estate photographers all over France, from the public directory
trouver-mon-photographe.fr (category "immobilier-architecture"; robots.txt allows it).

Each city page lists the photographers who serve that area; each profile gives the
photographer's name, town and, usually, their own website, which enrichment then reads
for emails and phones. We visit the largest town of every département, slowly, and
remember every page and profile done, so a stopped run resumes where it was.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from urllib.parse import urljoin

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser
from sqlalchemy import select

from prospecting.db import Lead, SearchQuery, session
from prospecting.http import Fetcher
from prospecting.leads import distance_from_center
from prospecting.sources.search import already_ran
from prospecting.text import NOT_OWN_SITE, SOCIAL, ascii_lower, domain_in, host

BASE = "https://www.trouver-mon-photographe.fr"
CATEGORY = "immobilier-architecture"
INTERVAL_S = 4.0  # polite: one request every 4 s to this site
_PROFILE = re.compile(r"/fr/photographe/(\d+)-[a-z0-9-]+")
# Metropolitan départements and overseas ones (the directory is France-only).
DEPARTEMENTS = [f"{i:02d}" for i in range(1, 96) if i != 20] + [
    "2A",
    "2B",
    "971",
    "972",
    "973",
    "974",
]


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", ascii_lower(name)).strip("-")


def _towns() -> list[tuple[str, str]]:
    """(slug, label) for the largest town of each département, e.g. ('bordeaux-33000', ...)."""
    out = []
    with httpx.Client(timeout=20) as client:
        for dep in DEPARTEMENTS:
            try:
                r = client.get(
                    f"https://geo.api.gouv.fr/departements/{dep}/communes",
                    params={"fields": "nom,codesPostaux,population"},
                )
                r.raise_for_status()
                biggest = max(r.json(), key=lambda c: c.get("population") or 0)
            except (httpx.HTTPError, ValueError):
                continue
            postcode = sorted(biggest["codesPostaux"])[0]
            out.append((f"{slugify(biggest['nom'])}-{postcode}", f"{biggest['nom']} ({dep})"))
    return out


def _remember(engine: str, query: str, n: int = 0) -> None:
    with session() as s:
        s.add(SearchQuery(engine=engine, query=query, results=n))
        s.commit()


def _commune(name: str) -> tuple[str, str | None, float | None, float | None]:
    """Official name, postcode and centre of a French town, best match by population."""
    try:
        r = httpx.get(
            "https://geo.api.gouv.fr/communes",
            params={
                "nom": name,
                "fields": "nom,codesPostaux,centre",
                "boost": "population",
                "limit": 1,
            },  # fmt: skip
            timeout=10,
        )
        r.raise_for_status()
        hits = r.json()
    except (httpx.HTTPError, ValueError):
        hits = []
    if not hits:
        return name, None, None, None
    c = hits[0]
    lon, lat = (c.get("centre") or {}).get("coordinates", (None, None))
    return c["nom"], (c.get("codesPostaux") or [None])[0], lat, lon


def parse_profile(html: str, url: str) -> dict:
    """Name, business name, town and own website from a profile page."""
    tree = HTMLParser(html)
    title_node = tree.css_first("title")
    title = title_node.text().strip() if title_node else ""
    h1 = tree.css_first("h1")
    person = h1.text(strip=True) if h1 else None
    business, town = None, None
    if m := re.match(r"(.+?)\s*:\s*(.+?)\s+à\s+(.+)$", title):
        business, person, town = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
    elif m := re.match(r"(.+?)\s+à\s+(.+)$", title):
        person, town = m.group(1).strip(), m.group(2).strip()
    website = None
    for a in tree.css('a[data-stats="clic_btn_siteInternet"], a[itemprop="url"]'):
        href = urljoin(url, (a.attributes.get("href") or "").strip())
        if (
            href.startswith("http")
            and "trouver-mon-photographe" not in href
            and not domain_in(href, NOT_OWN_SITE)
            and not domain_in(href, SOCIAL)
        ):
            website = href
            break
    socials: dict[str, str] = {}
    for a in tree.css("a[href]"):
        href = a.attributes.get("href") or ""
        own = "trouver" not in re.sub(r"[^a-z]", "", ascii_lower(href))  # not the directory's
        if (d := domain_in(href, SOCIAL)) and own:
            socials.setdefault(SOCIAL[d], href.split("?")[0])
    real_estate = "immobilier & architecture" in ascii_lower(html.replace("&amp;", "&"))
    reviews = re.search(r"(\d+)\s+avis clients?", tree.text(separator=" "))
    return {"person": person, "business": business, "town": town, "website": website,
            "socials": socials, "real_estate": real_estate,
            "reviews": int(reviews.group(1)) if reviews else 0}  # fmt: skip


def _upsert(p: dict, profile_url: str) -> bool:
    """Create or update the photographer's lead. True if new."""
    name = p["business"] or p["person"] or profile_url.rsplit("/", 1)[-1]
    with session() as s:
        lead = None
        if p["website"]:
            h = host(p["website"])
            lead = next(
                (l for l in s.scalars(select(Lead).where(Lead.website.ilike(f"%{h}%")))
                 if l.website and host(l.website) == h),
                None,
            )  # fmt: skip
        new = lead is None
        if new:
            lead = Lead(kind="photographer", name=name[:120])
            if p["town"]:
                lead.city, lead.postcode, lead.lat, lead.lon = _commune(p["town"])
                lead.distance_km = distance_from_center(lead.lat, lead.lon)
            s.add(lead)
        if p["website"] and not lead.website:
            lead.website, lead.website_source = p["website"], profile_url
        if p["person"] and p["person"] != name:
            person = p["person"].title() if p["person"].isupper() else p["person"]
            lead.add_person(person, "Photographe", profile_url)
        if p["socials"]:
            lead.socials = {**p["socials"], **(lead.socials or {})}
        tags = {"real_estate", "directory_listed"}
        if p.get("reviews", 0) <= 3:
            tags.add("few_reviews")
        if p.get("recent"):
            tags.add("recent_profile")
        lead.add_tags(tags)
        lead.add_source("trouver-mon-photographe", profile_url)
        s.commit()
    return new


def collect(progress: Callable[[str], None] = print, max_pages: int = 12) -> int:
    fetcher = Fetcher(interval_s=INTERVAL_S)
    towns = _towns()
    progress(f"directory: {len(towns)} towns to visit")
    created = 0
    try:
        for i, (slug, label) in enumerate(towns, 1):
            profiles: list[str] = []
            for page_no in range(1, max_pages + 1):
                url = f"{BASE}/fr/photographes/{slug}/{CATEGORY}"
                if page_no > 1:
                    url += f"?p={page_no}"
                page = fetcher.get(url)  # cached for 14 days, so a resumed run is fast here
                if page is None:
                    break
                profiles += sorted({BASE + m.group(0) for m in _PROFILE.finditer(page.text)})
                pages = re.findall(rf"{CATEGORY}\?p=(\d+)", page.text)
                if page_no >= max((int(n) for n in pages), default=1):
                    break
            for profile_url in dict.fromkeys(profiles):
                if already_ran("tmp_profile", profile_url):
                    continue
                page = fetcher.get(profile_url)
                if page is not None:
                    created += _upsert(parse_profile(page.text, profile_url), profile_url)
                _remember("tmp_profile", profile_url, int(page is not None))
            progress(
                f"directory: {i}/{len(towns)} {label}: {len(profiles)} profiles, {created} new so far"
            )
    finally:
        fetcher.close()
    return created


RECENT_SHARE = 0.2  # the newest 20% of profile ids count as recent sign-ups


def _seen_ids() -> set[int]:
    with session() as s:
        rows = s.scalars(select(SearchQuery.query).where(SearchQuery.engine == "tmp_profile"))
        return {int(m.group(1)) for q in rows if (m := _PROFILE.search(q))}


def sweep(
    max_id: int = 6000,
    every: int = 50,
    on_batch: Callable[[], None] | None = None,
    progress: Callable[[str], None] = print,
) -> int:
    """Every profile id, newest first, so photographers the ranked city lists never show
    (new or small ones) are found too. Only real-estate profiles become leads."""
    fetcher = Fetcher(interval_s=INTERVAL_S)
    seen = _seen_ids()
    top = max(seen, default=max_id)
    created = since_batch = 0
    try:
        for pid in range(max(max_id, top), 0, -1):
            if pid in seen or already_ran("tmp_id", str(pid)):
                continue
            page = fetcher.get(f"{BASE}/fr/photographe/{pid}")
            _remember("tmp_id", str(pid))
            if page is None or not _PROFILE.search(page.final_url):
                continue  # deleted profile: redirected to the home page
            p = parse_profile(page.text, page.final_url)
            if p["real_estate"]:
                p["recent"] = pid >= top * (1 - RECENT_SHARE)
                if _upsert(p, page.final_url):
                    created += 1
                    since_batch += 1
            _remember("tmp_profile", page.final_url, int(p["real_estate"]))
            if since_batch >= every and on_batch:
                on_batch()
                since_batch = 0
            if pid % 250 == 0:
                progress(f"directory sweep: id {pid}, {created} new real-estate photographers")
    finally:
        fetcher.close()
    if on_batch:
        on_batch()
    return created
