"""Web search without paying.

- DuckDuckGo's HTML page: no key, but it shows a captcha after a few quick queries. We
  query slowly and stop at the first captcha instead of working around it; already-run
  queries are remembered, so the next run resumes.
- Google Places (optional, `PROSPECT_GOOGLE_PLACES_KEY`): Text Search returns websites,
  phones and ratings. Google gives a free monthly quota; cap it in the console.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser
from sqlalchemy import select

from prospecting.config import settings
from prospecting.db import SearchQuery, session


class SearchBlocked(RuntimeError):
    pass


@dataclass
class Result:
    url: str
    title: str
    snippet: str


def already_ran(engine: str, query: str) -> bool:
    with session() as s:
        return (
            s.scalar(
                select(SearchQuery).where(SearchQuery.engine == engine, SearchQuery.query == query)
            )
            is not None
        )


def _remember(engine: str, query: str, n: int) -> None:
    with session() as s:
        s.add(SearchQuery(engine=engine, query=query, results=n))
        s.commit()


class DuckDuckGo:
    name = "duckduckgo"
    _last = 0.0

    def __init__(self) -> None:
        self.client = httpx.Client(
            timeout=20, headers={"User-Agent": settings.user_agent}, follow_redirects=True
        )

    def search(self, query: str, region: str = "fr-fr") -> list[Result]:
        wait = settings.search_interval_s - (time.monotonic() - DuckDuckGo._last)
        if wait > 0:
            time.sleep(wait)
        DuckDuckGo._last = time.monotonic()
        r = self.client.post("https://html.duckduckgo.com/html/", data={"q": query, "kl": region})
        if r.status_code in (202, 403, 429) or "anomaly" in r.text[:20000]:
            raise SearchBlocked("DuckDuckGo asks for a captcha; try again in an hour or two")
        r.raise_for_status()
        out = []
        tree = HTMLParser(r.text)
        for node in tree.css("div.result"):
            a = node.css_first("a.result__a")
            if a is None:
                continue
            href = a.attributes.get("href") or ""
            if "uddg=" in href:
                href = unquote(parse_qs(urlsplit(href).query).get("uddg", [""])[0])
            if not href.startswith("http") or "duckduckgo.com/y.js" in href:
                continue  # ads
            snip = node.css_first(".result__snippet")
            out.append(Result(href, a.text(strip=True), snip.text(strip=True) if snip else ""))
        _remember(self.name, query, len(out))
        return out


@dataclass
class Place:
    id: str
    name: str
    address: str
    lat: float | None
    lon: float | None
    website: str | None
    phone: str | None
    rating: float | None
    rating_count: int | None
    types: list[str]


class GooglePlaces:
    name = "google_places"
    URL = "https://places.googleapis.com/v1/places:searchText"
    FIELDS = (
        "places.id,places.displayName,places.formattedAddress,places.location,"
        "places.websiteUri,places.nationalPhoneNumber,places.rating,places.userRatingCount,"
        "places.types,nextPageToken"
    )

    def __init__(self, key: str):
        self.client = httpx.Client(timeout=30)
        self.key = key

    def search(self, query: str, max_pages: int = 3) -> list[Place]:
        out: list[Place] = []
        body: dict = {
            "textQuery": query,
            "languageCode": "fr",
            "regionCode": "FR",
            "locationBias": {
                "circle": {
                    "center": {"latitude": settings.center_lat, "longitude": settings.center_lon},
                    "radius": min(50000.0, settings.radius_km * 1000),
                }
            },
        }
        for _ in range(max_pages):
            r = self.client.post(
                self.URL,
                json=body,
                headers={"X-Goog-Api-Key": self.key, "X-Goog-FieldMask": self.FIELDS},
            )
            r.raise_for_status()
            data = r.json()
            for p in data.get("places", []):
                loc = p.get("location") or {}
                out.append(
                    Place(
                        id=p["id"],
                        name=(p.get("displayName") or {}).get("text", ""),
                        address=p.get("formattedAddress", ""),
                        lat=loc.get("latitude"),
                        lon=loc.get("longitude"),
                        website=p.get("websiteUri"),
                        phone=p.get("nationalPhoneNumber"),
                        rating=p.get("rating"),
                        rating_count=p.get("userRatingCount"),
                        types=p.get("types", []),
                    )
                )
            token = data.get("nextPageToken")
            if not token:
                break
            body = {**body, "pageToken": token}
            time.sleep(2)
        _remember(self.name, query, len(out))
        return out
