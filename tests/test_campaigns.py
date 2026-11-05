"""Campaigns: who, how many, the email versions, the daily batch, the results, the winner;
and dossiers. Dry-run mailbox: nothing is ever sent."""

import io

import pytest
from datetime import date, timedelta

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from prospecting import campaigns as C
from prospecting import inbox, mailer
from prospecting.api.app import app
from prospecting.db import Campaign, CampaignLead, Lead, Message
from tests.test_pilot import jpeg

client = TestClient(app)
FIRST = ["Claire", "Lucas", "Inès", "Marc", "Julie", "Hugo", "Léa", "Paul"]


@pytest.fixture(autouse=True)
def outside_sending_hours(monkeypatch):
    """Starting a campaign inside sending hours writes today's batch at once; the tests
    write it themselves."""
    monkeypatch.setattr(mailer, "in_window", lambda dt=None: False)


def leads(db, n=6, **kw) -> list[int]:
    ids = []
    with db() as s:
        for i in range(n):
            base = dict(
                kind="photographer", name=f"Studio {i}", city="Colmar", status="new",
                postcode="68000" if i % 2 else "67000", distance_km=20 + i * 10,
                emails=[{"value": f"contact@studio{i}.fr", "source": f"https://studio{i}.fr"}],
                phones=[], people=[{"name": f"{FIRST[i % len(FIRST)]} Martin", "role": None, "source": "x"}],
                tags=["sole_trader"] if i == n - 1 else [], sources=[], score=50 + i,
                do_not_contact=False, website=f"https://studio{i}.fr",
            )  # fmt: skip
            lead = Lead(**{**base, **kw})
            s.add(lead)
            s.flush()
            ids.append(lead.id)
        s.commit()
    return ids


def draft(**kw) -> dict:
    c = client.post("/api/campaigns", json={"name": "Test", "kind": "photographer", **kw})
    assert c.status_code == 200, c.text
    return c.json()


def two_versions(cid: int):
    v = [{"name": "Long", "subject": "{prenom}, votre avis ?", "body": "Bonjour {prenom}, A.\n{signature}"},
         {"name": "Court", "subject": "Un test ?", "body": "Bonjour {prenom}, B.\n{signature}"}]  # fmt: skip
    r = client.put(f"/api/campaigns/{cid}", json={"versions": v})
    assert r.status_code == 200, r.text
    return r.json()


def test_audience_counts_filters_order_and_exclusions(db):
    ids = leads(db, 6)
    r = client.post("/api/campaigns/audience", json={"kind": "photographer", "size": 3}).json()
    assert r["matching"] == 6 and r["chosen"] == 3 and r["deps"] == [{"dep": "67", "n": 3}, {"dep": "68", "n": 3}]
    assert r["preview"][0]["id"] == ids[-1]  # the one-person business comes first
    r = client.post("/api/campaigns/audience", json={"kind": "photographer", "size": 10,
                                                     "filters": {"deps": ["68"], "max_km": 50}}).json()  # fmt: skip
    assert r["matching"] == 2  # 68 and within 50 km: Studio 1 (30 km) and Studio 3 (50 km)
    r = client.post("/api/campaigns/audience", json={"kind": "photographer", "size": 10, "order": "score",
                                                     "excluded": [ids[5]]}).json()  # fmt: skip
    assert r["chosen"] == 5 and r["preview"][0]["id"] == ids[4] and r["excluded"][0]["id"] == ids[5]
    with db() as s:
        s.get(Lead, ids[0]).last_contacted_at = "2026-11-01T10:00:00+00:00"
        s.commit()
    assert client.post("/api/campaigns/audience", json={"kind": "photographer"}).json()["matching"] == 5


def test_a_new_draft_starts_from_the_usual_first_email(db):
    c = draft()
    assert c["status"] == "draft" and len(c["versions"]) == 1 and c["versions"][0]["subject"]
    assert c["daily_cap"] == 20 and c["review"] == "daily" and c["followups_on"]


def test_start_deals_versions_evenly_and_writes_the_daily_batch(db):
    leads(db, 6)
    c = draft(size=6, daily_cap=4)
    two_versions(c["id"])
    r = client.post(f"/api/campaigns/{c['id']}/start").json()
    assert r["leads"] == 6 and r["campaign"]["status"] == "running"
    with db() as s:
        rows = list(s.scalars(select(CampaignLead).where(CampaignLead.campaign_id == c["id"])))
        per = sorted(sum(1 for x in rows if x.template_id == v["id"]) for v in r["campaign"]["versions"])
        assert per == [3, 3]
        camp = s.get(Campaign, c["id"])
        assert camp.experiment_id and C.prepare(s, camp) == 4 and C.prepare(s, camp) == 0  # today's cap
        msgs = list(s.scalars(select(Message).where(Message.campaign_id == c["id"])))
        assert {m.status for m in msgs} == {"queued"} and all(m.experiment_id for m in msgs)
        assert any(m.subject.split(",")[0] in FIRST for m in msgs)  # {prenom} filled in
        s.commit()
    # Started: who and what can't change any more.
    assert client.put(f"/api/campaigns/{c['id']}", json={"filters": {"deps": ["67"]}}).status_code == 409
    assert client.put(f"/api/campaigns/{c['id']}", json={"daily_cap": 10}).status_code == 200


def test_auto_review_approves_and_a_future_start_waits(db):
    leads(db, 3)
    c = draft(size=3, review="auto", start_on=(date.today() + timedelta(days=3)).isoformat())
    two_versions(c["id"])
    client.post(f"/api/campaigns/{c['id']}/start")
    with db() as s:
        camp = s.get(Campaign, c["id"])
        assert C.prepare(s, camp) == 0  # not before its start day
        camp.start_on = None
        assert C.prepare(s, camp) == 3
        assert {m.status for m in s.scalars(select(Message).where(Message.campaign_id == c["id"]))} == {"approved"}


def test_results_winner_and_follow_ups_off(db, monkeypatch):
    monkeypatch.setattr(C.experiments, "MIN_EACH", 2)
    leads(db, 12)
    c = draft(size=12, daily_cap=8, followups_on=False)
    v = two_versions(c["id"])["versions"]
    client.post(f"/api/campaigns/{c['id']}/start")
    with db() as s:
        camp = s.get(Campaign, c["id"])
        C.prepare(s, camp)
        for m in s.scalars(select(Message).where(Message.campaign_id == c["id"], Message.status == "queued")):
            mailer.send(s, m)
        s.commit()
        assert not s.scalar(select(Message.id).where(Message.step > 0))  # follow-ups are off
        # Everyone who got version A replies.
        for m in s.scalars(select(Message).where(Message.campaign_id == c["id"], Message.template_id == v[0]["id"])):
            em = inbox.EmailMessage()
            em["From"] = m.to_addr
            em["Subject"] = "Re: x"
            em["Message-ID"] = f"<r{m.id}@x.fr>"
            em["In-Reply-To"] = m.message_id
            em.set_content("Oui, intéressé")
            inbox.handle(s, em)
        s.commit()
    r = client.get(f"/api/campaigns/{c['id']}").json()["results"]
    t = r["totals"]
    assert t["sent"] == 8 and t["replied"] == 4 and t["not_sent"] == 4
    a = next(x for x in r["versions"] if x["letter"] == "A")
    assert a["sent"] == 4 and a["replied"] == 4
    assert {x["state"] for x in r["leads"]} == {"replied", "waiting", "not_sent"}
    sug = r["test"]["suggest"]
    assert sug["letter"] == "A" and sug["unsent"] == 4
    moved = client.post(f"/api/campaigns/{c['id']}/winner", json={"template_id": sug["template_id"]}).json()
    assert moved["moved"] == 2  # the B leads not emailed yet now get A
    with db() as s:
        rows = list(s.scalars(select(CampaignLead).where(CampaignLead.campaign_id == c["id"])))
        sent = {m.lead_id for m in s.scalars(select(Message).where(Message.campaign_id == c["id"], Message.status == "sent"))}
        assert {r.template_id for r in rows if r.lead_id not in sent} == {v[0]["id"]}


def test_campaign_versions_stay_in_their_campaign(db):
    leads(db, 2)
    c = draft(size=1)
    two_versions(c["id"])
    tpl_ids = {v["id"] for v in client.get(f"/api/campaigns/{c['id']}").json()["versions"]}
    other = leads(db, 1, name="Hors campagne", emails=[{"value": "x@hors.fr", "source": "x"}])[0]
    comp = client.get("/api/compose", params={"lead_id": other}).json()
    assert comp["template_id"] not in tpl_ids
    assert not tpl_ids & {t["id"] for t in client.get("/api/templates").json()["items"]}


def test_a_lead_is_in_one_running_campaign_at_a_time(db):
    leads(db, 4)
    a = draft(size=2)
    two_versions(a["id"])
    client.post(f"/api/campaigns/{a['id']}/start")
    b = client.post("/api/campaigns/audience", json={"kind": "photographer", "size": 10}).json()
    assert b["matching"] == 2


def test_grow_duplicate_delete(db):
    leads(db, 5)
    c = draft(size=2)
    two_versions(c["id"])
    assert client.post(f"/api/campaigns/{c['id']}/more", json={"n": 2}).status_code == 409  # a draft
    client.post(f"/api/campaigns/{c['id']}/start")
    assert client.post(f"/api/campaigns/{c['id']}/more", json={"n": 2}).json()["added"] == 2
    assert client.get(f"/api/campaigns/{c['id']}").json()["size"] == 4
    d = client.post(f"/api/campaigns/{c['id']}/duplicate").json()
    assert d["status"] == "draft" and d["name"].endswith("(copy)") and len(d["versions"]) == 2
    assert client.delete(f"/api/campaigns/{c['id']}").status_code == 409
    assert client.delete(f"/api/campaigns/{d['id']}").status_code == 200


def test_start_needs_text_and_leads(db):
    c = draft()
    r = client.post(f"/api/campaigns/{c['id']}/start")
    assert r.status_code == 409 and "No lead matches" in r.json()["detail"]
    leads(db, 1)
    client.put(f"/api/campaigns/{c['id']}", json={"versions": [{"name": "A", "subject": "", "body": "x"}]})
    r = client.post(f"/api/campaigns/{c['id']}/start")
    assert r.status_code == 409 and "subject" in r.json()["detail"]


def test_dossiers_hold_images_and_make_before_after(db):
    a = client.post("/api/assets", params={"name": "salon.jpg"}, content=jpeg()).json()
    ds = client.get("/api/dossiers").json()["items"]
    assert len(ds) == 1 and ds[0]["images"][0]["id"] == a["id"]  # loose images get a first dossier
    d = client.post("/api/dossiers", json={"name": "Cuisines"}).json()
    before = client.post("/api/assets", params={"name": "avant.jpg", "dossier_id": d["id"]}, content=jpeg((40, 40, 40))).json()
    after = client.post("/api/assets", params={"name": "apres.jpg", "dossier_id": d["id"]},
                        content=jpeg((220, 220, 220), size=(600, 800))).json()  # fmt: skip
    pair = client.post(f"/api/dossiers/{d['id']}/pair", json={"before_id": before["id"], "after_id": after["id"]}).json()
    img = Image.open(io.BytesIO(client.get(pair["src"]).content))
    assert img.height == 1000 and img.width == round(800 * 1000 / 600) + 12 + round(600 * 1000 / 800)
    names = {x["name"] for x in client.get("/api/dossiers").json()["items"][1]["images"]}
    assert names == {"avant", "apres", "avant avant-après"}
    client.patch(f"/api/dossiers/{d['id']}", json={"archived": True})
    assert [x["name"] for x in client.get("/api/dossiers").json()["items"]] == ["Avant / après"]


def test_a_version_quoting_the_lean_only_reaches_measured_leads(db):
    leads(db, 2)
    leads(db, 1, name="Mesuré", lean_median=2.5, emails=[{"value": "m@mesure.fr", "source": "x"}])
    c = draft(size=10)  # the photographers' usual first email quotes {lean}
    assert "{lean}" in c["versions"][0]["body"]
    r = client.post("/api/campaigns/audience", json={"kind": "photographer", "size": 10, "needs_lean": True}).json()
    assert r["matching"] == 1
    assert client.post(f"/api/campaigns/{c['id']}/start").json()["leads"] == 1


def test_a_finished_campaign_reopens_when_it_grows(db):
    leads(db, 3)
    c = draft(size=1)
    two_versions(c["id"])
    client.post(f"/api/campaigns/{c['id']}/start")
    with db() as s:
        camp = s.get(Campaign, c["id"])
        C.prepare(s, camp)
        for m in s.scalars(select(Message).where(Message.campaign_id == c["id"])):
            mailer.send(s, m)
        C.prepare(s, camp)
        assert camp.status == "done"
        s.commit()
    assert client.post(f"/api/campaigns/{c['id']}/more", json={"n": 2}).json()["added"] == 2
    assert client.get(f"/api/campaigns/{c['id']}").json()["status"] == "running"


def test_smart_filters_and_their_counts(db):
    a, b, c, d = leads(db, 4)
    with db() as s:
        s.get(Lead, a).tags = ["short_stay", "drone"]
        s.get(Lead, a).socials = {"instagram": "https://instagram.com/a"}
        s.get(Lead, a).phones = [{"value": "0612345678", "source": "x"}]
        s.get(Lead, b).tags = ["short_stay"]
        s.get(Lead, b).founded = (date.today() - timedelta(days=200)).isoformat()
        s.get(Lead, c).website = None
        s.get(Lead, d).emails = [{"value": "marie.dupont@gmail.com", "source": "x"}]
        s.commit()

    def n(**f):
        return client.post("/api/campaigns/audience", json={"kind": "photographer", "filters": f}).json()["matching"]

    assert n(tags_any=["short_stay"]) == 2 and n(tags_none=["short_stay"]) == 2
    assert n(tags_any=["short_stay"], tags_none=["drone"]) == 1
    assert n(socials=["instagram"]) == 1 and n(phone=True) == 1
    assert n(founded_years=1) == 1 and n(website="no") == 1 and n(website="yes") == 3
    assert n(email_personal=True) == 1 and n(town="colmar") == 4 and n(town="Mulhouse") == 0
    assert n(q="studio 2") == 1  # every word must be found
    r = client.post("/api/campaigns/audience", json={"kind": "photographer"}).json()
    fa = r["facets"]
    assert fa["phone"] == 1 and fa["website_no"] == 1 and fa["socials"]["instagram"] == 1
    assert {"tag": "short_stay", "label": "Holiday lets", "n": 2} in fa["tags"]


def test_leads_added_by_hand_come_first_and_count_on_top(db):
    ids = leads(db, 4)
    other = leads(db, 1, kind="agency", name="Agence du Rhin")[0]
    gone = leads(db, 1, name="Déjà vu", last_contacted_at="2026-11-01T10:00:00+00:00")[0]
    c = draft(size=2, filters={"deps": ["67"]})  # 67: ids[0], ids[2]
    r = client.post(f"/api/campaigns/{c['id']}/leads", json={"lead_ids": [ids[1], other, gone]}).json()
    assert r["added"] == 2 and r["notes"] == ["Déjà vu: already contacted"]
    aud = client.post("/api/campaigns/audience", json={"kind": "photographer", "filters": {"deps": ["67"]}, "size": 2,
                                                       "included": r["included"], "campaign_id": c["id"]}).json()  # fmt: skip
    assert aud["chosen"] == 4 and aud["from_filters"] == 2
    assert [x["id"] for x in aud["preview"][:2]] == [ids[1], other] and aud["preview"][0]["by_hand"]
    found = client.get("/api/campaigns/lookup", params={"q": "Déjà", "campaign_id": c["id"]}).json()["items"]
    assert found[0]["why"] == "already contacted"
    two_versions(c["id"])
    assert client.post(f"/api/campaigns/{c['id']}/start").json()["leads"] == 4


def test_add_a_lead_by_hand_to_a_started_campaign(db):
    ids = leads(db, 3)
    c = draft(size=2, order="score")
    two_versions(c["id"])
    client.post(f"/api/campaigns/{c['id']}/start")
    spare = [i for i in ids if i not in {ids[1], ids[2]}][0]
    camps = client.get(f"/api/leads/{spare}/campaigns").json()
    assert camps["why"] is None and camps["items"][0]["inside"] is False
    r = client.post(f"/api/campaigns/{c['id']}/leads", json={"lead_ids": [spare]}).json()
    assert r["added"] == 1
    with db() as s:
        assert s.scalar(select(CampaignLead).where(CampaignLead.lead_id == spare)).campaign_id == c["id"]
    assert client.get(f"/api/leads/{spare}/campaigns").json()["items"][0]["inside"] is True
    again = client.post(f"/api/campaigns/{c['id']}/leads", json={"lead_ids": [spare]}).json()
    assert again["added"] == 0


def test_labels_tag_filter_and_follow_leads(db):
    a, b, c = leads(db, 3)
    vip = client.post("/api/labels", json={"name": "  VIP  ", "color": "gold"}).json()
    fair = client.post("/api/labels", json={"name": "Met at the fair", "color": "violet"}).json()
    assert vip["name"] == "VIP"
    assert client.post("/api/labels", json={"name": "VIP"}).status_code == 409
    assert client.post("/api/labels", json={"name": "X", "color": "neon"}).status_code == 422
    r = client.post("/api/leads/labels", json={"lead_ids": [a, b], "add": [vip["id"]]}).json()
    assert r["changed"] == 2 and r["labels"][str(a)] == [vip["id"]]
    client.post("/api/leads/labels", json={"lead_ids": [b], "add": [fair["id"]], "remove": [vip["id"]]})
    counts = {x["name"]: x["n"] for x in client.get("/api/labels").json()["items"]}
    assert counts == {"VIP": 1, "Met at the fair": 1}
    listed = client.get("/api/leads", params={"label": vip["id"], "kind": "photographer"}).json()
    assert [x["id"] for x in listed["items"]] == [a] and listed["items"][0]["labels"] == [vip["id"]]

    def n(**f):
        return client.post("/api/campaigns/audience", json={"kind": "photographer", "filters": f}).json()

    assert n(labels_any=[vip["id"]])["matching"] == 1 and n(labels_none=[vip["id"], fair["id"]])["matching"] == 1
    assert n()["facets"]["labels"] == {str(vip["id"]): 1, str(fair["id"]): 1}
    client.patch(f"/api/labels/{vip['id']}", json={"name": "Très important", "color": "red"})
    assert client.delete(f"/api/labels/{fair['id']}").json()["leads"] == 1
    with db() as s:
        assert s.get(Lead, b).labels == []


def test_identity_says_who_a_lead_is(db):
    (a,) = leads(db, 1, postcode="74700", city="Sallanches", tags=["sole_trader", "prestige", "rental"],
                 founded="2020-05-14", socials={"instagram": "https://instagram.com/x"})  # fmt: skip
    (b,) = leads(db, 1, kind="agency", network="Laforêt", headcount="02", postcode="67000")
    ia = client.get(f"/api/leads/{a}").json()["id_"]
    assert ia["dep_name"] == "Haute-Savoie" and ia["instagram"] is True
    assert [x["t"] for x in ia["facts"]] == ["Solo", "Since 2020"]
    assert [x["t"] for x in ia["specialties"]] == ["Luxury", "Rentals"]  # rarest first
    ib = client.get(f"/api/leads/{b}").json()["id_"]
    assert [x["t"] for x in ib["facts"]] == ["Laforêt", "3–5 staff"] and ib["dep_name"] == "Bas-Rhin"
