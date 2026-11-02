"""Emails, replies, calls and campaigns, end to end on a throwaway database."""

from email.message import EmailMessage

import pytest
from PIL import Image
from sqlalchemy import select

from prospecting import contacts, inbox, mailer, outreach
from prospecting.config import settings
from prospecting.db import Activity, Campaign, Lead, Message, Photo, Template
from prospecting.mailer import MailError


def add_lead(s, **kw) -> Lead:
    base = dict(
        kind="photographer", name="Studio Lumière", city="Colmar", status="new",
        emails=[{"value": "claire@studio-lumiere.fr", "source": "https://studio-lumiere.fr/contact"}],
        phones=[{"value": "06 12 34 56 78", "source": "x"}],
        people=[{"name": "Claire Martin", "role": None, "source": "x"}],
        tags=["real_estate"], sources=[], website="https://studio-lumiere.fr",
        lean_median=3.4, photos_measured=3, do_not_contact=False, score=80,
    )  # fmt: skip
    lead = Lead(**{**base, **kw})
    s.add(lead)
    s.flush()
    return lead


def add_photo(s, lead: Lead, lean: float = 4.2) -> Photo:
    path = settings.photos_dir / str(lead.id)
    path.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (800, 600), (200, 190, 180)).save(path / "1.jpg")
    p = Photo(lead_id=lead.id, url="https://x/1.jpg", path=f"photos/{lead.id}/1.jpg",
              lean_deg=lean, n_lines=60)  # fmt: skip
    s.add(p)
    s.flush()
    return p


# --- contacts ------------------------------------------------------------------------


def test_head_office_and_privacy_addresses_are_not_used(db):
    with db() as s:
        agent = add_lead(s, kind="independent_agent",
                         emails=[{"value": "contact@safti.fr", "source": "x"}])  # fmt: skip
        dpo = add_lead(s, kind="agency",
                       emails=[{"value": "donneespersonnelles@laforet.com", "source": "x"}])  # fmt: skip
        assert contacts.usable_emails(agent) == []
        assert contacts.usable_emails(dpo) == []
        assert contacts.flags(agent) == ["Email: network head-office address"]


def test_phone_on_many_leads_is_a_switchboard(db):
    with db() as s:
        for i in range(6):
            add_lead(s, name=f"Agent {i}", phones=[{"value": "05 32 09 35 85", "source": "x"}])
        s.commit()
        lead = s.scalars(select(Lead)).first()
        _, sp = contacts.shared_contacts(max_age_s=0)
        assert contacts.usable_phones(lead, sp) == []


# --- rendering -----------------------------------------------------------------------


def test_render_fills_the_lead_and_drops_unknown_first_name(db):
    with db() as s:
        lead = add_lead(s, people=[])
        t = s.scalar(select(Template).where(Template.key == "ph_first"))
        d = mailer.draft(s, lead, t)
        assert d["subject"] == "Vos photos à Colmar"
        assert d["body"].startswith("Bonjour,\n")
        assert "3,4°" in d["body"]
        assert "{" not in d["body"]


def test_straight_photographers_get_the_template_without_lean(db):
    with db() as s:
        lead = add_lead(s, lean_median=0.3)
        assert mailer.pick_template(s, lead).key == "ph_straight"
        assert mailer.pick_template(s, add_lead(s, lean_median=4.0)).key == "ph_first"
        # Never measured: neither "your walls lean" nor "already straight".
        assert mailer.pick_template(s, add_lead(s, lean_median=None)).key == "ph_plain"
        agency = add_lead(s, kind="agency", lean_median=None)
        assert "{lean}" not in mailer.pick_template(s, agency).body


def test_footer_says_where_the_address_came_from(db):
    with db() as s:
        lead = add_lead(s)
        f = mailer.footer(lead, "claire@studio-lumiere.fr")
        assert "stop" in f and "studio-lumiere.fr" in f


def test_marked_photo_is_a_jpeg(db):
    with db() as s:
        p = add_photo(s, add_lead(s))
        assert mailer.marked_photo(p)[:2] == b"\xff\xd8"


# --- sending -------------------------------------------------------------------------


def _send_first(s, lead: Lead) -> Message:
    t = mailer.pick_template(s, lead)
    d = mailer.draft(s, lead, t)
    m = mailer.new_outbound(s, lead, subject=d["subject"], body=d["body"], template_id=t.id,
                            attach_photo_id=d["attach_photo_id"], status="approved")  # fmt: skip
    mailer.send(s, m)
    s.commit()
    return m


def test_send_marks_contacted_and_writes_followups(db):
    with db() as s:
        lead = add_lead(s)
        add_photo(s, lead)
        m = _send_first(s, lead)
        assert m.status == "sent" and m.message_id
        assert lead.status == "contacted" and lead.last_contacted_at
        fus = list(s.scalars(select(Message).where(Message.step > 0)))
        assert [f.step for f in fus] == [1, 2]
        assert all(f.status == "queued" and f.in_reply_to == m.message_id for f in fus)
        assert fus[0].subject == "Re: Vos photos à Colmar"
        eml = mailer.outbox_files()[-1].read_bytes()
        assert b"image/jpeg" in eml and b"stop" in eml


def test_daily_cap_stops_sending(db, monkeypatch):
    monkeypatch.setattr(settings, "daily_send_cap", 1)
    with db() as s:
        _send_first(s, add_lead(s))
        with pytest.raises(MailError, match="cap"):
            _send_first(s, add_lead(s, name="Autre", emails=[{"value": "a@b.fr", "source": "x"}]))


def test_do_not_contact_is_never_sent(db):
    with db() as s:
        lead = add_lead(s, do_not_contact=True)
        m = mailer.new_outbound(s, lead, subject="x", body="y", status="approved")
        with pytest.raises(MailError):
            mailer.send(s, m)
        assert m.status == "cancelled"


# --- replies -------------------------------------------------------------------------


def _reply(to_msg: Message, text: str, sender: str = "claire@studio-lumiere.fr") -> EmailMessage:
    em = EmailMessage()
    em["From"] = f"Claire <{sender}>"
    em["To"] = "moi@veralume.test"
    em["Subject"] = "Re: Vos photos à Colmar"
    em["Message-ID"] = "<reply-1@studio-lumiere.fr>"
    em["In-Reply-To"] = to_msg.message_id
    em.set_content(f"{text}\n\nLe lun. 3 nov. 2026, Veralume a écrit :\n> Bonjour")
    return em


def test_reply_moves_lead_and_cancels_followups(db):
    with db() as s:
        lead = add_lead(s)
        m = _send_first(s, lead)
        assert inbox.handle(s, _reply(m, "Oui, envoyez-moi un exemple !")) == "reply"
        s.commit()
        assert lead.status == "replied"
        pending = s.scalars(select(Message).where(Message.status.in_(("queued", "approved"))))
        assert list(pending) == []
        got = s.scalar(select(Message).where(Message.direction == "in"))
        assert got.body == "Oui, envoyez-moi un exemple !"
        # Recorded once, even if the inbox is read again.
        assert inbox.handle(s, _reply(m, "Oui")) is None
        assert [l.id for l, _ in outreach.replies_to_answer(s)] == [lead.id]


def test_stop_reply_means_do_not_contact(db):
    with db() as s:
        lead = add_lead(s)
        m = _send_first(s, lead)
        assert inbox.handle(s, _reply(m, "STOP merci")) == "stop"
        assert lead.do_not_contact


def test_bounce_flags_the_address(db):
    with db() as s:
        lead = add_lead(s)
        m = _send_first(s, lead)
        em = EmailMessage()
        em["From"] = "Mail Delivery System <MAILER-DAEMON@mail.example>"
        em["Subject"] = "Undelivered Mail Returned to Sender"
        em["Message-ID"] = "<bounce-1@mail.example>"
        em.set_content(f"Final-Recipient: rfc822; claire@studio-lumiere.fr\nMessage-ID: {m.message_id}")
        assert inbox.handle(s, em) == "bounce"
        assert m.status == "failed" and lead.emails[0]["bounced"]
        assert contacts.usable_emails(lead) == []


# --- calls and campaigns -------------------------------------------------------------


def test_call_outcomes(db):
    with db() as s:
        lead = add_lead(s)
        outreach.log_call(s, lead, "no_answer")
        assert lead.next_action == "call" and lead.status == "new"
        outreach.log_call(s, lead, "voicemail")
        assert lead.status == "contacted"
        assert s.scalar(select(Message).where(Message.status == "queued")).subject.startswith("Suite")
        outreach.log_call(s, lead, "interested")
        assert lead.status == "interview" and lead.next_action == "Book the interview"
        assert s.scalar(select(Message).where(Message.status == "queued")) is None
        assert len(list(s.scalars(select(Activity).where(Activity.kind == "call")))) == 3


def test_campaign_writes_first_emails_for_review(db):
    with db() as s:
        for i in range(5):
            add_lead(s, name=f"Studio {i}", emails=[{"value": f"p{i}@studio{i}.fr", "source": "x"}],
                     tags=["real_estate", "new_business"] if i == 4 else ["real_estate"])  # fmt: skip
        add_lead(s, name="Sans email", emails=[])
        c = Campaign(name="Photographes", kind="photographer", daily_cap=3, status="running")
        s.add(c)
        s.flush()
        assert outreach.prepare(s, c) == 3
        assert outreach.prepare(s, c) == 0  # today's batch is written
        queued = list(s.scalars(select(Message).where(Message.status == "queued")))
        assert len(queued) == 3
        # Small and new businesses come first.
        assert s.get(Lead, queued[0].lead_id).name == "Studio 4"
        assert len(outreach.ready_to_send(s)) == 3


def test_fresh_leads_skip_contacted_and_unusable(db):
    with db() as s:
        add_lead(s, name="A")
        add_lead(s, name="B", status="contacted")
        add_lead(s, name="C", emails=[{"value": "contact@safti.fr", "source": "x"}])
        assert [l.name for l in outreach.fresh_leads(s, "photographer")] == ["A"]
