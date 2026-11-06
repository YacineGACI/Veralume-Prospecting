"""One email in several languages: each lead gets its own language, and nobody gets an
email in a language the campaign isn't written in. Dry-run mailbox: nothing is sent."""

from sqlalchemy import select

from prospecting import campaigns as C
from prospecting import inbox, mailer
from prospecting.db import Campaign, Lead, Message
from tests.test_campaigns import client, draft, leads
from tests.test_campaigns import outside_sending_hours  # noqa: F401  (autouse: we write the batch)

FR = {"subject": "{prenom}, votre avis ?", "body": "Bonjour {prenom},\n\nFrançais.\n\n{signature}"}
DE = {"subject": "{prenom}, Ihre Meinung?", "body": "Hallo {prenom},\n\nDeutsch.\n\n{signature}"}


def _audience(cid: int) -> dict:
    """The live count the builder asks for: only the languages every version is written in."""
    langs = None
    for v in client.get(f"/api/campaigns/{cid}").json()["versions"]:
        mine = {v["language"]} | {k for k, x in v["translations"].items() if x["subject"].strip() and x["body"].strip()}
        langs = mine if langs is None else langs & mine
    r = client.post("/api/campaigns/audience", json={"kind": "photographer", "size": 50, "campaign_id": cid,
                                                     "languages": sorted(langs)})  # fmt: skip
    assert r.status_code == 200, r.text
    return r.json()


def _setup(db):
    fr = leads(db, 3)
    de = leads(db, 2, country="DE", language="de", city="München", postcode="80331")
    return fr, de, draft(size=50)


def test_leads_whose_language_isnt_written_are_left_out(db):
    fr, de, c = _setup(db)
    client.put(f"/api/campaigns/{c['id']}", json={"versions": [{"name": "A", **FR}]})
    a = _audience(c["id"])
    assert a["chosen"] == 3 and a["left_out"] == {"de": 2} and a["by_language"] == {"fr": 3, "de": 2}
    # A German translation with only a subject doesn't count yet.
    half = {"de": {"subject": DE["subject"], "body": ""}}
    client.put(f"/api/campaigns/{c['id']}", json={"versions": [{"name": "A", **FR, "translations": half}]})
    assert _audience(c["id"])["chosen"] == 3
    v = client.put(f"/api/campaigns/{c['id']}", json={"versions": [{"name": "A", **FR, "translations": {"de": DE}}]})
    assert v.json()["versions"][0]["translations"]["de"]["subject"] == DE["subject"]
    a = _audience(c["id"])
    assert a["chosen"] == 5 and a["left_out"] == {}


def test_each_lead_gets_its_own_language_with_the_footer_and_signature(db):
    fr, de, c = _setup(db)
    client.put("/api/settings", json={"signatures": {"de": "Yacine\nGründer von Veralume", "xx": "no"}})
    assert client.get("/api/settings").json()["signatures"] == {"de": "Yacine\nGründer von Veralume"}
    client.put(f"/api/campaigns/{c['id']}", json={"versions": [{"name": "A", **FR, "translations": {"de": DE}}]})
    assert client.post(f"/api/campaigns/{c['id']}/start").json()["leads"] == 5
    with db() as s:
        assert C.prepare(s, s.get(Campaign, c["id"])) == 5
        s.commit()
        msgs = {m.lead_id: m for m in s.scalars(select(Message).where(Message.campaign_id == c["id"]))}
        for lid in de:
            m = msgs[lid]
            assert m.language == "de" and m.subject.endswith("Ihre Meinung?") and "Hallo" in m.body
            assert "Gründer von Veralume" in m.body
            text = mailer.build(m, s.get(Lead, lid), s).get_body().get_content()
            assert "Antworten Sie „Stopp“" in text and "Ihre Adresse steht auf" in text
        for lid in fr:
            m = msgs[lid]
            assert m.language == "fr" and "Bonjour" in m.body and "Gründer" not in m.body
            assert "Répondez « stop »" in mailer.build(m, s.get(Lead, lid), s).get_body().get_content()
    res = client.get(f"/api/campaigns/{c['id']}").json()["results"]
    assert res["languages"]["fr"]["leads"] == 3 and res["languages"]["de"]["leads"] == 2


def test_with_two_versions_a_language_needs_both(db):
    fr, de, c = _setup(db)
    vs = [{"name": "A", **FR, "translations": {"de": DE}}, {"name": "B", **FR}]
    client.put(f"/api/campaigns/{c['id']}", json={"versions": vs})
    assert _audience(c["id"])["left_out"] == {"de": 2}
    with db() as s:
        camp = s.get(Campaign, c["id"])
        assert C.covered(C.versions(s, camp)) == {"fr"}
        assert len(C.audience(s, camp)[0]) == 3


def test_a_german_text_as_the_main_one(db):
    fr, de, c = _setup(db)
    client.put(f"/api/campaigns/{c['id']}", json={"versions": [{"name": "A", **DE, "language": "de"}]})
    assert _audience(c["id"])["chosen"] == 2
    bad = client.put(f"/api/campaigns/{c['id']}", json={"versions": [{"name": "A", **DE, "language": "xx"}]})
    assert bad.status_code == 422


def test_preview_and_stop_in_the_leads_language(db):
    fr, de, c = _setup(db)
    p = client.post("/api/templates/preview", json={**DE, "segment": "photographer", "language": "de"}).json()
    assert p["lead"]["id"] in de and p["subject"].endswith("Ihre Meinung?") and "Stopp" in p["footer"]
    for text in ("Stopp, bitte.", "Bitte keine weiteren E-Mails", "Afmelden aub", "Remove me please",
                 "Cancellami", "Darme de baja"):
        assert inbox._STOP.match(text), text
    assert not inbox._STOP.match("Hallo, gerne mehr Infos!")


def test_send_lead_by_lead_from_the_campaign_page(db):
    fr, de, c = _setup(db)
    url = f"/api/campaigns/{c['id']}/leads"
    assert client.post(f"{url}/{fr[0]}/email").status_code == 409  # a draft: start it first
    client.put(f"/api/campaigns/{c['id']}", json={"versions": [{"name": "A", **FR, "translations": {"de": DE}}]})
    client.post(f"/api/campaigns/{c['id']}/start")
    one = client.post(f"{url}/{de[0]}/email").json()
    assert one["lead"]["id"] == de[0] and one["message"]["language"] == "de"
    assert one["message"]["subject"].endswith("Ihre Meinung?") and one["message"]["status"] == "queued"
    again = client.post(f"{url}/{de[0]}/email").json()
    assert again["message"]["id"] == one["message"]["id"]  # never written twice
    assert client.post(f"/api/messages/{one['message']['id']}/send").json()["status"] == "sent"
    r = client.post(f"{url}/{de[0]}/email")
    assert r.status_code == 409 and "Already sent" in r.json()["detail"]
    states = {l["id"]: l["state"] for l in client.get(f"/api/campaigns/{c['id']}").json()["results"]["leads"]}
    assert states[de[0]] == "waiting" and states[fr[0]] == "not_sent"
    assert client.post(f"{url}/999999/email").status_code == 409
