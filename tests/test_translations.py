"""Every template in many languages: our two texts get their 12 translations, a waiting
email can be rewritten in another language, a started campaign can gain languages.
Dry-run mailbox: nothing is sent."""

from sqlalchemy import select

from prospecting import mailer, translations
from prospecting.db import Lead, Template
from tests.test_campaigns import client, draft, leads
from tests.test_campaigns import outside_sending_hours  # noqa: F401  (autouse)

FR_SUBJ, FR_BODY = translations.FIRST_EMAIL["fr"]


def test_every_language_has_both_texts_and_the_placeholders():
    from prospecting.languages import LANGUAGES

    assert set(translations.FIRST_EMAIL) == set(LANGUAGES) == set(translations.FOLLOWUP)
    assert set(translations.SIGNATURE) == set(LANGUAGES)
    for lang, (subj, body) in translations.FIRST_EMAIL.items():
        assert subj.strip() and "{prenom}" in body and body.endswith("{signature}"), lang
    for lang, body in translations.FOLLOWUP.items():
        assert "{prenom}" in body and body.endswith("{signature}"), lang


def test_our_french_texts_get_their_translations_once(db):
    with db() as s:
        mailer.set_setting(s, "signature", "Yacine\nCréateur de Veralume")
        first = Template(key="t1", name="Premier", segment="photographer", step=0, subject=FR_SUBJ,
                         body=FR_BODY.replace("d'objets", "d’objets"), language="fr", translations={})  # fmt: skip
        fu = Template(key="t2", name="Relance", step=1, subject="Re:", body=translations.FOLLOWUP["fr"],
                      language="fr", translations={"de": {"subject": "Re:", "body": "Mein eigener Text"}})  # fmt: skip
        other = Template(key="t3", name="Autre", step=0, subject="Autre", body="Bonjour", language="fr")
        s.add_all([first, fu, other])
        s.commit()
        assert translations.fill_known(s) == {"templates": 2, "signatures": 12}
        s.commit()
        assert len(first.translations) == 12 and first.translations["de"]["subject"].startswith("Automatische")
        assert fu.translations["de"]["body"] == "Mein eigener Text"  # yours is kept
        assert fu.translations["it"]["body"].startswith("Buongiorno") and not other.translations
        assert mailer.signature(s, "de") == "Yacine\nGründer von Veralume"
        # Removed by hand: it doesn't come back.
        first.translations = {k: v for k, v in first.translations.items() if k != "pl"}
        s.commit()
        assert translations.fill_known(s) == {"templates": 0, "signatures": 0}
        assert "pl" not in first.translations


def test_a_new_campaigns_followup_is_in_every_language(db):
    c = draft()
    assert len(c["followup"]["translations"]) == 12
    assert c["followup"]["translations"]["es"].startswith("Hola")
    assert len(c["followup_default"]["translations"]) == 12


def test_general_templates_keep_their_languages(db):
    r = client.post("/api/templates", json={"name": "Réponse", "step": -1, "subject": "Merci", "body": "Merci {prenom}",
                                            "language": "fr", "translations": {"de": {"subject": "Danke", "body": "Danke {prenom}"},
                                                                               "xx": {"subject": "?", "body": "?"}}})  # fmt: skip
    assert r.status_code == 200 and r.json()["translations"] == {"de": {"subject": "Danke", "body": "Danke {prenom}"}}
    assert client.post("/api/templates", json={"name": "X", "subject": "a", "body": "b", "language": "xx"}).status_code == 422
    de = leads(db, 1, country="DE", language="de", city="Berlin", postcode="10115")[0]
    c = client.get(f"/api/compose?lead_id={de}&template_id={r.json()['id']}").json()
    assert c["language"] == "de" and c["body"].startswith("Danke") and c["languages"] == ["fr", "de"]
    c = client.get(f"/api/compose?lead_id={de}&template_id={r.json()['id']}&language=fr").json()
    assert c["language"] == "fr" and c["body"].startswith("Merci") and "stop" in c["footer"]


def _started(db):
    fr = leads(db, 2)
    de = leads(db, 2, country="DE", language="de", city="München", postcode="80331")
    c = draft(size=50)
    v = {"name": "A", "subject": FR_SUBJ, "body": FR_BODY, "language": "fr", "asset_ids": [], "attach_photo": False}
    client.put(f"/api/campaigns/{c['id']}", json={"versions": [v]})
    assert client.post(f"/api/campaigns/{c['id']}/start").json()["leads"] == 2  # French only
    return c["id"], fr, de, {**v, "id": client.get(f"/api/campaigns/{c['id']}").json()["versions"][0]["id"]}


def test_a_waiting_email_can_be_rewritten_in_another_language(db):
    cid, fr, de, v = _started(db)
    m = client.post(f"/api/campaigns/{cid}/leads/{fr[0]}/email").json()["message"]
    assert client.get(f"/api/messages/{m['id']}/languages").json()["languages"] == ["fr"]
    assert client.post(f"/api/messages/{m['id']}/language", json={"language": "de"}).status_code == 409
    client.put(f"/api/campaigns/{cid}", json={"versions": [{**v, "translations": translations.first_email_translations()}]})
    langs = client.get(f"/api/messages/{m['id']}/languages").json()
    assert len(langs["languages"]) == 13 and langs["lead_language"] == "fr"
    r = client.post(f"/api/messages/{m['id']}/language", json={"language": "de"}).json()
    assert r["language"] == "de" and r["subject"].startswith("Automatische") and r["body"].startswith("Hallo")
    with db() as s:
        from prospecting.db import Message

        msg = s.get(Message, m["id"])
        assert "Stopp" in mailer.build(msg, s.get(Lead, fr[0]), s).get_body().get_content()


def test_a_started_campaign_can_gain_languages_but_not_change_its_email(db):
    cid, fr, de, v = _started(db)
    r = client.put(f"/api/campaigns/{cid}", json={"versions": [{**v, "translations": translations.first_email_translations()}]})
    assert r.status_code == 200 and len(r.json()["versions"][0]["translations"]) == 12
    assert client.put(f"/api/campaigns/{cid}", json={"versions": [{**v, "subject": "Autre"}]}).status_code == 409
    added = client.post(f"/api/campaigns/{cid}/more", json={"n": 10}).json()["added"]
    assert added == 2  # the German photographers can join now
    with db() as s:
        t = s.scalars(select(Template).where(Template.id == v["id"])).one()
        assert t.subject == FR_SUBJ
