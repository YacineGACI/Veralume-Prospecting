"""Enrichment: find each lead's website, then read it like a visitor would.

Website discovery, all free:
1. already known (OpenStreetMap, Google Places, the search, or typed in the app);
2. SAFTI / iad advisor profile, matched by name in the networks' public sitemaps;
3. a guessed domain from the name, accepted only when the site shows the SIREN or the
   name plus the town.

Then up to `max_pages_per_site` pages (home, contact, legal notice, team, listings) give
emails, phones, the named manager, social links, what the business does, and listing
photos to measure. Work runs in threads; results are applied in the main thread.
"""

from __future__ import annotations

import json
import re
import socket
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser
from sqlalchemy import select

from prospecting.config import settings
from prospecting.countries import COUNTRIES
from prospecting.db import Lead, Photo, now, session
from prospecting.http import Fetcher, Page
from prospecting.leads import distance_from_center
from prospecting.text import (
    NOT_OWN_SITE,
    SOCIAL,
    ascii_lower,
    domain_in,
    email_belongs,
    find_emails,
    find_phones,
    host,
    name_tokens,
    network_of,
    pretty_name,
)

NETWORK_SITEMAPS = {
    "SAFTI": "https://www.safti.fr/sitemaps/sitemap.mandataires.xml",
    "iad": "https://www.iadfrance.fr/sitemap/fr/agents.xml",
}

_TAGS = {
    "transaction": r"\b(vente|acheter|achat|estimation|a vendre)\b",
    "rental": r"\blocation\b",
    "management": r"gestion locative|administration de biens",
    "syndic": r"\bsyndic\b",
    "short_stay": r"location saisonniere|conciergerie|airbnb|courte duree",
    "new_build": r"programme neuf|immobilier neuf|\bvefa\b",
    "prestige": r"\bprestige\b|\bluxe\b|biens d.exception",
    "commercial": r"immobilier d.entreprise|locaux commerciaux|fonds de commerce|bureaux a louer",
    "virtual_tour": r"visite virtuelle|matterport|visite 360|virtuelle?r? (rundgang|tour)|"
    r"virtuele tour|360.?(grad|rundgang)|tour virtuel|visita virtuale",
    "drone": r"\bdrone\b|\bdrohne|\bdrohnen|luftaufnahme",
    "home_staging": r"home staging",
    "pro_photos": r"photographe professionnel|photos? professionnelles?|reportage photo|"
    r"professionelle fotos|professionele foto",
    # French, German, Italian (immobili…), Dutch, English.
    "real_estate": r"immobili|vastgoed|real estate|makelaar",
}

_PAGE_KEYS = [
    (r"contact|coordonnees|nous-trouver|kontakt|contatt", 5),
    (r"mentions-legales|mentions_legales|legal|mentions|impressum|colofon", 5),
    (r"equipe|a-propos|qui-sommes|notre-agence|l-agence|about|presentation|ueber-mich|"
     r"uber-mich|ueber-uns|uber-uns|over-mij|over-ons|chi-siamo", 3),  # fmt: skip
    (
        r"vente|acheter|achat|nos-biens|biens|annonces|catalogue|proprietes|portfolio|realisations|"
        r"galerie|referenzen|projekte|immobilien",
        4,
    ),
]
_DETAIL = re.compile(
    r"(annonce|detail|fiche|bien|propriete|property|vente|achat|appartement|maison)[^?#]*\d{3,}",
    re.IGNORECASE,
)
_PHOTO_PAGE = re.compile(r"vente|biens|annonces|portfolio|realisations|galerie", re.IGNORECASE)
_IMG_BAD = re.compile(
    r"logo|icon|picto|avatar|equipe|team|portrait|banner|header|footer|flag|sprite|marker|"
    r"placeholder|loader|partenaire|partner|label|fnaim|qr|badge|favicon|\.svg|\.gif|"
    r"facebook|instagram|linkedin|google|tracking|pixel|blank|spacer|gravatar|emoji",
    re.IGNORECASE,
)
_IMG_URL = re.compile(r"https?://[^\s\"'<>()]+?\.(?:jpe?g|webp)(?:\?[^\s\"'<>()]*)?", re.IGNORECASE)
_PUBLISHER = re.compile(
    r"(?i:directeur|directrice|responsable)\s+(?i:de\s+(?:la\s+)?)?(?i:publication)\s*[:\-–]?\s*"
    r"(?:(?i:M\.|Mr|Mme|Monsieur|Madame)\s+)?([A-ZÀ-Ý][\w'’\-]+(?:\s+[A-ZÀ-Ý][\w'’\-]+){1,2})"
)
# German legal notice (Impressum): 'Inhaber: Max Mustermann'.
_OWNER = re.compile(
    r"(?i:inhaber(?:in)?|verantwortlich(?:\s+f(?:ü|ue)r\s+den\s+inhalt)?)\s*[:\-–]?\s*"
    r"(?:(?i:Herr|Frau)\s+)?([A-ZÄÖÜ][\wäöüß'’\-]+(?:\s+[A-ZÄÖÜ][\wäöüß'’\-]+){1,2})"
)


@dataclass
class Findings:
    lead_id: int
    status: str = "done"
    note: str | None = None
    website: str | None = None
    website_source: str | None = None
    network: str | None = None
    emails: list[tuple[str, str]] = field(default_factory=list)
    phones: list[tuple[str, str]] = field(default_factory=list)
    people: list[tuple[str, str | None, str]] = field(default_factory=list)
    socials: dict[str, str] = field(default_factory=dict)
    tags: set[str] = field(default_factory=set)
    listings_seen: int = 0
    photos: list[tuple[str, str]] = field(default_factory=list)
    # (town, postcode, lat, lon), found on the site for leads without an address.
    location: tuple[str, str, float, float] | None = None


@dataclass
class LeadView:
    """What a worker thread needs; ORM objects stay in the main thread."""

    id: int
    kind: str
    name: str
    legal_name: str | None
    siren: str | None
    city: str | None
    postcode: str | None
    website: str | None
    website_source: str | None
    country: str = "FR"


# --- website discovery -------------------------------------------------------------


def _name_key(s: str) -> str:
    return re.sub(r"[^a-z]", "", ascii_lower(s))


def network_profiles(fetcher: Fetcher) -> dict[str, dict[str, str]]:
    path = settings.cache_dir / "networks.json"
    if path.exists():
        return json.loads(path.read_text())
    out: dict[str, dict[str, str]] = {}
    for network, url in NETWORK_SITEMAPS.items():
        if not fetcher.allowed(url):
            continue
        try:
            r = fetcher.client.get(url, timeout=60)
            r.raise_for_status()
        except httpx.HTTPError:
            continue
        out[network] = {
            _name_key(u.rstrip("/").rsplit("/", 1)[-1]): u
            for u in re.findall(r"<loc>([^<]+)</loc>", r.text)
        }
    path.write_text(json.dumps(out))
    return out


def _profile_for(lead: LeadView, profiles: dict[str, dict[str, str]]) -> tuple[str, str] | None:
    """A sole trader's advisor page, matched on 'first last' from the register."""
    base = re.sub(r"\(.*?\)", "", lead.legal_name or lead.name).split()
    if len(base) < 2:
        return None
    keys = {_name_key("".join(base)), _name_key(base[0] + base[-1])}
    for network, slugs in profiles.items():
        for k in keys:
            if k in slugs:
                return network, slugs[k]
    return None


def guess_domains(name: str, country: str = "FR") -> list[str]:
    toks = name_tokens(name)
    tlds = (*COUNTRIES[country].tlds[-1:], ".com") if country in COUNTRIES else (".fr", ".com")
    if not toks or sum(len(t) for t in toks) < 4:
        return []
    joined, dashed = "".join(toks), "-".join(toks)
    bases = list(dict.fromkeys([joined, dashed]))
    out = []
    for b in bases:
        for pre, suf in [("", ""), ("", "-immobilier"), ("", "immobilier"), ("", "-immo"),
                         ("", "immo"), ("immobilier-", ""), ("agence-", "")]:  # fmt: skip
            for tld in tlds:
                out.append(pre + b + suf + tld)
        out.append(b + ".immo")
    return list(dict.fromkeys(out))


def _resolves(domain: str) -> bool:
    try:
        socket.getaddrinfo(domain, 443)
        return True
    except OSError:
        return False


def _confirms(lead: LeadView, page: Page, fetcher: Fetcher) -> bool:
    """The site is this lead's: it shows the SIREN, or the name and the town."""
    digits = re.sub(r"\D", "", page.text)
    if lead.siren and lead.siren in digits:
        return True
    text = ascii_lower(HTMLParser(page.text).text(separator=" "))
    if "immo" not in text:
        return False
    toks = [t for t in name_tokens(lead.name) if len(t) > 2]
    named = bool(toks) and all(t in text for t in toks)
    placed = any(p and ascii_lower(p) in text for p in (lead.city, lead.postcode))
    if named and placed:
        return True
    if lead.siren:  # legal notice usually carries the SIREN
        for link in _links(page)[:200]:
            if re.search(r"mentions", link, re.IGNORECASE):
                legal = fetcher.get(link)
                return bool(legal and lead.siren in re.sub(r"\D", "", legal.text))
    return False


def _profile_is_local(lead: LeadView, page: Page | None) -> bool:
    """Network profiles are matched on a name alone, so the profile must show a postcode
    in the lead's own département (iad and SAFTI put "Town (67000)" in the title)."""
    if page is None or not lead.postcode:
        return False
    title = HTMLParser(page.text).css_first("title")
    head = (title.text() if title else "") + " " + page.text[:20000]
    return any(pc.startswith(lead.postcode[:2]) for pc in re.findall(r"\((\d{5})\)", head))


def discover(lead: LeadView, fetcher: Fetcher, profiles: dict) -> tuple[str, str] | None:
    if lead.kind == "independent_agent" and (hit := _profile_for(lead, profiles)):
        if _profile_is_local(lead, fetcher.get(hit[1])):
            return hit[1], f"{hit[0]} sitemap, same département on the profile"
    for name in dict.fromkeys(filter(None, [lead.name, lead.legal_name])):
        for domain in guess_domains(name, lead.country):
            if not _resolves(domain):
                continue
            page = fetcher.get(f"https://{domain}/") or fetcher.get(f"http://{domain}/")
            if (
                page
                and not domain_in(page.final_url, NOT_OWN_SITE)
                and _confirms(lead, page, fetcher)
            ):
                return page.final_url, f"domain guess ({domain}), confirmed on the site"
    return None


# --- reading a site ----------------------------------------------------------------


def _links(page: Page) -> list[str]:
    tree = HTMLParser(page.text)
    out = []
    for a in tree.css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if href and not href.startswith(("#", "javascript:")):
            out.append(urljoin(page.final_url, href))
    return out


def _same_site(url: str, site: str) -> bool:
    return host(url) == host(site)


def _image_urls(page: Page) -> list[str]:
    tree = HTMLParser(page.text)
    found: list[str] = []
    for img in tree.css("img, source"):
        a = img.attributes
        srcset = a.get("srcset") or a.get("data-srcset")
        if srcset:
            found.append(srcset.split(",")[-1].strip().split(" ")[0])
        for k in (
            "data-src",
            "data-lazy-src",
            "data-original",
            "data-full",
            "data-zoom-image",
            "src",
        ):
            if a.get(k):
                found.append(a[k])  # type: ignore[arg-type]
                break
    for m in tree.css('meta[property="og:image"]'):
        if c := m.attributes.get("content"):
            found.append(c)
    for a in tree.css("a[href]"):
        href = a.attributes.get("href") or ""
        if re.search(r"\.(jpe?g|webp)(\?|$)", href, re.IGNORECASE):
            found.append(href)
    found += _IMG_URL.findall(page.text.replace("\\/", "/"))
    out = []
    for u in found:
        u = urljoin(page.final_url, u.strip())
        if u.startswith("http") and not _IMG_BAD.search(u) and u not in out:
            out.append(u)
    return out


def read_site(lead: LeadView, site: str, fetcher: Fetcher, f: Findings) -> None:
    home = fetcher.get(site)
    if home is None:
        f.status, f.note = "error", "website unreachable or disallowed by robots.txt"
        return
    site = home.final_url
    f.network = network_of(site, lead.name)
    links = [u for u in _links(home) if _same_site(u, site)]
    for u in _links(home):
        if d := domain_in(u, SOCIAL):
            f.socials.setdefault(SOCIAL[d], u.split("?")[0])

    ranked: list[tuple[int, str]] = []
    for u in dict.fromkeys(links):
        path = ascii_lower(urlsplit(u).path)
        score = max((w for pat, w in _PAGE_KEYS if re.search(pat, path)), default=0)
        if score and not _DETAIL.search(path):
            ranked.append((score, u))
    details = [u for u in dict.fromkeys(links) if _DETAIL.search(urlsplit(u).path)]
    f.listings_seen = len(details)
    ranked.sort(key=lambda x: -x[0])
    chosen = [u for _, u in ranked][: settings.max_pages_per_site - 3] + details[:3]

    # Images also on the home page are the site's own (logos, staff, stock), not listings.
    site_imgs = set(_image_urls(home))
    texts: list[str] = []
    listing_imgs: list[tuple[str, str]] = []
    for p in [home] + [p for u in chosen if (p := fetcher.get(u))]:
        tree = HTMLParser(p.text)
        for node in tree.css("script, style, noscript"):
            node.decompose()
        text = tree.text(separator=" ")
        texts.append(text)
        mailtos = [
            (a.attributes.get("href") or "")[7:].split("?")[0]
            for a in tree.css('a[href^="mailto:"]')
        ]
        tels = [(a.attributes.get("href") or "")[4:] for a in tree.css('a[href^="tel:"]')]
        for e in find_emails(" ".join(mailtos) + " " + text):
            if email_belongs(e, site):
                f.emails.append((e, p.final_url))
        for ph in find_phones(" ".join(tels) + " " + text, lead.country):
            f.phones.append((ph, p.final_url))
        low = ascii_lower(text)
        for tag, pat in _TAGS.items():
            if re.search(pat, low):
                f.tags.add(tag)
        if m := _PUBLISHER.search(text):
            f.people.append(
                (pretty_name(m.group(1).strip()), "Directeur de la publication", p.final_url)
            )
        elif lead.country in ("DE", "CH", "LU") and (m := _OWNER.search(text)):
            f.people.append((m.group(1).strip(), "Inhaber", p.final_url))
        path = urlsplit(p.final_url).path
        if p is not home and (_DETAIL.search(path) or _PHOTO_PAGE.search(path)):
            listing_imgs += [(u, p.final_url) for u in _image_urls(p) if u not in site_imgs]
    # Listing detail pages first, then listing indexes, then (if little else) the home page.
    listing_imgs.sort(key=lambda x: 0 if _DETAIL.search(urlsplit(x[1]).path) else 1)
    if len(listing_imgs) < settings.photos_per_lead * 2:
        listing_imgs += [(u, home.final_url) for u in site_imgs]
    f.photos = list(dict((u, (u, pg)) for u, pg in reversed(listing_imgs)).values())[::-1][:16]
    if not lead.postcode:
        pages = texts[1:] + texts[:1]  # contact / legal pages before home
        f.location = locate(pages) if lead.country == "FR" else locate_abroad(pages, lead.country)
    if lead.kind == "photographer" and "real_estate" not in f.tags:
        f.note = "no real-estate work found on the site"


_POSTCODE_TOWN = re.compile(r"\b(\d{5})[\s,]+([A-ZÀ-Ý][A-Za-zÀ-ÿ'’\- ]{1,40})")


def locate(texts: list[str]) -> tuple[str, str, float, float] | None:
    """The first 'postcode Town' on the site that is a real French commune."""
    tried = 0
    for text in texts:
        for m in _POSTCODE_TOWN.finditer(text):
            if tried >= 5:
                return None
            tried += 1
            try:
                r = httpx.get(
                    "https://geo.api.gouv.fr/communes",
                    params={"codePostal": m.group(1), "fields": "nom,centre"},
                    timeout=10,
                )
                r.raise_for_status()
                communes = r.json()
            except (httpx.HTTPError, ValueError):
                continue
            town = ascii_lower(m.group(2)).replace("-", " ")
            for c in communes:
                name = ascii_lower(c["nom"]).replace("-", " ")
                if town.startswith(name) and c.get("centre"):
                    lon, lat = c["centre"]["coordinates"]
                    return c["nom"], m.group(1), lat, lon
    return None


_NOMINATIM_LOCK = threading.Lock()
_NOMINATIM_LAST = [0.0]


def _nominatim(params: dict) -> list[dict]:
    """OpenStreetMap's geocoder: free, one request a second at most (its usage policy)."""
    with _NOMINATIM_LOCK:
        wait = 1.1 - (time.monotonic() - _NOMINATIM_LAST[0])
        if wait > 0:
            time.sleep(wait)
        _NOMINATIM_LAST[0] = time.monotonic()
        try:
            r = httpx.get(
                "https://nominatim.openstreetmap.org/search",
                params={**params, "format": "json", "limit": 1},
                headers={"User-Agent": settings.user_agent},
                timeout=15,
            )
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError):
            return []


def locate_abroad(texts: list[str], country: str) -> tuple[str, str, float, float] | None:
    """The first 'postcode Town' written the way `country` writes addresses, placed by
    Nominatim within that country."""
    c = COUNTRIES.get(country)
    if c is None or c.postcode is None:
        return None
    tried = 0
    for text in texts:
        for m in c.postcode.finditer(text):
            if tried >= 3:
                return None
            tried += 1
            pc = m.group("pc").replace(" ", "")
            if country == "CA":
                pc = f"{pc[:3]} {pc[3:]}"
            town = re.split(r"\s{2,}|\s(?:Tel|Tél|Fon|Phone|E-?mail|Mobil|Mob|GSM)\b", m.group("town"))[0]
            town = town.strip(" ,.-")
            if len(town) < 2:
                continue
            hits = _nominatim({"q": f"{pc} {town}", "countrycodes": country.lower()})
            if hits:
                return town[:120], pc, float(hits[0]["lat"]), float(hits[0]["lon"])
    return None


def enrich_one(lead: LeadView, fetcher: Fetcher, profiles: dict) -> Findings:
    f = Findings(lead.id)
    try:
        site, source = lead.website, lead.website_source
        if not site:
            hit = discover(lead, fetcher, profiles)
            if hit is None:
                f.status, f.note = "no_website", "no website found automatically"
                return f
            site, source = hit
        f.website, f.website_source = site, source
        read_site(lead, site, fetcher, f)
    except Exception as e:  # one bad site must not stop the batch
        f.status, f.note = "error", f"{type(e).__name__}: {e}"[:300]
    return f


MAX_CONTACTS = 6


def _first(items: list[tuple[str, str]], n: int) -> list[tuple[str, str]]:
    return list({v: (v, src) for v, src in reversed(items)}.values())[::-1][:n]


def apply(f: Findings) -> None:
    with session() as s:
        lead = s.get(Lead, f.lead_id)
        if lead is None:
            return
        lead.enrich_status, lead.enrich_note, lead.enriched_at = f.status, f.note, now()
        if f.website and not lead.website:
            lead.website, lead.website_source = f.website, f.website_source
            lead.add_source("website", f.website)
        lead.network = lead.network or f.network
        if f.location and not lead.postcode:
            lead.city, lead.postcode, lead.lat, lead.lon = f.location
            lead.distance_km = distance_from_center(lead.lat, lead.lon)
        # A network's site lists every branch; keep the first few, found on home/contact.
        for e, src in _first(f.emails, MAX_CONTACTS):
            lead.add_contact("emails", e, src)
        for ph, src in _first(f.phones, MAX_CONTACTS):
            lead.add_contact("phones", ph, src)
        for name, role, src in f.people:
            lead.add_person(name, role, src)
        if f.socials:
            lead.socials = {**f.socials, **(lead.socials or {})}
        lead.add_tags(f.tags)
        if f.listings_seen:
            lead.listings_seen = f.listings_seen
        known = {p.url for p in lead.photos}
        for url, page in f.photos:
            if url not in known:
                lead.photos.append(Photo(url=url, page_url=page))
        s.commit()


def run(
    lead_ids: list[int] | None = None,
    limit: int | None = None,
    retry: bool = False,
    workers: int = 8,
    progress: Callable[[str], None] = print,
) -> dict[str, int]:
    with session() as s:
        q = select(Lead).where(Lead.do_not_contact.is_(False))
        if lead_ids:
            q = q.where(Lead.id.in_(lead_ids))
        elif not retry:
            q = q.where(Lead.enrich_status.is_(None))
        # Most promising first: known website, then agencies with staff, nearest first.
        leads = sorted(
            s.scalars(q),
            key=lambda l: (
                l.website is None,
                l.kind == "independent_agent",
                l.headcount in (None, "NN", "00"),
                l.distance_km or 99,
            ),
        )[:limit]
        views = [
            LeadView(
                l.id,
                l.kind,
                l.name,
                l.legal_name,
                l.siren,
                l.city,
                l.postcode,
                l.website,
                l.website_source,
                l.country or "FR",
            )  # fmt: skip
            for l in leads
        ]
    fetcher = Fetcher()
    profiles = network_profiles(fetcher)
    counts: dict[str, int] = {}
    with ThreadPoolExecutor(workers) as ex:
        futures = [ex.submit(enrich_one, v, fetcher, profiles) for v in views]
        for i, fut in enumerate(as_completed(futures), 1):
            f = fut.result()
            apply(f)
            counts[f.status] = counts.get(f.status, 0) + 1
            if i % 25 == 0 or i == len(futures):
                progress(f"enrich: {i}/{len(futures)} {counts}")
    fetcher.close()
    return counts
