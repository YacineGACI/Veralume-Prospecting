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


def _area(code: str) -> str:
    return f'"ISO3166-1"="{code}"][admin_level=2'


# Any alphabet's capital, for Polish, Czech or Nordic towns (Łódź, Ørsted, Århus).
_TOWN_U = r"(?P<town>[^\W\d_](?:[^\W\d_]|['’.\- ]){1,40})"


def _pc_town(pc: str) -> re.Pattern[str]:
    """'postcode Town' where the postcode matches `pc`."""
    return re.compile(r"\b(?P<pc>" + pc + r")[\s,]+" + _TOWN_U)


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
        # --- the rest of Europe (2026-11-06) ---
        Country("AT", "Autriche", (".at",), _area("AT"), [
            ("at-de", ["Immobilienfotograf Österreich", "Immobilienfotografie Österreich"]
             + _towns("Immobilienfotograf", "Wien, Graz, Linz, Salzburg, Innsbruck, Klagenfurt, "
                      "Villach, Wels, St. Pölten, Dornbirn, Bregenz, Wiener Neustadt, Kitzbühel"))],
            _pc_town(r"(?:A-?\s?)?\d{4}")),
        Country("NL", "Pays-Bas", (".nl",), _area("NL"), [
            ("nl-nl", ["vastgoedfotograaf Nederland", "vastgoedfotografie makelaar"]
             + _towns("vastgoedfotograaf", "Amsterdam, Rotterdam, Den Haag, Utrecht, Eindhoven, "
                      "Groningen, Tilburg, Almere, Breda, Nijmegen, Haarlem, Arnhem, Amersfoort, "
                      "Maastricht, Zwolle, Leiden"))],
            _pc_town(r"\d{4}\s?[A-Z]{2}")),
        Country("IT", "Italie", (".it",), _area("IT"), [
            ("it-it", ["fotografo immobiliare", "fotografia immobiliare agenzie"]
             + _towns("fotografo immobiliare", "Milano, Roma, Torino, Napoli, Bologna, Firenze, "
                      "Genova, Verona, Venezia, Padova, Bari, Palermo, Brescia, Bergamo, Como, "
                      "Trieste, Cagliari, Catania, Pisa, Rimini, Bolzano"))],
            _pc_town(r"\d{5}")),
        Country("ES", "Espagne", (".es", ".cat"), _area("ES"), [
            ("es-es", ["fotógrafo inmobiliario", "fotografía inmobiliaria"]
             + _towns("fotógrafo inmobiliario", "Madrid, Barcelona, Valencia, Sevilla, Málaga, "
                      "Marbella, Alicante, Palma de Mallorca, Bilbao, Zaragoza, Murcia, Ibiza, "
                      "Las Palmas, Tenerife, San Sebastián, Granada, Valladolid"))],
            _pc_town(r"\d{5}")),
        Country("PT", "Portugal", (".pt",), _area("PT"), [
            ("pt-pt", ["fotógrafo imobiliário", "fotografia imobiliária"]
             + _towns("fotógrafo imobiliário", "Lisboa, Porto, Faro, Braga, Coimbra, Cascais, "
                      "Funchal, Aveiro, Setúbal, Albufeira, Lagos"))],
            _pc_town(r"\d{4}-\d{3}")),
        Country("GB", "Royaume-Uni", (".uk",), _area("GB"), [
            ("uk-en", ["property photographer UK", "estate agent photographer",
                       "real estate photographer UK"]
             + _towns("property photographer", "London, Manchester, Birmingham, Leeds, Bristol, "
                      "Edinburgh, Glasgow, Liverpool, Sheffield, Newcastle, Nottingham, Cardiff, "
                      "Brighton, Oxford, Cambridge, Bath, Southampton, Belfast, Leicester, York"))],
            # 'Bristol BS1 4DJ': town first, then the postcode.
            re.compile(r"(?P<town>[A-Z][A-Za-z'’\- ]{1,30}?)[\s,]+"
                       r"(?P<pc>[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2})\b")),
        Country("IE", "Irlande", (".ie",), _area("IE"), [
            ("ie-en", ["property photographer Ireland", "property photography Ireland"]
             + _towns("property photographer", "Dublin, Cork, Galway, Limerick, Waterford, "
                      "Kilkenny, Wexford, Sligo"))]),
        Country("MC", "Monaco", (".mc",), _area("MC"), [
            ("fr-fr", ["photographe immobilier Monaco", "photographe immobilier Monte-Carlo"])],
            _pc_town(r"980\d{2}")),
        Country("SE", "Suède", (".se",), _area("SE"), [
            ("se-sv", ["mäklarfotograf", "fastighetsfotograf Sverige"]
             + _towns("fastighetsfotograf", "Stockholm, Göteborg, Malmö, Uppsala, Västerås, "
                      "Örebro, Linköping, Helsingborg, Norrköping, Lund, Umeå"))],
            _pc_town(r"\d{3}\s?\d{2}")),
        Country("DK", "Danemark", (".dk",), _area("DK"), [
            ("dk-da", ["boligfotograf Danmark", "ejendomsfotograf"]
             + _towns("boligfotograf", "København, Aarhus, Odense, Aalborg, Esbjerg, Randers, "
                      "Kolding, Horsens, Vejle, Roskilde"))],
            _pc_town(r"(?:DK-?\s?)?\d{4}")),
        Country("NO", "Norvège", (".no",), _area("NO"), [
            ("no-no", ["boligfotograf Norge", "eiendomsfotograf"]
             + _towns("boligfotograf", "Oslo, Bergen, Trondheim, Stavanger, Kristiansand, "
                      "Drammen, Fredrikstad, Tromsø, Sandnes"))],
            _pc_town(r"\d{4}")),
        Country("FI", "Finlande", (".fi",), _area("FI"), [
            ("fi-fi", ["asuntokuvaaja", "kiinteistökuvaaja"]
             + _towns("asuntokuvaaja", "Helsinki, Espoo, Tampere, Vantaa, Oulu, Turku, "
                      "Jyväskylä, Lahti, Kuopio"))],
            _pc_town(r"\d{5}")),
        Country("PL", "Pologne", (".pl",), _area("PL"), [
            ("pl-pl", ["fotograf nieruchomości", "fotografia nieruchomości"]
             + _towns("fotograf nieruchomości", "Warszawa, Kraków, Wrocław, Łódź, Poznań, Gdańsk, "
                      "Szczecin, Bydgoszcz, Lublin, Katowice, Gdynia, Sopot"))],
            _pc_town(r"\d{2}-\d{3}")),
        Country("CZ", "Tchéquie", (".cz",), _area("CZ"), [
            ("cz-cs", ["fotograf nemovitostí", "fotografie nemovitostí"]
             + _towns("fotograf nemovitostí", "Praha, Brno, Ostrava, Plzeň, Liberec, Olomouc, "
                      "České Budějovice, Hradec Králové"))],
            _pc_town(r"\d{3}\s?\d{2}")),
    ]
}  # fmt: skip
FOREIGN = [c for c in COUNTRIES if c != "FR"]
# The countries done first; `prospect abroad --europe` does the others.
FIRST_WAVE = ["BE", "LU", "CH", "DE", "CA"]
EUROPE = [c for c in FOREIGN if c not in FIRST_WAVE]


def country_of_url(url: str | None) -> str | None:
    """The country a site's domain names, if any (.be → BE)."""
    from prospecting.text import host

    h = host(url or "")
    for c in COUNTRIES.values():
        if any(h.endswith(t) for t in c.tlds):
            return c.code
    return None


_PREFIXES = (("+352", "LU"), ("+32", "BE"), ("+41", "CH"), ("+49", "DE"), ("+1", "CA"),
             ("+33", "FR"), ("+43", "AT"), ("+31", "NL"), ("+39", "IT"), ("+34", "ES"),
             ("+351", "PT"), ("+44", "GB"), ("+353", "IE"), ("+377", "MC"), ("+46", "SE"),
             ("+45", "DK"), ("+47", "NO"), ("+358", "FI"), ("+48", "PL"), ("+420", "CZ"))  # fmt: skip


def country_of_phone(number: str) -> str | None:
    """+32 … → BE. French numbers are written 0X XX XX XX XX."""
    for prefix, code in _PREFIXES:
        if number.startswith(prefix):
            return code
    return "FR" if re.match(r"0\d ", number) else None
