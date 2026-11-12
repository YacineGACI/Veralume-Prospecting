"""The campaign follow-up: one email, sent on its own N days after a first email that got
no reply, in the first email's language and thread; caught up after the app was off.
Dry-run mailbox: nothing is sent."""

from datetime import timedelta

import pytest

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
    monkeypatch.setattr(mailer, "in_window", lambda dt=None, c=None: True)
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


# --- follow-up 2, and changes to follow-ups already written ----------------------------


def _send_all(db, cid, step, days_ago=None):
    """Send every written follow-up of this step (dry-run), dated `days_ago` days back."""
    with db() as s:
        for m in _followups_step(s, cid, step):
            if m.status == "approved":
                mailer.send(s, m)
                if days_ago is not None:
                    m.sent_at = mailer.iso(mailer.local() - timedelta(days=days_ago))
        s.commit()


def _followups_step(s, cid, step):
    return list(s.scalars(select(Message).where(Message.campaign_id == cid, Message.step == step)))


def test_a_new_campaign_has_a_second_followup_after_10_days(db):
    c = draft()
    assert c["followup2_on"] is True and c["followup2_days"] == 10
    assert "Dernier message" in c["followup2"]["body"] and len(c["followup2"]["translations"]) == 12


def test_followup_2_goes_after_followup_1_to_those_still_silent(db):
    cid, fr, de = _sent(db, n_de=1)
    with db() as s:
        assert C.write_due_followups(s) == 4  # follow-up 1 for all
        s.commit()
    _send_all(db, cid, 1, days_ago=3)
    with db() as s:
        assert C.write_due_followups(s) == 0  # follow-up 2 waits 10 days after follow-up 1
        s.add(Message(lead_id=fr[0], direction="in", status="received", subject="Re", body="Oui",
                      sent_at=mailer.iso(mailer.local())))  # fmt: skip
        s.commit()
    st = client.get(f"/api/campaigns/{cid}").json()["followup2_status"]
    assert st["later"] == 3 and st["replied"] == 1
    client.put(f"/api/campaigns/{cid}", json={"followup2_days": 2})
    with db() as s:
        assert C.write_due_followups(s) == 3  # not to the one who replied
        s.commit()
        fu1 = {m.lead_id: m for m in _followups_step(s, cid, 1)}
        fu2 = {m.lead_id: m for m in _followups_step(s, cid, 2)}
        assert set(fu2) == {fr[1], fr[2], de[0]}
        for lid, m in fu2.items():
            assert m.in_reply_to == fu1[lid].message_id and m.subject == fu1[lid].subject  # one "Re:"
            assert m.language == fu1[lid].language and not m.asset_ids
        assert "letzte Nachricht" in fu2[de[0]].body and "Dernier message" in fu2[fr[1]].body
    _send_all(db, cid, 2)
    states = {l["id"]: l["state"] for l in client.get(f"/api/campaigns/{cid}").json()["results"]["leads"]}
    assert states[fr[1]] == "followed_up_2" and states[fr[0]] == "replied"


def test_followup_2_off_or_followup_1_off(db):
    cid, fr, de = _sent(db, n_de=0, days_ago=20)  # first emails 20 days ago, follow-up 1 11
    with db() as s:
        C.write_due_followups(s)
        s.commit()
    _send_all(db, cid, 1, days_ago=11)
    client.put(f"/api/campaigns/{cid}", json={"followup2_on": False})
    with db() as s:
        assert C.write_due_followups(s) == 0
    client.put(f"/api/campaigns/{cid}", json={"followup2_on": True, "followups_on": False})
    with db() as s:
        assert C.write_due_followups(s) == 0  # follow-up 2 hangs on follow-up 1
    client.put(f"/api/campaigns/{cid}", json={"followups_on": True})
    with db() as s:
        assert C.write_due_followups(s) == 3


def test_switching_off_or_editing_rewrites_the_followups_not_sent_yet(db):
    cid, fr, de = _sent(db, n_de=0)
    with db() as s:
        assert C.write_due_followups(s) == 3  # written, waiting for a free slot
        s.commit()
    client.put(f"/api/campaigns/{cid}", json={"followups_on": False})
    with db() as s:
        assert _followups_step(s, cid, 1) == []  # none goes out
    client.put(f"/api/campaigns/{cid}", json={"followups_on": True})
    with db() as s:
        assert C.write_due_followups(s) == 3
        s.commit()
    client.put(f"/api/campaigns/{cid}", json={"followup": {"language": "fr", "body": "Nouveau texte, {prenom}"}})
    with db() as s:
        assert _followups_step(s, cid, 1) == []
        assert C.write_due_followups(s) == 3
        s.commit()
        assert all(m.body.startswith("Nouveau texte") for m in _followups_step(s, cid, 1))
    # Saving the same text again changes nothing.
    client.put(f"/api/campaigns/{cid}", json={"followup": {"language": "fr", "body": "Nouveau texte, {prenom}"}})
    with db() as s:
        assert len(_followups_step(s, cid, 1)) == 3
    # A longer delay: not due any more, so nothing waits.
    client.put(f"/api/campaigns/{cid}", json={"followup_days": 20})
    with db() as s:
        assert _followups_step(s, cid, 1) == [] and C.write_due_followups(s) == 0


# --- "No reply": the campaign is over for those who never answered ----------------------


def _all_sent(db, n_de=0):
    """First email 40 days ago, follow-up 1 30 days ago, follow-up 2 20 days ago: all silent."""
    cid, fr, de = _sent(db, n_de=n_de, days_ago=40)
    with db() as s:
        C.write_due_followups(s)
        s.commit()
    _send_all(db, cid, 1, days_ago=30)
    with db() as s:
        C.write_due_followups(s)
        s.commit()
    _send_all(db, cid, 2, days_ago=20)
    return cid, fr, de


def test_silent_after_every_email_become_no_reply(db):
    cid, fr, de = _all_sent(db)
    c = client.get(f"/api/campaigns/{cid}").json()
    assert c["close_on"] is True and c["close_days"] == 15
    with db() as s:
        assert C.close_silent(s) == 3
        s.commit()
        lead = s.get(Lead, fr[0])
        assert lead.status == "no_reply" and not lead.do_not_contact  # not blocked: can be tried again
        assert C.close_silent(s) == 0
        from prospecting.db import Activity

        note = s.scalars(select(Activity).where(Activity.lead_id == fr[0], Activity.kind == "status")
                         .order_by(Activity.id.desc())).first().text  # fmt: skip
        assert "no answer to 3 emails" in note
    r = client.get(f"/api/campaigns/{cid}").json()
    assert r["close_status"]["done"] == 3 and r["results"]["totals"]["no_reply"] == 3
    assert {l["state"] for l in r["results"]["leads"] if l["id"] in fr} == {"no_reply"}
    assert client.get("/api/pipeline").json()["counts"]["no_reply"] == 3


def test_a_late_reply_still_moves_them_to_replied(db):
    from email.message import EmailMessage

    from prospecting import inbox

    cid, fr, de = _all_sent(db)
    with db() as s:
        C.close_silent(s)
        s.commit()
        first = s.scalars(select(Message).where(Message.lead_id == fr[0], Message.step == 0)).first()
        em = EmailMessage()
        em["From"], em["To"], em["Subject"] = first.to_addr, "moi@veralume.test", "Re: votre email"
        em["Message-ID"], em["In-Reply-To"] = "<late@x>", first.message_id
        em.set_content("Bonjour, désolé du retard, oui je suis intéressé !")
        assert inbox.handle(s, em) == "reply"
        s.commit()
        assert s.get(Lead, fr[0]).status == "replied"


def test_no_reply_waits_its_days_and_for_every_followup(db):
    cid, fr, de = _sent(db, n_de=0, days_ago=40)
    with db() as s:
        assert C.close_silent(s) == 0  # follow-up 1 is still to come (due)
        C.write_due_followups(s)
        s.commit()
        assert C.close_silent(s) == 0  # written, waiting to go out
    _send_all(db, cid, 1, days_ago=5)
    with db() as s:
        assert C.close_silent(s) == 0  # follow-up 2 is planned
    client.put(f"/api/campaigns/{cid}", json={"followup2_on": False})
    with db() as s:
        assert C.close_silent(s) == 0  # 5 days after the last email: not yet 15
    st = client.get(f"/api/campaigns/{cid}").json()["close_status"]
    assert st["later"] == 3 and st["next_at"]
    client.put(f"/api/campaigns/{cid}", json={"close_days": 4})
    with db() as s:
        assert C.close_silent(s) == 3


def test_no_reply_off_and_counted_from_the_first_email_without_followups(db):
    cid, fr, de = _sent(db, n_de=0, days_ago=20)
    client.put(f"/api/campaigns/{cid}", json={"followups_on": False, "close_on": False})
    with db() as s:
        assert C.close_silent(s) == 0
    assert client.put(f"/api/campaigns/{cid}", json={"close_days": 0}).status_code == 422
    client.put(f"/api/campaigns/{cid}", json={"close_on": True})
    with db() as s:
        assert C.close_silent(s) == 3  # 20 days after the first email, nothing else to come


@pytest.mark.parametrize("status", ["replied", "interview", "pilot"])
def test_manual_engagement_blocks_future_automatic_followups(db, status):
    cid, fr, _ = _sent(db, n_fr=1, n_de=0)
    assert client.patch(f"/api/leads/{fr[0]}", json={"status": status}).status_code == 200
    with db() as s:
        assert C.write_due_followups(s) == 0
        assert not _followups(s, cid)
    # Reporting may require an actual incoming email; automation must still respect
    # the manually recorded conversation and avoid declaring the lead silent.
    page = client.get(f"/api/campaigns/{cid}")
    assert page.status_code == 200
    assert page.json()["followup_status"]["replied"] == 1
    with db() as s:
        assert C.close_silent(s) == 0
        assert s.get(Lead, fr[0]).status == status
