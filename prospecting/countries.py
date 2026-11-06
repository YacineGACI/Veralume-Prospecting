"""Countries we prospect in: France first, then real-estate photographers in Belgium,
Luxembourg, Switzerland, Germany and Quebec.

Every lead has a `country` (ISO code; "CA" leads are all in Quebec). Each foreign country
says where to look in OpenStreetMap, which DuckDuckGo region and words to search with
(the local languages), and how its addresses are written, so a site's "postcode Town"
can be located.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Country:
    code: str
    name: str
    tlds: tuple[str, ...] = ()
    osm_area: str = ""  # an Overpass area filter
    # (DuckDuckGo region, queries)
    searches: list[tuple[str, list[str]]] = field(default_factory=list)
    # 'postcode Town' on a site; groups "pc" and "town".
    postcode: re.Pattern[str] | None = None


def _towns(term: str, towns: str) -> list[str]:
    return [f"{term} {t.strip()}" for t in towns.split(",")]


_TOWN = r"(?P<town>[A-ZÀ-ÝÄÖÜ][A-Za-zÀ-ÿÄÖÜäöüß'’.\- ]{1,40})"

COUNTRIES: dict[str, Country] = {
    c.code: c
    for c in [
        Country("FR", "France", (".fr",)),
        Country(
            "BE", "Belgique", (".be",), '"ISO3166-1"="BE"][admin_level=2',
            [
                ("be-fr", ["photographe immobilier Belgique", "photographe immobilier Wallonie",
                           "photographe immobilier indépendant Belgique"]
                 + _towns("photographe immobilier", "Bruxelles, Liège, Namur, Charleroi, Mons, "
                          "Wavre, Waterloo, Nivelles, Louvain-la-Neuve, Tournai, Arlon, Verviers, "
                          "Huy, Mouscron, Braine-l'Alleud, Marche-en-Famenne")),
                ("be-nl", ["vastgoedfotograaf België", "vastgoedfotografie Vlaanderen"]
                 + _towns("vastgoedfotograaf", "Antwerpen, Gent, Brugge, Leuven, Hasselt, "
                          "Mechelen, Kortrijk, Aalst, Sint-Niklaas, Genk, Oostende, Roeselare")),
            ],
            re.compile(r"\b(?:B-?\s?)?(?P<pc>[1-9]\d{3})[\s,]+" + _TOWN),
        ),
        Country(
            "LU", "Luxembourg", (".lu",), '"ISO3166-1"="LU"][admin_level=2',
            [
                ("fr-fr", ["photographe immobilier Luxembourg", "photographe immobilier Grand-Duché",
                           "photographe immobilier Esch-sur-Alzette",
                           "photographe architecture intérieur Luxembourg"]),
                ("de-de", ["Immobilienfotograf Luxemburg", "Immobilienfotografie Luxemburg"]),
            ],
            re.compile(r"\bL-?\s?(?P<pc>\d{4})[\s,]+" + _TOWN),
        ),
        Country(
            "CH", "Suisse", (".ch",), '"ISO3166-1"="CH"][admin_level=2',
            [
                ("ch-fr", ["photographe immobilier Suisse romande"]
                 + _towns("photographe immobilier", "Genève, Lausanne, Neuchâtel, Fribourg, Sion, "
                          "Montreux, Nyon, Yverdon-les-Bains, La Chaux-de-Fonds, Delémont, Vevey, "
                          "Morges, Bulle, Martigny")),
                ("ch-de", ["Immobilienfotograf Schweiz"]
                 + _towns("Immobilienfotograf", "Zürich, Basel, Bern, Luzern, St. Gallen, "
                          "Winterthur, Zug, Aarau, Biel, Thun, Chur, Schaffhausen")),
                ("ch-it", ["fotografo immobiliare Ticino", "fotografo immobiliare Lugano"]),
            ],
            re.compile(r"\b(?:CH-?\s?)?(?P<pc>[1-9]\d{3})[\s,]+" + _TOWN),
        ),
        Country(
            "DE", "Allemagne", (".de",), '"ISO3166-1"="DE"][admin_level=2',
            [
                ("de-de", ["Immobilienfotograf", "Immobilienfotografie Deutschland",
                           "Immobilienfotograf selbstständig", "Immobilienfotografie Makler"]
                 + _towns("Immobilienfotograf", "Berlin, Hamburg, München, Köln, Frankfurt, "
                          "Stuttgart, Düsseldorf, Leipzig, Dortmund, Essen, Bremen, Dresden, "
                          "Hannover, Nürnberg, Duisburg, Bochum, Wuppertal, Bielefeld, Bonn, "
                          "Münster, Mannheim, Karlsruhe, Augsburg, Wiesbaden, Mainz, Freiburg, "
                          "Heidelberg, Saarbrücken, Kiel, Rostock, Potsdam, Regensburg, "
                          "Würzburg, Ulm, Konstanz, Offenburg, Baden-Baden, Trier, Kaiserslautern, "
                          "Lübeck, Erfurt, Kassel, Aachen")),
            ],
            re.compile(r"\b(?:D-?\s?)?(?P<pc>\d{5})[\s,]+" + _TOWN),
        ),
        Country(
            "CA", "Québec", (".qc.ca", ".ca"), '"ISO3166-2"="CA-QC"',
            [
                ("ca-fr", ["photographe immobilier Québec", "photographie immobilière Québec",
                           "photographe immobilier courtier Québec"]
                 + _towns("photographe immobilier", "Montréal, Québec, Laval, Gatineau, Longueuil, "
                          "Sherbrooke, Trois-Rivières, Lévis, Saguenay, Terrebonne, Saint-Jérôme, "
                          "Drummondville, Granby, Rimouski, Blainville, Repentigny, "
                          "Saint-Jean-sur-Richelieu, Brossard, Mirabel, Rive-Sud")),
            ],
            # 'Montréal (Québec) H2X 1Y4' or 'Montréal, QC H2X 1Y4': town first, then postcode.
            re.compile(
                r"(?P<town>[A-ZÀ-Ý][A-Za-zÀ-ÿ'’\- ]{1,40}?)[\s,]+(?:\(Qu[ée]bec\)|QC|Qu[ée]bec)[\s,]*"
                r"(?P<pc>[A-Z]\d[A-Z]\s?\d[A-Z]\d)\b"
            ),
        ),
    ]
}  # fmt: skip
FOREIGN = [c for c in COUNTRIES if c != "FR"]


def country_of_url(url: str | None) -> str | None:
    """The country a site's domain names, if any (.be → BE)."""
    from prospecting.text import host

    h = host(url or "")
    for c in COUNTRIES.values():
        if any(h.endswith(t) for t in c.tlds):
            return c.code
    return None


_PREFIXES = (("+352", "LU"), ("+32", "BE"), ("+41", "CH"), ("+49", "DE"), ("+1", "CA"),
             ("+33", "FR"))  # fmt: skip


def country_of_phone(number: str) -> str | None:
    """+32 … → BE. French numbers are written 0X XX XX XX XX."""
    for prefix, code in _PREFIXES:
        if number.startswith(prefix):
            return code
    return "FR" if re.match(r"0\d ", number) else None
