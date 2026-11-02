"""A/B tests of the first email, on a throwaway database (dry-run mailbox)."""

from collections import Counter

from fastapi.testclient import TestClient
from sqlalchemy import select

from prospecting import experiments, mailer
from prospecting.api.app import app
from prospecting.db import Activity, Experiment, Lead, Message, Template

client = TestClient(app)


def _leads(db, n, kind="photographer", lean=2.5) -> list[int]:
    ids = []
    with db() as s:
        for i in range(n):
            lead = Lead(
                kind=kind, name=f"Studio {i}", city="Strasbourg", status="new",
                emails=[{"value": f"hello{i}@studio{i}.fr", "source": f"https://studio{i}.fr"}],
                phones=[], people=[], tags=[], sources=[], lean_median=lean,
                photos_measured=3, score=50, do_not_contact=False,
            )  # fmt: skip
            s.add(lead)
            s.flush()
            ids.append(lead.id)
        s.commit()
    return ids


def _tpl(db, key) -> int:
    with db() as s:
        return s.scalar(select(Template.id).where(Template.key == key))


def _start(db, keys=("ph_first", "ph_plain"), metric="reply") -> dict:
    r = client.post("/api/experiments", json={
        "name": "Lean vs plain", "kind": "photographer",
        "template_ids": [_tpl(db, k) for k in keys], "metric": metric,
    })  # fmt: skip
    assert r.status_code == 200, r.text
    return r.json()


def _send(lid: int) -> dict:
    c = client.get(f"/api/compose?lead_id={lid}").json()
    r = client.post("/api/send", json={
        "lead_id": lid, "to": c["to"], "subject": c["subject"], "body": c["body"],
        "template_id": c["template_id"], "experiment_id": c["test"] and c["test"]["id"],
        "followups": False,
    })  # fmt: skip
    assert r.status_code == 200, r.text
    return c


def test_versions_are_balanced_and_stable(db):
    ids = _leads(db, 10)
    _start(db)
    first = client.get(f"/api/compose?lead_id={ids[0]}").json()
    again = client.get(f"/api/compose?lead_id={ids[0]}").json()
    assert first["test"]["in_test"] and first["test"]["letter"] == again["test"]["letter"]
    letters = Counter(_send(lid)["test"]["letter"] for lid in ids)
    assert letters == {"A": 5, "B": 5}


def test_leads_a_version_does_not_fit_stay_out(db):
    # ph_first quotes the lean: an unmeasured lead must not get it, nor enter the test.
    (lid,) = _leads(db, 1, lean=None)
    _start(db)
    c = client.get(f"/api/compose?lead_id={lid}").json()
    assert c["test"] is None and "{lean}" not in c["body"]
    with db() as s:
        assert experiments.assign(s, s.get(Lead, lid)) is None


def test_picking_another_template_takes_the_lead_out(db):
    (lid,) = _leads(db, 1)
    exp = _start(db)
    c = client.get(f"/api/compose?lead_id={lid}").json()
    other = next(t for t in exp["template_ids"] if t != c["test"]["template_id"])
    c2 = client.get(f"/api/compose?lead_id={lid}&template_id={other}").json()
    assert c2["test"]["in_test"] is False


def test_results_count_replies_and_ignore_stops(db):
    ids = _leads(db, 4)
    exp = _start(db)
    sent = {lid: _send(lid)["test"]["letter"] for lid in ids}
    a = [lid for lid, x in sent.items() if x == "A"]
    b = [lid for lid, x in sent.items() if x == "B"]
    with db() as s:
        s.get(Lead, a[0]).status = "replied"  # marked by hand
        s.add(Activity(lead_id=a[1], kind="status", text="contacted → interview"))
        s.get(Lead, a[1]).status = "lost"
        s.add(Message(lead_id=b[0], direction="in", status="received", body="stop"))
        s.get(Lead, b[0]).do_not_contact = True
        s.commit()
    res = client.get("/api/experiments").json()["items"][0]["results"]
    rows = {r["letter"]: r for r in res["versions"]}
    assert (rows["A"]["sent"], rows["A"]["replied"], rows["A"]["interested"]) == (2, 2, 1)
    assert (rows["B"]["replied"], rows["B"]["stopped"]) == (0, 1)
    assert res["verdict"]["state"] == "early"
    assert rows["A"]["chance_best"] > rows["B"]["chance_best"]
    assert exp["id"] == client.get("/api/experiments").json()["items"][0]["id"]


def test_clear_winner_and_stopping_makes_it_the_default(db):
    best = experiments.chance_best([(15, 60), (2, 60)], seed=1)[0]
    assert best[0] > 0.99
    assert experiments.needed_each(0.05) > 300  # small rates need many emails

    (lid,) = _leads(db, 1)
    exp = _start(db)
    winner = exp["template_ids"][1]
    r = client.post(f"/api/experiments/{exp['id']}/stop",
                    json={"winner_id": winner, "archive_losers": True})  # fmt: skip
    assert r.json()["status"] == "stopped"
    with db() as s:
        lead = s.get(Lead, lid)
        assert mailer.pick_template(s, lead).id == winner
        assert s.get(Template, exp["template_ids"][0]).archived


def test_one_running_test_per_segment_and_validation(db):
    _start(db)
    r = client.post("/api/experiments", json={
        "name": "x", "kind": "photographer",
        "template_ids": [_tpl(db, "ph_first"), _tpl(db, "ph_straight")],
    })  # fmt: skip
    assert r.status_code == 409
    r = client.post("/api/experiments", json={
        "name": "x", "kind": "agency", "template_ids": [_tpl(db, "ag_first"), _tpl(db, "fu1")],
    })  # fmt: skip
    assert r.status_code == 422


def test_campaigns_feed_the_test(db):
    _leads(db, 6)
    _start(db)
    camp = client.post("/api/campaigns", json={"name": "c", "kind": "photographer"}).json()
    assert camp["test"] == "Lean vs plain"
    client.post(f"/api/campaigns/{camp['id']}/prepare", json={"n": 6})
    with db() as s:
        tags = Counter(m.template_id for m in s.scalars(
            select(Message).where(Message.experiment_id.is_not(None))))  # fmt: skip
        assert sorted(tags.values()) == [3, 3]


def test_sent_from_your_mail_app_counts(db):
    (lid,) = _leads(db, 1)
    _start(db)
    c = client.get(f"/api/compose?lead_id={lid}").json()
    r = client.post("/api/send", json={
        "lead_id": lid, "to": c["to"], "subject": c["subject"], "body": c["body"] + " Merci.",
        "template_id": c["template_id"], "experiment_id": c["test"]["id"],
        "when": "external", "edited": True,
    })  # fmt: skip
    assert r.status_code == 200, r.text
    assert r.json()["lead"]["status"] == "contacted"
    with db() as s:
        msgs = list(s.scalars(select(Message).where(Message.lead_id == lid)))
        assert [(m.status, m.edited, m.from_addr) for m in msgs] == [("sent", True, "your mail app")]
    res = client.get("/api/experiments").json()["items"][0]["results"]
    assert sum(v["sent"] for v in res["versions"]) == 1
    assert sum(v["edited"] for v in res["versions"]) == 1


def test_delete_only_before_sending(db):
    (lid,) = _leads(db, 1)
    exp = _start(db)
    _send(lid)
    assert client.delete(f"/api/experiments/{exp['id']}").status_code == 409
    with db() as s:
        assert s.get(Experiment, exp["id"]) is not None
