from prospecting.enrich import Findings, LeadView, read_site
from prospecting.http import Page

SITE = "https://www.exemple-immo.fr/"
PAGES = {
    SITE: """<html><body>
      <a href="/contact">Contact</a> <a href="/mentions-legales">Mentions</a>
      <a href="/vente/appartement-strasbourg-12345">Bel appartement</a>
      <a href="https://www.facebook.com/exempleimmo">fb</a>
      <img src="/img/logo.png"><img src="/img/hero.jpg">
      <p>Achat, vente et gestion locative à Strasbourg</p>
      <p>Site réalisé par La Boîte Immo, contact@la-boite-immo.com</p>
    </body></html>""",
    SITE + "contact": """<html><body>
      <a href="mailto:contact@exemple-immo.fr">écrire</a> Tél : 03 88 00 00 00
    </body></html>""",
    SITE + "mentions-legales": """<html><body>
      Directeur de la publication : Jean Exemple. SIREN 123 456 789.
    </body></html>""",
    SITE + "vente/appartement-strasbourg-12345": """<html><body>
      <img src="/img/hero.jpg">
      <img data-src="https://cdn.exemple.fr/biens/12345/salon.jpg">
      <img srcset="/media/12345/cuisine-400.webp 400w, /media/12345/cuisine-1600.webp 1600w">
      Visite virtuelle disponible.
    </body></html>""",
}


class FakeFetcher:
    def get(self, url: str):
        text = PAGES.get(url)
        return None if text is None else Page(url, url, 200, "text/html", text)


def test_read_site_extracts_contacts_tags_and_listing_photos():
    lead = LeadView(
        1, "agency", "Agence Exemple", None, "123456789", "Strasbourg", "67000", SITE, "test"
    )
    f = Findings(1)
    read_site(lead, SITE, FakeFetcher(), f)  # type: ignore[arg-type]

    assert [e for e, _ in f.emails] == ["contact@exemple-immo.fr"]  # vendor footer dropped
    assert ("03 88 00 00 00", SITE + "contact") in f.phones
    assert f.people[0][:2] == ("Jean Exemple", "Directeur de la publication")
    assert f.socials["facebook"] == "https://www.facebook.com/exempleimmo"
    assert {"transaction", "management", "virtual_tour"} <= f.tags
    assert f.listings_seen == 1
    photos = [u for u, _ in f.photos]
    # Listing images first; the home page's own image is not taken as a listing photo.
    assert photos[:2] == [
        "https://cdn.exemple.fr/biens/12345/salon.jpg",
        "https://www.exemple-immo.fr/media/12345/cuisine-1600.webp",
    ]
    assert not any("logo" in u for u in photos)
