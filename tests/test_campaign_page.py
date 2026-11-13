"""The campaign page: acting on some leads (skip or send their follow-up now, remove, move),
what goes out in the coming days, its own sending days, hours and cap, pausing on its own
when too many addresses bounce, export, preview, archive and delete.
Dry-run mailbox: nothing is sent."""

from datetime import date, timedelta

from sqlalchemy import select

from prospecting import campaigns as C
from prospecting import mailer
from prospecting.db import Campaign, CampaignLead, Lead, Message
from tests.test_campaigns import (
    client,
    draft,
    leads,
    outside_sending_hours,  # noqa: F401  (autouse)
)
from tests.test_followups import _followups, _sent

REAL_IN_WINDOW = mailer.in_window  # the autouse fixture replaces it with "never"


def _bulk(cid, action, ids, **kw):
    return client.post(f"/api/campaigns/{cid}/leads/bulk", json={"action": action, "lead_ids": ids, **kw})


def test_skip_resume_and_send_a_followup_now(db):
    cid, fr, _ = _sent(db, days_ago=3, n_de=0)
    assert _bulk(cid, "skip", [fr[0]]).json()["done"] == 1
    rows = {l["id"]: l for l in client.get(f"/api/campaigns/{cid}").json()["results"]["leads"]}
    assert rows[fr[0]]["skip"] and rows[fr[0]]["next"] is None
    assert rows[fr[1]]["next"]["step"] == 1 and len(rows[fr[1]]["emails"]) == 1
    # Day 3 of 7: "now" writes it at once for those not skipped.
    r = _bulk(cid, "followup_now", fr).json()
    assert r["done"] == 2 and "skipped" in r["notes"][0]
    with db() as s:
        fus = _followups(s, cid)
        assert set(fus) == set(fr[1:]) and all(m.status == "approved" for m in fus.values())
    assert "already written" in _bulk(cid, "followup_now", [fr[1]]).json()["notes"][0]
    # Skipping drops what was written; resuming puts them back in line.
    _bulk(cid, "skip", [fr[1]])
    with db() as s:
        assert fr[1] not in _followups(s, cid)
    _bulk(cid, "resume", [fr[0], fr[1]])
    with db() as s:
        assert C.write_due_followups(s) == 0  # not their day yet
    rows = {l["id"]: l for l in client.get(f"/api/campaigns/{cid}").json()["results"]["leads"]}
    assert not rows[fr[0]]["skip"] and rows[fr[0]]["next"]["step"] == 1


def test_remove_and_move_leads(db):
    cid, fr, _ = _sent(db, days_ago=8, n_de=0)
    extra = leads(db, 2, city="Colmar", postcode="68000")
    client.post(f"/api/campaigns/{cid}/leads", json={"lead_ids": extra})
    other = draft()
    # A lead already emailed stays; one not emailed yet moves.
    r = _bulk(cid, "move", [fr[0], extra[0]], target=other["id"]).json()
    assert r["done"] == 1 and "already got" in r["notes"][0]
    assert extra[0] in client.get(f"/api/campaigns/{other['id']}").json()["included"]
    assert _bulk(cid, "remove", [fr[0]]).json()["done"] == 1
    with db() as s:
        assert C.write_due_followups(s) == 2  # not the removed one
        assert fr[0] not in _followups(s, cid)
        assert fr[0] in s.get(Campaign, cid).excluded
        ids = set(s.scalars(select(CampaignLead.lead_id).where(CampaignLead.campaign_id == cid)))
        assert fr[0] not in ids and extra[0] not in ids and extra[1] in ids
        assert s.scalar(select(Message.id).where(Message.lead_id == fr[0], Message.status == "sent"))  # history kept


def test_forecast_spreads_followups_under_the_daily_cap(db):
    cid, _, _ = _sent(db, days_ago=8, n_fr=5, n_de=0)
    client.put(f"/api/campaigns/{cid}", json={"daily_cap": 2})
    days = client.get(f"/api/campaigns/{cid}").json()["forecast"]
    assert len(days) == 12 and all(mailer.local().date().isoformat() <= d["day"] for d in days)
    assert sum(d["fu1"] for d in days) == 5 and max(d["fu1"] + d["fu2"] for d in days) <= 2
    assert sum(d["fu2"] for d in days) >= 1  # those still silent get follow-up 2 later
    assert all(date.fromisoformat(d["day"]).weekday() < 5 for d in days)


def test_own_days_hours_and_cap_decide_what_goes_out(db, monkeypatch):
    cid, _, _ = _sent(db, days_ago=12, n_de=0)
    with db() as s:
        assert C.write_due_followups(s) == 3
        s.commit()
    real_local = mailer.local
    monday10 = real_local().replace(hour=10, minute=0) - timedelta(days=real_local().weekday())
    monkeypatch.setattr(mailer, "local", lambda iso_str=None: real_local(iso_str) if iso_str else monday10)
    monkeypatch.setattr(mailer, "in_window", REAL_IN_WINDOW)
    mailer._NEXT_SLOT.clear()
    # Its own hours: afternoons only, so nothing at 10:00.
    assert client.put(f"/api/campaigns/{cid}", json={"send_hours": [14, 18]}).status_code == 200
    with db() as s:
        assert mailer.send_due(s) is None
    client.put(f"/api/campaigns/{cid}", json={"send_hours": [9, 18], "send_days": [1, 2]})  # Tue, Wed
    with db() as s:
        assert mailer.send_due(s) is None
    c = client.put(f"/api/campaigns/{cid}", json={"send_days": [0, 1, 2, 3, 4], "daily_cap": 3}).json()
    assert c["send_days"] == [0, 1, 2, 3, 4] and c["send_hours"] == [9, 18]
    with db() as s:  # its cap counts today's first emails too (3 sent "today")
        for m in s.scalars(select(Message).where(Message.campaign_id == cid, Message.step == 0)):
            m.sent_at = mailer.iso(monday10 - timedelta(hours=1))
        s.commit()
        assert mailer.send_due(s) is None
    client.put(f"/api/campaigns/{cid}", json={"daily_cap": 4})
    with db() as s:
        assert mailer.send_due(s).step == 1
        s.commit()
    assert client.put(f"/api/campaigns/{cid}", json={"send_days": []}).status_code == 422
    assert client.put(f"/api/campaigns/{cid}", json={"send_hours": [18, 9]}).status_code == 422


def test_paused_campaigns_send_nothing_and_too_many_bounces_pause_it(db, monkeypatch):
    cid, _, _ = _sent(db, days_ago=12, n_fr=20, n_de=0)
    monkeypatch.setattr(mailer, "in_window", lambda dt=None, c=None: True)
    with db() as s:
        C.write_due_followups(s)
        s.commit()
    client.post(f"/api/campaigns/{cid}/status", json={"status": "paused"})
    mailer._NEXT_SLOT.clear()
    with db() as s:
        assert mailer.send_due(s) is None  # its follow-ups wait while it's paused
    client.post(f"/api/campaigns/{cid}/status", json={"status": "running"})
    client.put(f"/api/campaigns/{cid}", json={"bounce_pause": 5})
    with db() as s:
        first = list(s.scalars(select(Message).where(Message.campaign_id == cid, Message.step == 0)))
        first[0].status, first[0].error = "failed", "bounced"  # 1 of 20: 5 %
        s.commit()
        assert C.pause_on_bounces(s) == 1
        s.commit()
    c = client.get(f"/api/campaigns/{cid}").json()
    assert c["status"] == "paused" and "1 of 20" in c["paused_reason"]
    client.post(f"/api/campaigns/{cid}/status", json={"status": "running"})
    with db() as s:  # resumed: the same bounce doesn't pause it again
        assert C.pause_on_bounces(s) == 0
    assert client.get(f"/api/campaigns/{cid}").json()["paused_reason"] is None


def test_export_preview_settings_archive_and_delete(db):
    cid, fr, de = _sent(db, days_ago=3, n_fr=2, n_de=1)
    csv = client.get(f"/api/campaigns/{cid}/export.csv")
    assert csv.status_code == 200 and csv.text.strip().count("\n") == 3 and "follow-up 1" in csv.text
    p = client.post(f"/api/campaigns/{cid}/preview", json={"step": 1, "language": "de"}).json()
    assert p["subject"].startswith("Re: ") and "Ich melde mich" in p["body"]
    p = client.post(f"/api/campaigns/{cid}/preview", json={"step": 1, "language": "fr", "body": "Salut {prenom} !"}).json()
    assert p["body"].startswith("Salut ") and "{prenom}" not in p["body"]
    p = client.post(f"/api/campaigns/{cid}/preview", json={"step": 0}).json()
    assert not p["subject"].startswith("Re:") and p["lead"]["id"] in fr + de
    c = client.put(f"/api/campaigns/{cid}", json={"notes": "Test of the subject", "bounce_pause": 0}).json()
    assert c["notes"] == "Test of the subject" and c["bounce_pause"] is None and c["send_mode"] in ("manual", "scheduled")
    assert client.post(f"/api/campaigns/{cid}/status", json={"status": "archived"}).json()["status"] == "archived"
    assert next(x for x in client.get("/api/campaigns").json()["items"] if x["id"] == cid)["status"] == "archived"
    assert client.post(f"/api/campaigns/{cid}/status", json={"status": "unarchive"}).json()["status"] == "paused"
    _bulk(cid, "followup_now", fr)
    assert client.delete(f"/api/campaigns/{cid}").status_code == 200
    with db() as s:
        assert s.get(Campaign, cid) is None
        assert not list(s.scalars(select(Message).where(Message.campaign_id == cid, Message.status == "approved")))
        assert s.scalar(select(Message.id).where(Message.lead_id == fr[0], Message.status == "sent"))
        assert s.get(Lead, fr[0]) is not None


def test_add_a_version_to_a_running_campaign(db):
    cid, fr, de = _sent(db, days_ago=2, n_fr=2, n_de=1)
    extra = leads(db, 8, city="Colmar", postcode="68000")
    extra_de = leads(db, 1, country="DE", language="de", city="München", postcode="80331")
    client.post(f"/api/campaigns/{cid}/leads", json={"lead_ids": extra + extra_de})
    queued = client.post(f"/api/campaigns/{cid}/leads/{extra[0]}/email").json()["message"]["id"]
    B = {"name": "Free test", "subject": "Vos photos retouchées gratuitement ?", "body": "Bonjour {prenom}, B.\n{signature}"}
    r = client.post(f"/api/campaigns/{cid}/versions", json=B)
    assert r.status_code == 200, r.text
    b_id, got = r.json()["id"], r.json()["leads"]
    with db() as s:
        rows = {x.lead_id: x.template_id for x in s.scalars(select(CampaignLead).where(CampaignLead.campaign_id == cid))}
        a_id = rows[fr[0]]
        assert all(rows[l] == a_id for l in fr + de)  # already emailed: unchanged
        assert rows[extra_de[0]] == a_id  # B isn't in German
        assert sum(rows[l] == b_id for l in extra) == got == 4  # half of the 8 French ones
        assert s.get(Message, queued).status == "cancelled"  # dealt again, written again
    c = client.get(f"/api/campaigns/{cid}").json()
    assert len(c["versions"]) == 2 and len(c["results"]["versions"]) == 2
    test = c["results"]["test"]
    assert test and all(v["sent"] == 0 for v in test["versions"])  # compared from now on
    assert client.post(f"/api/campaigns/{cid}/versions", json={**B, "subject": " "}).status_code == 409
    # Up to 4 versions.
    for _ in range(2):
        assert client.post(f"/api/campaigns/{cid}/versions", json=B).status_code == 200
    assert "at most" in client.post(f"/api/campaigns/{cid}/versions", json=B).json()["detail"]


def test_no_new_version_once_the_test_has_ended(db):
    cid, _, _ = _sent(db, days_ago=2, n_fr=2, n_de=0)
    B = {"name": "B", "subject": "Un test ?", "body": "Bonjour {prenom}, B.\n{signature}"}
    b_id = client.post(f"/api/campaigns/{cid}/versions", json=B).json()["id"]
    assert client.post(f"/api/campaigns/{cid}/winner", json={"template_id": b_id}).status_code == 200
    r = client.post(f"/api/campaigns/{cid}/versions", json=B)
    assert r.status_code == 409 and "ended" in r.json()["detail"]
