from prospecting.enrich import guess_domains
from prospecting.text import (
    email_belongs,
    email_is_personal,
    find_emails,
    find_phones,
    name_similarity,
    network_of,
    pretty_name,
)


def test_find_phones_formats_and_dedupes():
    text = "Tél. 03.88.00.00.00 ou +33 (0)6 12 34 56 78, fax 0388000000, SIRET 65850050900012"
    assert find_phones(text) == ["03 88 00 00 00", "06 12 34 56 78"]


def test_find_emails_skips_images_and_placeholders():
    text = "contact@exemple-immo.fr logo@2x.png votre@email.fr prenom.nom@exemple.fr Jean.Dupont@Exemple-Immo.fr"
    assert find_emails(text) == ["contact@exemple-immo.fr", "jean.dupont@exemple-immo.fr"]


def test_email_belongs_drops_vendor_addresses():
    site = "https://www.exemple-immo.fr/"
    assert email_belongs("contact@exemple-immo.fr", site)
    assert email_belongs("agence.exemple@orange.fr", site)
    assert not email_belongs("contact@la-boite-immo.com", site)


def test_personal_email():
    assert email_is_personal("jean.dupont@exemple.fr")
    assert not email_is_personal("contact@exemple.fr")


def test_names():
    assert pretty_name("AGENCE IMMOBILIERE DE LA ROBERTSAU") == "Agence Immobiliere de la Robertsau"
    assert name_similarity("Agence Immobilière Baumann", "BAUMANN IMMO SARL") == 1.0
    assert name_similarity("Orpi Strasbourg", "Century 21 Schiltigheim") == 0.0


def test_network_detection():
    assert network_of("https://www.century21brasseurs.com/") is None
    assert network_of("https://immobilier-haguenau.nestenn.com/") == "Nestenn"
    assert network_of(None, "SQUARE HABITAT STRASBOURG") == "Square Habitat"


def test_guess_domains():
    domains = guess_domains("Cabinet Immobilier Dromson")
    assert "dromson-immobilier.com" in domains
    assert "dromson.fr" in domains
    assert guess_domains("SCI") == []
