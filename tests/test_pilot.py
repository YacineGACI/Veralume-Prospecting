"""Before/after images in templates, the photos leads send us, and the pilot's steps.
Dry-run mailbox: emails are written to the test outbox, never sent."""

import email
import io
import zipfile
from email.message import EmailMessage
from email.policy import default as default_policy

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from prospecting import inbox, mailer
from prospecting.api.app import app
from prospecting.db import Lead, LeadFile, Message, Template

client = TestClient(app)


def jpeg(color=(200, 190, 180), size=(800, 600)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def add_lead(db) -> int:
    with db() as s:
        lead = Lead(
            kind="photographer", name="Studio Lumière", city="Colmar", status="new",
            emails=[{"value": "claire@studio-lumiere.fr", "source": "https://studio-lumiere.fr"}],
            phones=[], people=[{"name": "Claire Martin", "role": None, "source": "x"}],
            tags=[], sources=[], website="https://studio-lumiere.fr", score=70,
            do_not_contact=False,
        )  # fmt: skip
        s.add(lead)
        s.commit()
        return lead.id


def last_eml() -> email.message.EmailMessage:
    return email.message_from_bytes(mailer.outbox_files()[-1].read_bytes(), policy=default_policy)


def send(lead_id: int, **kw) -> dict:
    c = client.get("/api/compose", params={"lead_id": lead_id}).json()
    body = {"lead_id": lead_id, "to": c["to"], "subject": c["subject"], "body": c["body"],
            "template_id": c["template_id"], "step": c["step"], "in_reply_to": c["in_reply_to"],
            "asset_ids": [a["id"] for a in c["assets"]], "followups": False, **kw}  # fmt: skip
    r = client.post("/api/send", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def reply_with_photos(db, lead_id: int, files: dict[str, bytes], text="Voici mes photos") -> str:
    with db() as s:
        out = s.scalars(select(Message).where(Message.lead_id == lead_id, Message.direction == "out")
                        .order_by(Message.id.desc())).first()  # fmt: skip
        em = EmailMessage()
        em["From"] = "Claire <claire@studio-lumiere.fr>"
        em["To"] = "moi@veralume.test"
        em["Subject"] = "Re: test"
        em["Message-ID"] = f"<r{len(files)}-{out.id}@studio-lumiere.fr>"
        em["In-Reply-To"] = out.message_id
        em.set_content(f"{text}\nhttps://we.tl/t-AbCd123 \n")
        for name, data in files.items():
            em.add_attachment(data, maintype="image", subtype="jpeg", filename=name)
        what = inbox.handle(s, em)
        s.commit()
        return what


def test_template_images_go_out_with_the_first_email(db):
    lid = add_lead(db)
    a1 = client.post("/api/assets", params={"name": "salon.jpg"}, content=jpeg()).json()
    a2 = client.post("/api/assets", params={"name": "sejour.jpg"}, content=jpeg((90, 90, 90))).json()
    assert client.get(a1["thumb"]).headers["content-type"] == "image/jpeg"
    with db() as s:
        t = s.scalar(select(Template).where(Template.key == "ph_first"))
        tid = t.id
        payload = {"name": t.name, "segment": t.segment, "step": 0, "attach_photo": False,
                   "subject": "{prenom}, votre avis ?",
                   "body": "Bonjour {prenom},\nJe vous joins {nb_images} exemples.\n{signature}",
                   "asset_ids": [a1["id"], a2["id"]]}  # fmt: skip
    assert client.put(f"/api/templates/{tid}", json=payload).json()["asset_ids"] == [a1["id"], a2["id"]]
    c = client.get("/api/compose", params={"lead_id": lid, "template_id": tid}).json()
    assert c["subject"] == "Claire, votre avis ?" and "joins 2 exemples" in c["body"]
    assert [a["id"] for a in c["assets"]] == [a1["id"], a2["id"]]
    send(lid, template_id=tid)
    names = [p.get_filename() for p in last_eml().iter_attachments()]
    assert names == ["avant-apres-1.jpg", "avant-apres-2.jpg"]


def test_campaign_drafts_carry_the_template_images(db):
    lid = add_lead(db)
    a = client.post("/api/assets", params={"name": "x.jpg"}, content=jpeg()).json()
    with db() as s:
        t = s.scalar(select(Template).where(Template.key == "ph_first"))
        t.asset_ids = [a["id"]]
        lead = s.get(Lead, lid)
        d = mailer.draft(s, lead, t)
        m = mailer.new_outbound(s, lead, subject=d["subject"], body=d["body"], asset_ids=d["asset_ids"])
        assert m.asset_ids == [a["id"]]


def test_photos_in_a_reply_are_kept_and_start_the_pilot(db):
    lid = add_lead(db)
    send(lid)
    assert reply_with_photos(db, lid, {"IMG_4102.jpg": jpeg(), "IMG_4107.jpg": jpeg()}) == "reply"
    f = client.get(f"/api/leads/{lid}/files").json()
    assert [x["filename"] for x in f["files"]] == ["IMG_4102.jpg", "IMG_4107.jpg"]
    assert f["stage"] == "photos" and f["counts"]["received"] == 2
    assert f["links"][0]["url"] == "https://we.tl/t-AbCd123"
    assert "retouch their 2 photos" in f["next"]
    z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/leads/{lid}/files.zip").content))
    assert sorted(z.namelist()) == ["IMG_4102.jpg", "IMG_4107.jpg"]
    lead = client.get(f"/api/leads/{lid}").json()
    assert lead["status"] == "pilot" and lead["pilot_stage"] == "photos"


def test_retouched_photos_are_matched_sent_back_and_feedback_follows(db):
    lid = add_lead(db)
    send(lid)
    reply_with_photos(db, lid, {"IMG_4102.jpg": jpeg(), "IMG_4107.jpg": jpeg()})
    up = client.post(f"/api/leads/{lid}/files", params={"name": "IMG_4102-retouche.jpg"},
                     content=jpeg((10, 10, 10))).json()  # fmt: skip
    originals = {x["filename"]: x["id"] for x in up["files"] if x["kind"] == "received"}
    assert up["file"]["original_id"] == originals["IMG_4102.jpg"]
    assert up["stage"] == "retouching" and "last 1 photo" in up["next"]
    send(lid, subject="Vos photos", body="Les voici", asset_ids=[], file_ids=[up["file"]["id"]])
    assert [p.get_filename() for p in last_eml().iter_attachments()] == ["IMG_4102-retouche.jpg"]
    f = client.get(f"/api/leads/{lid}/files").json()
    assert f["stage"] == "sent_back" and f["counts"]["sent"] == 1
    reply_with_photos(db, lid, {}, text="Super résultat !")
    assert client.get(f"/api/leads/{lid}/files").json()["stage"] == "feedback"
    board = client.get("/api/pilots").json()
    assert [c["id"] for c in board["columns"]["feedback"]] == [lid] and board["need"] == 1


def test_files_of_another_lead_are_never_attached(db):
    a, b = add_lead(db), add_lead(db)
    send(a)
    reply_with_photos(db, a, {"IMG_1.jpg": jpeg()})
    with db() as s:
        fid = s.scalar(select(LeadFile.id).where(LeadFile.lead_id == a))
    send(b, subject="x", body="y", file_ids=[fid])
    assert list(last_eml().iter_attachments()) == []


def test_too_big_for_gmail_is_refused_before_sending(db, monkeypatch):
    from prospecting import pilot

    monkeypatch.setattr(pilot, "MAX_EMAIL", 1000)
    lid = add_lead(db)
    a = client.post("/api/assets", params={"name": "big.jpg"}, content=jpeg()).json()
    r = client.post("/api/send", json={"lead_id": lid, "to": "claire@studio-lumiere.fr",
                                       "subject": "s", "body": "b", "asset_ids": [a["id"]]})  # fmt: skip
    assert r.status_code == 422 and "20 MB" in r.json()["detail"]
    with db() as s:
        assert s.scalar(select(Message.id).where(Message.lead_id == lid)) is None


def test_pilot_steps_by_hand_and_the_board(db):
    lid = add_lead(db)
    r = client.put(f"/api/leads/{lid}/pilot", json={"stage": "interested", "due_at": "2026-11-06T09:00:00+00:00"})
    assert r.json()["stage"] == "interested" and r.json()["due_at"].startswith("2026-11-06")
    assert client.put(f"/api/leads/{lid}/pilot", json={"stage": "nope"}).status_code == 422
    assert client.get("/api/pilots").json()["running"] == 1
    client.put(f"/api/leads/{lid}/pilot", json={"stage": "customer"})
    lead = client.get(f"/api/leads/{lid}").json()
    assert lead["status"] == "customer"
    client.put(f"/api/leads/{lid}/pilot", json={"clear": True})
    assert client.get("/api/pilots").json()["running"] == 0


def test_library_refuses_non_images(db):
    r = client.post("/api/assets", params={"name": "notes.pdf"}, content=b"%PDF-1.4")
    assert r.status_code == 422


def test_gmail_wrapped_quote_header_is_cut():
    t = ("Okay, je participe\n\nLe mer. 4 nov. 2026 à 13:58, Yacine | Veralume <y@gmail.com>\n"
         "a écrit :\n> Bonjour")
    assert inbox.new_text(t) == "Okay, je participe"
    assert inbox.new_text("Le mieux est lundi.\nMerci") == "Le mieux est lundi.\nMerci"
