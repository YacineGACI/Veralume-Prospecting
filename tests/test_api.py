"""The routes the app calls, on a throwaway database (dry-run mailbox)."""

from fastapi.testclient import TestClient

from prospecting.api.app import app
from prospecting.db import Lead

client = TestClient(app)


def _lead(db, **kw) -> int:
    with db() as s:
        lead = Lead(
            kind="agency", name="Agence du Rhin", city="Strasbourg", status="new",
            emails=[{"value": "marie@agence-rhin.fr", "source": "https://agence-rhin.fr"}],
            phones=[{"value": "03 88 11 22 33", "source": "x"}],
            people=[{"name": "Marie Weber", "role": None, "source": "x"}],
            tags=[], sources=[], lean_median=4.1, photos_measured=4, score=90,
            do_not_contact=False, **kw,
        )  # fmt: skip
        s.add(lead)
        s.commit()
        return lead.id


def test_today_meta_and_leads(db):
    lid = _lead(db)
    t = client.get("/api/today?segment=agency").json()
    assert [l["id"] for l in t["fresh"]] == [lid]
    assert t["mailbox"]["dry_run"] is True
    assert client.get("/api/meta").json()["by_kind"]["agency"]["email"] == 1
    rows = client.get("/api/leads?kind=agency&reach=email&contacted=no").json()
    assert rows["total"] == 1 and rows["items"][0]["email_ok"]


def test_compose_send_and_followups_show_up(db):
    lid = _lead(db)
    c = client.get(f"/api/compose?lead_id={lid}").json()
    assert c["mode"] == "first" and c["to"] == "marie@agence-rhin.fr"
    assert c["body"].startswith("Bonjour Marie,")
    r = client.post("/api/send", json={
        "lead_id": lid, "to": c["to"], "subject": c["subject"], "body": c["body"],
        "template_id": c["template_id"],
    })  # fmt: skip
    assert r.status_code == 200, r.text
    lead = client.get(f"/api/leads/{lid}").json()
    assert lead["status"] == "contacted"
    assert [m["status"] for m in lead["messages"]] == ["queued", "queued", "sent"]
    # Composing again is a follow-up, threaded on the first email.
    c2 = client.get(f"/api/compose?lead_id={lid}").json()
    assert c2["mode"] == "followup" and c2["subject"].startswith("Re: ")


def test_send_rejects_a_bad_address(db):
    lid = _lead(db)
    r = client.post("/api/send", json={"lead_id": lid, "to": "nope", "subject": "a", "body": "b"})
    assert r.status_code == 422


def test_call_outcome_and_pipeline(db):
    lid = _lead(db)
    r = client.post(f"/api/leads/{lid}/call", json={"outcome": "interested"})
    assert r.status_code == 200 and "Interview" in r.json()["message"]
    p = client.get("/api/pipeline").json()
    assert p["counts"]["interview"] == 1
    assert client.post(f"/api/leads/{lid}/call", json={"outcome": "maybe"}).status_code == 422


def test_campaign_prepare_approve(db):
    _lead(db)
    tpl = client.get("/api/templates").json()["items"]
    ag = next(t for t in tpl if t["key"] == "ag_first")
    c = client.post("/api/campaigns", json={"name": "Agences", "kind": "agency",
                                            "template_id": ag["id"], "daily_cap": 5}).json()  # fmt: skip
    assert c["stats"]["matching"] == 1
    w = client.post(f"/api/campaigns/{c['id']}/prepare", json={}).json()
    assert w["written"] == 1
    ready = client.get("/api/today?segment=agency").json()["ready"]
    assert len(ready) == 1
    a = client.post("/api/messages/approve", json={"ids": [ready[0]["message"]["id"]]}).json()
    assert a["approved"] == 1


def test_dnc_cancels_pending(db):
    lid = _lead(db)
    client.post(f"/api/leads/{lid}/call", json={"outcome": "voicemail"})
    lead = client.patch(f"/api/leads/{lid}", json={"do_not_contact": True}).json()
    assert all(m["status"] == "cancelled" for m in lead["messages"])


def test_settings_roundtrip(db):
    r = client.put("/api/settings", json={"goals": {"email": 20, "call": 10, "reply": 2}}).json()
    assert r["goals"]["email"] == 20
