"""Leads abroad: each one has its country, the lists and campaigns filter on it, and
foreign phones and addresses are read the way each country writes them."""

from fastapi.testclient import TestClient

from prospecting import campaigns as C
from prospecting.api.app import app
from prospecting.countries import COUNTRIES, country_of_phone
from prospecting.db import Lead
from prospecting.sources.abroad import _name, _site_country
from prospecting.text import find_phones

client = TestClient(app)


def _lead(db, **kw) -> int:
    with db() as s:
        lead = Lead(
            **{"kind": "photographer", "name": "Studio", "status": "new", "do_not_contact": False,
               "emails": [{"value": "hello@studio.example", "source": "x"}], "phones": [],
               "people": [], "tags": [], "sources": [], **kw}
        )  # fmt: skip
        s.add(lead)
        s.commit()
        return lead.id


def test_leads_are_french_unless_told_otherwise(db):
    lid = _lead(db, name="Studio Colmar", postcode="68000")
    r = client.get(f"/api/leads/{lid}").json()
    assert r["country"] == "FR" and r["dep"] == "68" and r["id_"]["dep_name"] == "Haut-Rhin"


def test_a_german_postcode_is_not_a_french_departement(db):
    fr = _lead(db, name="Studio Strasbourg", postcode="67000")
    de = _lead(db, name="Studio Ludwigshafen", postcode="67059", country="DE")
    r = client.get(f"/api/leads/{de}").json()
    assert r["dep"] is None and r["id_"]["dep_name"] is None and r["id_"]["country"] == "DE"
    ids = [x["id"] for x in client.get("/api/leads?kind=photographer&dep=67").json()["items"]]
    assert fr in ids and de not in ids
    with db() as s:
        got = {l.id for l in C.candidates(s, "photographer", {"deps": ["67"]})}
    assert fr in got and de not in got


def test_filter_by_country_in_lists_and_campaigns(db):
    fr = _lead(db, name="Studio Nancy")
    be = _lead(db, name="Studio Liège", country="BE", city="Liège")
    ch = _lead(db, name="Studio Genève", country="CH", city="Genève")
    ids = [x["id"] for x in client.get("/api/leads?kind=photographer&country=BE").json()["items"]]
    assert ids == [be]
    meta = client.get("/api/meta").json()
    assert meta["by_country"]["BE"]["photographer"] == 1
    assert meta["by_country"]["FR"]["photographer"] == 1
    with db() as s:
        got = {l.id for l in C.candidates(s, "photographer", {"countries": ["BE", "CH"]})}
        facets = C.facets(s, C.candidates(s, "photographer", {}))
    assert got == {be, ch} and fr not in got
    assert facets["countries"] == {"FR": 1, "BE": 1, "CH": 1}


def test_foreign_phones_are_written_internationally():
    assert find_phones("Tel. 0171 1234567", "DE") == ["+49 171 1234567"]
    assert find_phones("GSM 0471 12 34 56", "BE") == ["+32 471 12 34 56"]
    assert find_phones("Mobile 079 123 45 67", "CH") == ["+41 79 123 45 67"]
    assert find_phones("(514) 555-1234", "CA") == ["+1 514-555-1234"]
    assert find_phones("03 88 12 34 56") == ["03 88 12 34 56"]  # France unchanged
    assert country_of_phone("+352 621 123 456") == "LU"
    assert country_of_phone("03 88 12 34 56") == "FR"


def test_a_site_says_its_country_and_france_and_others_are_left_out():
    assert _site_country("https://studio-licht.de/", "CH") == "DE"
    assert _site_country("https://studio.photo/", "BE") == "BE"
    assert _site_country("https://foto.at/", "DE") == "AT"
    assert _site_country("https://foto.hu/", "AT") is None  # Hungary: not prospected
    assert _site_country("https://studio.fr/", "BE") is None  # France is collected separately
    assert _site_country("https://studio.qc.ca/", "CA") == "CA"
    assert _site_country("https://licht.berlin/", "DE") == "DE"


def test_names_skip_the_search_words():
    assert _name("Immobilienfotograf München | Licht & Raum", "https://x.de") == "Licht & Raum"
    assert _name("Photographe immobilier Liège - Accueil", "https://studio-clair.be") == "Studio Clair"


def test_addresses_each_country_writes_them():
    cases = {"DE": ("Musterstr. 1\n80331 München", "80331", "München"),
             "BE": ("Rue X 5, 4000 Liège", "4000", "Liège"),
             "LU": ("L-1234 Luxembourg", "1234", "Luxembourg"),
             "CH": ("Rue Y 2\n1201 Genève", "1201", "Genève"),
             "CA": ("123 rue X, Montréal (Québec) H2X 1Y4", "H2X 1Y4", "Montréal")}  # fmt: skip
    for code, (text, pc, town) in cases.items():
        m = COUNTRIES[code].postcode.search(text)
        assert m and m.group("pc") == pc and m.group("town").strip() == town, code


def test_language_from_the_region():
    from prospecting.languages import from_html_lang, from_region

    assert from_region("FR", "67000") == "fr"
    assert from_region("BE", "9000") == "nl"  # Gent
    assert from_region("BE", "4000") == "fr"  # Liège
    assert from_region("BE", "4700") == "de"  # Eupen
    assert from_region("BE", None, "duckduckgo: vastgoedfotograaf Gent") == "nl"
    assert from_region("CH", "1201") == "fr" and from_region("CH", "8001") == "de"
    assert from_region("CH", "6900") == "it"  # Lugano
    assert from_region("DE", "80331") == "de" and from_region("CA", None) == "fr"
    assert from_html_lang("de-CH", "CH") == "de"
    assert from_html_lang("en-US", "DE") is None  # a translation: they read German
    assert from_html_lang("en", "CA") == "en"  # an English-speaking Montrealer


def test_you_choose_the_language_and_filter_on_it(db):
    from prospecting import enrich

    nl = _lead(db, name="Studio Gent", country="BE", language="nl", language_source="region")
    fr = _lead(db, name="Studio Namur", country="BE", language="fr", language_source="region")
    r = client.patch(f"/api/leads/{fr}", json={"language": "de"})
    assert r.status_code == 200 and r.json()["language"] == "de"
    assert r.json()["language_source"] == "you"
    assert client.patch(f"/api/leads/{fr}", json={"language": "xx"}).status_code == 422
    enrich.apply(enrich.Findings(fr, html_lang="fr-BE"))  # the site never overrides you
    enrich.apply(enrich.Findings(nl, html_lang="fr-BE"))  # but corrects a guess
    ids = [x["id"] for x in client.get("/api/leads?kind=photographer&language=de").json()["items"]]
    assert ids == [fr]
    with db() as s:
        got = {l.id: l.language for l in C.candidates(s, "photographer", {"languages": ["fr"]})}
    assert got == {nl: "fr"}


def test_an_absurd_photo_address_is_a_failed_download_not_a_crash():
    from prospecting.http import Fetcher

    f = Fetcher()
    assert f.get_bytes("https://example.com/" + "a" * 70_000) is None
    assert f.get("https://example.com/" + "a" * 70_000) is None


def test_the_rest_of_europe():
    from prospecting.countries import EUROPE
    from prospecting.languages import from_html_lang, from_region

    assert {"AT", "NL", "IT", "ES", "PT", "GB", "IE", "PL"} <= set(EUROPE)
    assert from_region("NL", "1012 AB") == "nl" and from_region("PL", "00-001") == "pl"
    assert from_html_lang("en-GB", "GB") == "en" and from_html_lang("nb-NO", "NO") == "no"
    assert from_html_lang("ca", "ES") == "es"  # Catalan site: a Spanish email reaches them
    assert find_phones("Tel. 0612345678", "NL") == ["+31 6 12345678"]
    assert find_phones("07911 123456", "GB") == ["+44 7911 123456"]
    assert _site_country("https://fotostudio.pl/", "CZ") == "PL"
    assert _name("Fotograf nieruchomości Kraków | Jan Nowak Studio", "https://x.pl") == "Jan Nowak Studio"
