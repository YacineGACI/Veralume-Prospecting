from prospecting.sources.directory import parse_profile, slugify

PROFILE = """<html><head><title>Studio Exemple : Jean DUPONT à Lyon</title></head><body>
<h1 class="worker-name">Jean DUPONT</h1>
<a itemprop="url" data-stats="clic_btn_siteInternet" href="https://www.studio-exemple.fr">Site</a>
<a href="https://www.instagram.com/studioexemple/">ig</a>
<a href="https://www.linkedin.com/company/trouver-mon-photographe">directory</a>
</body></html>"""


def test_parse_profile():
    p = parse_profile(PROFILE, "https://www.trouver-mon-photographe.fr/fr/photographe/1-x")
    assert (p["business"], p["person"], p["town"]) == ("Studio Exemple", "Jean DUPONT", "Lyon")
    assert p["website"] == "https://www.studio-exemple.fr"
    assert p["socials"] == {"instagram": "https://www.instagram.com/studioexemple/"}


def test_slugify():
    assert slugify("Saint-Étienne") == "saint-etienne"
    assert slugify("Fort-de-France") == "fort-de-france"
