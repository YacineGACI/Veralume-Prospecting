"""The language to write to each lead in: French, German, Dutch, Italian or English.

In order of trust:
1. you set it in the lead's file (`language_source` = "you"), never overwritten;
2. the website says it (`<html lang="de-CH">`), read during enrichment;
3. the region: the country, then the postcode where a country has several languages
   (Flanders and Brussels in Belgium, the Swiss language areas), or the search that
   found the lead ("vastgoedfotograaf Gent" → Dutch).
"""

from __future__ import annotations

import re
from collections.abc import Callable

from sqlalchemy import or_, select

from prospecting.db import Lead, session

LANGUAGES = {"fr": "French", "de": "German", "nl": "Dutch", "it": "Italian", "en": "English",
             "es": "Spanish", "pt": "Portuguese", "sv": "Swedish", "da": "Danish",
             "no": "Norwegian", "fi": "Finnish", "pl": "Polish", "cs": "Czech"}  # fmt: skip
_DEFAULT = {"FR": "fr", "BE": "fr", "LU": "fr", "CH": "de", "DE": "de", "CA": "fr",
            "AT": "de", "NL": "nl", "IT": "it", "ES": "es", "PT": "pt", "GB": "en", "IE": "en",
            "MC": "fr", "SE": "sv", "DK": "da", "NO": "no", "FI": "fi", "PL": "pl", "CZ": "cs"}  # fmt: skip
_ALIASES = {"nb": "no", "nn": "no", "ca": "es", "eu": "es", "gl": "es"}  # Bokmål, Catalan…
# Words of the searches that found the lead, by language.
_SEARCH_WORDS = [("nl", r"vastgoed"), ("de", r"immobilienfoto|luxemburg\b"), ("it", r"fotografo"),
                 ("fr", r"photographe|photographie")]  # fmt: skip


def _by_postcode(country: str, postcode: str | None) -> str | None:
    pc = re.sub(r"\D", "", postcode or "")
    if len(pc) != 4:
        return None
    n = int(pc)
    if country == "BE":
        if 1500 <= n <= 3999 or 8000 <= n <= 9999:
            return "nl"  # Flemish Brabant, Antwerp, Limburg, the Flanders
        if 4700 <= n <= 4799:
            return "de"  # Eupen and the German-speaking East
        return "fr"  # Brussels (mostly French-speaking), Wallonia
    if country == "CH":
        if 6500 <= n <= 6999:
            return "it"  # Ticino
        if 1000 <= n <= 2999 and not 2500 <= n <= 2599:  # Biel/Bienne area: German first
            return "fr"  # Romandie: Geneva, Vaud, Neuchâtel, Jura, Fribourg, Valais (west)
        return "de"
    return None


def from_region(country: str | None, postcode: str | None, found_by: str | None = None) -> str:
    country = country or "FR"
    if lang := _by_postcode(country, postcode):
        return lang
    low = (found_by or "").lower()
    if country != "FR" and low:
        for lang, pat in _SEARCH_WORDS:
            if re.search(pat, low):
                return lang
    return _DEFAULT.get(country, "fr")


def of_lead(lead: Lead) -> str:
    """The language to write to this lead in."""
    if lead.language in LANGUAGES:
        return lead.language
    return from_region(lead.country, lead.postcode, lead.website_source)


def from_html_lang(value: str | None, country: str | None) -> str | None:
    """'de-CH' → 'de'. English counts only in Quebec, the UK and Ireland: elsewhere an
    English site is usually a translation, and the owner reads the local language.
    Catalan, Basque and Galician sites count as Spanish (a Spanish email reaches them)."""
    code = (value or "").strip().lower()[:2]
    code = _ALIASES.get(code, code)
    if code not in LANGUAGES:
        return None
    if code == "en" and (country or "FR") not in ("CA", "GB", "IE"):
        return None
    return code


def fill(progress: Callable[[str], None] = print) -> int:
    """Give a language to every lead without one (from the region; enrichment refines it
    from the website)."""
    n = 0
    with session() as s:
        for lead in s.scalars(select(Lead).where(or_(Lead.language.is_(None), Lead.language == ""))):
            found_by = lead.website_source or " ".join(x.get("kind", "") for x in lead.sources or [])
            lead.language = from_region(lead.country, lead.postcode, found_by)
            lead.language_source = "region"
            n += 1
        s.commit()
    progress(f"languages: {n} leads given a language from their region")
    return n


def read_sites(progress: Callable[[str], None] = print) -> int:
    """For leads abroad whose language only comes from the region, read the language
    their home page declares. One request per site, polite and cached."""
    from concurrent.futures import ThreadPoolExecutor

    from selectolax.lexbor import LexborHTMLParser as HTMLParser

    from prospecting.http import Fetcher

    with session() as s:
        todo = [
            (l.id, l.website, l.country)
            for l in s.scalars(
                select(Lead).where(
                    Lead.country.is_not(None), Lead.country != "FR", Lead.website.is_not(None),
                    or_(Lead.language_source.is_(None), Lead.language_source == "region"),
                )
            )
        ]
    fetcher = Fetcher()

    def one(item: tuple[int, str, str]) -> tuple[int, str | None]:
        lead_id, site, country = item
        try:
            page = fetcher.get(site)
        except Exception:
            return lead_id, None
        root = HTMLParser(page.text).css_first("html") if page else None
        return lead_id, from_html_lang(root.attributes.get("lang") if root else None, country)

    changed = 0
    with ThreadPoolExecutor(8) as ex:
        found = list(ex.map(one, todo))
    fetcher.close()
    with session() as s:
        for lead_id, lang in found:
            lead = s.get(Lead, lead_id)
            if lead and lang and lead.language_source != "you":
                changed += lead.language != lang
                lead.language, lead.language_source = lang, "site"
        s.commit()
    progress(f"languages: {len(todo)} sites read, {changed} languages corrected")
    return changed
