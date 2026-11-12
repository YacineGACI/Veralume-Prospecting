"""The campaign follow-up: one email, sent on its own N days after a first email that got
no reply, in the first email's language and thread; caught up after the app was off.
Dry-run mailbox: nothing is sent."""

from datetime import timedelta

from sqlalchemy import select

from prospecting import campaigns as C
from prospecting import mailer
from prospecting.db import Campaign, Lead, Message
from tests.test_campaign_languages import DE, FR
from tests.test_campaigns import client, draft, leads
from tests.test_campaigns import outside_sending_hours  # noqa: F401  (autouse)

FU = {"language": "fr", "body": "Bonjour {prenom},\n\nJe reviens vers vous.\n\n{signature}",
      "translations": {"de": "Hallo {prenom},\n\nIch melde mich noch einmal.\n\n{signature}"}}  # fmt: skip


def _sent(db, *, fu=FU, days_ago=8, n_fr=3, n_de=2):
    """A started campaign whose first emails all went out `days_ago` days ago."""
    fr = leads(db, n_fr)
    de = leads(db, n_de, country="DE", language="de", city="München", postcode="80331") if n_de else []
    c = draft(size=50)
    body = {"versions": [{"name": "A", **FR, "translations": {"de": DE}}]}
    if fu is not None:
        body["followup"] = fu
    assert client.put(f"/api/campaigns/{c['id']}", json=body).status_code == 200
    client.post(f"/api/campaigns/{c['id']}/start")
    for lid in fr + de:
        m = client.post(f"/api/campaigns/{c['id']}/leads/{lid}/email").json()["message"]
        assert client.post(f"/api/messages/{m['id']}/send").json()["status"] == "sent"
    back = mailer.iso(mailer.local() - timedelta(days=days_ago))
    with db() as s:
        for m in s.scalars(select(Message).where(Message.campaign_id == c["id"])):
            m.sent_at = back
        s.commit()
    return c["id"], fr, de


def _followups(s, cid):
    return {m.lead_id: m for m in s.scalars(
        select(Message).where(Message.campaign_id == cid, Message.step == 1))}  # fmt: skip


def test_new_campaign_has_a_french_followup_after_7_days(db):
    c = draft()
    assert c["followup_days"] == 7 and c["followup"]["language"] == "fr"
    assert "revenir vers vous" in c["followup"]["body"]
    assert len(c["versions"]) == 1  # the follow-up isn't a version


def test_followup_goes_out_after_the_days_in_the_first_emails_language_and_thread(db):
    cid, fr, de = _sent(db)
    with db() as s:
        assert C.write_due_followups(s) == 5
        s.commit()
        fus = _followups(s, cid)
        firsts = {m.lead_id: m for m in s.scalars(
            select(Message).where(Message.campaign_id == cid, Message.step == 0))}  # fmt: skip
        for lid, m in fus.items():
            first = firsts[lid]
            assert m.status == "approved" and m.in_reply_to == first.message_id
            assert m.subject == f"Re: {first.subject}" and m.to_addr == first.to_addr
            assert m.language == first.language and not m.asset_ids and not m.attach_photo_id
        assert "Ich melde mich" in fus[de[0]].body and "Je reviens" in fus[fr[0]].body
        assert C.write_due_followups(s) == 0  # never twice
        # The worker sends them like any approved email (in dry-run).
        mailer.send(s, fus[de[0]])
        s.commit()
        em = mailer.build(fus[de[0]], s.get(Lead, de[0]), s)
        assert "Stopp" in em.get_body().get_content() and not list(em.iter_attachments())
    res = client.get(f"/api/campaigns/{cid}").json()
    states = {l["id"]: l["state"] for l in res["results"]["leads"]}
    assert states[de[0]] == "followed_up" and states[fr[0]] == "waiting"
    assert res["followup_status"]["sent"] == 1 and res["followup_status"]["waiting"] == 4


def test_not_before_its_day_and_the_lead_page_shows_when(db):
    cid, fr, de = _sent(db, days_ago=3, n_de=0)
    with db() as s:
        assert C.write_due_followups(s) == 0
    st = client.get(f"/api/campaigns/{cid}").json()["followup_status"]
    assert st["later"] == 3 and st["next_at"]
    plan = client.get(f"/api/leads/{fr[0]}").json()["followup_plan"]
    assert len(plan) == 1 and plan[0]["campaign_id"] == cid
    day7 = mailer.first_slot(mailer.local() - timedelta(days=3) + timedelta(days=7))
    assert mailer.local(plan[0]["due_at"]).date() == day7.date() and day7.weekday() < 5
    # Two days: now it's due.
    client.put(f"/api/campaigns/{cid}", json={"followup_days": 2})
    with db() as s:
        assert C.write_due_followups(s) == 3


def test_catch_up_after_the_app_was_off(db, monkeypatch):
    """First emails 12 days ago, the worker never ran: the next run writes them all, and
    send_due sends them one at a time inside the sending hours."""
    cid, fr, de = _sent(db, days_ago=12, n_de=0)
    with db() as s:
        assert C.write_due_followups(s) == 3
        s.commit()
    monkeypatch.setattr(mailer, "in_window", lambda dt=None: True)
    mailer._NEXT_SLOT.clear()
    with db() as s:
        assert mailer.send_due(s).step == 1
        s.commit()
        assert mailer.send_due(s) is None  # the gap before the next one
        assert sum(m.status == "sent" for m in _followups(s, cid).values()) == 1
    mailer._NEXT_SLOT.clear()


def test_no_followup_after_a_reply_a_stop_or_without_their_language(db):
    cid, fr, de = _sent(db, fu={"language": "fr", "body": FU["body"], "translations": {}})
    with db() as s:
        s.add(Message(lead_id=fr[0], direction="in", status="received", subject="Re", body="Oui !",
                      sent_at=mailer.iso(mailer.local())))  # fmt: skip
        s.get(Lead, fr[1]).do_not_contact = True
        s.commit()
        assert C.write_due_followups(s) == 1  # fr[2] only; the Germans have no German follow-up
        s.commit()
        assert set(_followups(s, cid)) == {fr[2]}
    st = client.get(f"/api/campaigns/{cid}").json()["followup_status"]
    assert st["missing"] == {"de": 2} and st["replied"] == 2


def test_followup_off_or_campaign_paused(db):
    cid, fr, de = _sent(db, n_de=0)
    client.put(f"/api/campaigns/{cid}", json={"followups_on": False})
    with db() as s:
        assert C.write_due_followups(s) == 0
    client.put(f"/api/campaigns/{cid}", json={"followups_on": True})
    with db() as s:
        s.get(Campaign, cid).status = "paused"
        s.commit()
        assert C.write_due_followups(s) == 0


def test_followup_and_days_editable_once_started_but_not_the_first_email(db):
    cid, fr, de = _sent(db, n_de=0, days_ago=1)
    r = client.put(f"/api/campaigns/{cid}", json={"followup_days": 5,
                                                  "followup": {"language": "fr", "body": "Nouveau {prenom}"}})  # fmt: skip
    assert r.status_code == 200 and r.json()["followup_days"] == 5 and r.json()["followup"]["body"] == "Nouveau {prenom}"
    assert client.put(f"/api/campaigns/{cid}", json={"followup_days": 0}).status_code == 422
    assert client.put(f"/api/campaigns/{cid}", json={"followup": {"language": "fr", "body": " "}}).status_code == 422
    assert client.put(f"/api/campaigns/{cid}", json={"versions": [{"name": "A", **FR}]}).status_code == 409
    d = client.post(f"/api/campaigns/{cid}/duplicate").json()
    assert d["followup"]["body"] == "Nouveau {prenom}" and d["followup_days"] == 5


def test_a_due_day_on_a_weekend_or_evening_moves_to_the_next_sending_hours():
    tz = mailer.TZ
    from datetime import datetime

    sat = datetime(2026, 11, 14, 11, 39, tzinfo=tz)
    assert mailer.first_slot(sat) == datetime(2026, 11, 16, 9, 0, tzinfo=tz)  # Monday 9:00
    fri_eve = datetime(2026, 11, 13, 19, 0, tzinfo=tz)
    assert mailer.first_slot(fri_eve) == datetime(2026, 11, 16, 9, 0, tzinfo=tz)
    tue_early = datetime(2026, 11, 17, 7, 30, tzinfo=tz)
    assert mailer.first_slot(tue_early) == datetime(2026, 11, 17, 9, 0, tzinfo=tz)
    tue = datetime(2026, 11, 17, 11, 0, tzinfo=tz)
    assert mailer.first_slot(tue) == tue


def test_a_campaign_starts_only_with_its_automations_set(db):
    from prospecting.db import Template, session

    leads(db, 2)
    c = draft(size=10)
    client.put(f"/api/campaigns/{c['id']}", json={"versions": [{"name": "A", **FR}]})
    with session() as s:  # the follow-up is on but its text is gone
        for t in s.scalars(select(Template).where(Template.campaign_id == c["id"], Template.step == 1)):
            t.archived = True
        s.commit()
    r = client.post(f"/api/campaigns/{c['id']}/start")
    assert r.status_code == 409 and "automations" in r.json()["detail"]
    client.put(f"/api/campaigns/{c['id']}", json={"followups_on": False})  # turned off: fine
    assert client.post(f"/api/campaigns/{c['id']}/start").status_code == 200
