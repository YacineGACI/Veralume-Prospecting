"""Routes for outreach: Today, the composer, the review queue, call mode, the pipeline,
campaigns, templates, insights and settings."""

from __future__ import annotations

import re
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from prospecting import inbox, mailer, outreach, worker
from prospecting.config import settings
from prospecting.contacts import shared_contacts, usable_emails
from prospecting.db import KINDS, STATUSES, Campaign, Lead, Message, Photo, Template, now, session
from prospecting.mailer import MailError
from prospecting.outreach import summary

router = APIRouter(prefix="/api")


def _lead(s, lead_id: int) -> Lead:
    lead = s.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(404, "No lead with this id")
    return lead


def _msg(m: Message) -> dict[str, Any]:
    return {
        "id": m.id, "lead_id": m.lead_id, "direction": m.direction, "status": m.status,
        "subject": m.subject, "body": m.body, "to": m.to_addr, "step": m.step,
        "due_at": m.due_at, "sent_at": m.sent_at, "error": m.error,
        "campaign_id": m.campaign_id, "template_id": m.template_id,
        "attach_photo_id": m.attach_photo_id,
    }  # fmt: skip


def mailbox_status(s) -> dict[str, Any]:
    return {
        "configured": settings.mail_configured, "dry_run": settings.mail_dry_run,
        "sender": settings.sender, "imap": bool(settings.imap_host),
        "sent_today": mailer.sent_today(s), "cap": settings.daily_send_cap,
        "in_window": mailer.in_window(), "hours": settings.send_hours,
        "approved_waiting": outreach.approved_waiting(s),
        "inbox": dict(inbox.STATUS), "worker": dict(worker.STATUS),
    }  # fmt: skip


# --- today ---------------------------------------------------------------------------


@router.get("/today")
def today(segment: str | None = None) -> dict[str, Any]:
    shared = shared_contacts()
    with session() as s:
        seg = segment or mailer.get_setting(s, "segment", "photographer")
        goals = mailer.get_setting(s, "goals", mailer.DEFAULT_GOALS)
        replies = outreach.replies_to_answer(s)
        ready = outreach.ready_to_send(s)
        actions = outreach.due_actions(s)
        busy = {l.id for l, _ in replies} | {m.lead_id for m in ready}
        fresh = outreach.fresh_leads(s, seg, 8, exclude=busy)
        ids = {m.lead_id for m in ready}
        leads = {l.id: l for l in s.scalars(select(Lead).where(Lead.id.in_(ids)))} if ids else {}
        return {
            "segment": seg, "goals": goals,
            "done": {
                "email": outreach.count_today(s, ("email",)),
                "call": outreach.count_today(s, ("call",)),
                "reply": outreach.count_today(s, ("email_in",)),
            },
            "streak": outreach.streak(s), "week": outreach.week(s), "funnel": outreach.funnel(s),
            "replies": [
                {"lead": summary(l, shared),
                 "message": {"id": m.id, "subject": m.subject, "at": m.sent_at,
                             "snippet": re.sub(r"\s+", " ", m.body)[:220]}}
                for l, m in replies
            ],
            "ready": [{"lead": summary(leads[m.lead_id], shared), "message": _msg(m)}
                      for m in ready if m.lead_id in leads],
            "actions": [summary(l, shared) for l in actions],
            "fresh": [summary(l, shared) for l in fresh],
            "opener": outreach.opener(s),
            "mailbox": mailbox_status(s),
        }  # fmt: skip


# --- composing and sending -----------------------------------------------------------


@router.get("/compose")
def compose(lead_id: int, template_id: int | None = None) -> dict[str, Any]:
    """A ready-to-edit email for one lead: the right template, filled in, with the photo
    to attach and the addresses to choose from."""
    se, _ = shared_contacts()
    with session() as s:
        lead = _lead(s, lead_id)
        last_in = s.scalars(
            select(Message).where(Message.lead_id == lead.id, Message.direction == "in")
            .order_by(Message.id.desc())
        ).first()  # fmt: skip
        first_sent = s.scalars(
            select(Message).where(Message.lead_id == lead.id, Message.direction == "out",
                                  Message.status == "sent").order_by(Message.id)
        ).first()  # fmt: skip
        mode = "reply" if last_in else ("followup" if first_sent else "first")
        if template_id:
            t = s.get(Template, template_id)
        else:
            t = mailer.pick_template(s, lead, {"reply": -1, "followup": 1, "first": 0}[mode])
        if t is None:
            raise HTTPException(404, "No template fits this lead. Add one in Campaigns.")
        d = mailer.draft(s, lead, t)
        subject, in_reply_to, step = d["subject"], None, 0
        if mode == "reply":
            subj = last_in.subject or ""
            subject = subj if subj.lower().startswith("re:") else f"Re: {subj}"
            in_reply_to, step = last_in.message_id, -1
        elif first_sent:
            subject = f"Re: {first_sent.subject}" if t.step != 0 else d["subject"]
            in_reply_to = first_sent.message_id if t.step != 0 else None
            step = 1
        seg = "agency" if lead.kind == "independent_agent" else lead.kind
        tpls = s.scalars(
            select(Template).where(Template.archived.is_(False)).order_by(Template.step, Template.id)
        )
        photo = s.get(Photo, d["attach_photo_id"]) if d["attach_photo_id"] else None
        photo = photo or mailer.best_photo(s, lead.id)
        best = next(iter(usable_emails(lead, se)), None)
        return {
            "lead": summary(lead), "mode": mode, "template_id": t.id, "step": step,
            "subject": subject, "body": d["body"], "in_reply_to": in_reply_to,
            "to": best, "to_options": [e["value"] for e in lead.emails or []],
            "usable": usable_emails(lead, se), "footer": mailer.footer(lead, best or ""),
            "photo": {"id": photo.id, "lean": photo.lean_deg,
                      "src": f"/api/photos/{photo.id}/marked.jpg"} if photo else None,
            "attach": bool(d["attach_photo_id"]),
            "last_in": {"subject": last_in.subject, "body": last_in.body[:1500],
                        "at": last_in.sent_at} if last_in else None,
            "templates": [{"id": x.id, "name": x.name, "step": x.step}
                          for x in tpls if x.segment in ("any", lead.kind, seg)],
            "followups": mailer.get_setting(s, "followups", mailer.DEFAULT_FOLLOWUPS),
            "mailbox": mailbox_status(s),
        }  # fmt: skip


class SendIn(BaseModel):
    lead_id: int
    to: str
    subject: str
    body: str
    template_id: int | None = None
    attach_photo_id: int | None = None
    in_reply_to: str | None = None
    step: int = 0
    followups: bool = True
    when: str = "now"  # now | queue (approved: goes out at the next free slot)


@router.post("/send")
def send(b: SendIn) -> dict[str, Any]:
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", b.to.strip()):
        raise HTTPException(422, "That address doesn't look like an email.")
    if not b.subject.strip() or not b.body.strip():
        raise HTTPException(422, "Write a subject and a message first.")
    with session() as s:
        lead = _lead(s, b.lead_id)
        if lead.do_not_contact:
            raise HTTPException(409, "This lead asked not to be contacted.")
        already = s.scalar(
            select(Message.id).where(Message.lead_id == lead.id, Message.direction == "out",
                                     Message.status == "sent")
        )  # fmt: skip
        step = b.step if b.step != 0 or not already else 1  # never restart a sequence
        # The email you wrote replaces whatever was waiting for this lead.
        mailer.cancel_pending(s, lead.id, "replaced by an email you wrote")
        msg = mailer.new_outbound(
            s, lead, subject=b.subject.strip(), body=b.body.strip(), to=b.to.strip().lower(),
            step=step, template_id=b.template_id, attach_photo_id=b.attach_photo_id,
            in_reply_to=b.in_reply_to, status="approved",
        )  # fmt: skip
        if b.when == "now":
            try:
                mailer.send(s, msg, followups=b.followups)
            except MailError as e:
                s.commit()
                raise HTTPException(409, str(e)) from e
        s.commit()
        return {"message": _msg(msg), "lead": summary(lead), "sent_today": mailer.sent_today(s)}


@router.get("/messages")
def messages(status: str = "queued", due: bool = True, limit: int = 200) -> dict[str, Any]:
    with session() as s:
        q = select(Message).where(Message.direction == "out", Message.status == status)
        if due:
            q = q.where(Message.due_at <= now())
        rows = list(s.scalars(q.order_by(Message.due_at).limit(limit)))
        ids = {m.lead_id for m in rows}
        leads = {l.id: l for l in s.scalars(select(Lead).where(Lead.id.in_(ids)))} if ids else {}
        return {"items": [{"message": _msg(m), "lead": summary(leads[m.lead_id])} for m in rows]}


class IdsIn(BaseModel):
    ids: list[int]


@router.post("/messages/approve")
def approve(b: IdsIn) -> dict[str, Any]:
    """Approve written emails: the worker sends them one by one, spaced out, inside the
    sending window and the daily cap."""
    with session() as s:
        n = 0
        q = select(Message).where(Message.id.in_(b.ids), Message.status == "queued")
        for m in s.scalars(q):
            m.status = "approved"
            n += 1
        s.commit()
        return {"approved": n, "mailbox": mailbox_status(s)}


class MessagePatch(BaseModel):
    subject: str | None = None
    body: str | None = None
    to: str | None = None


def _pending(s, message_id: int) -> Message:
    m = s.get(Message, message_id)
    if m is None or m.status not in ("queued", "approved", "failed"):
        raise HTTPException(409, "This email was already sent or cancelled.")
    return m


@router.patch("/messages/{message_id}")
def edit_message(message_id: int, p: MessagePatch) -> dict[str, Any]:
    with session() as s:
        m = _pending(s, message_id)
        if p.subject is not None:
            m.subject = p.subject
        if p.body is not None:
            m.body = p.body
        if p.to is not None:
            m.to_addr = p.to.strip().lower()
        s.commit()
        return _msg(m)


@router.post("/messages/{message_id}/send")
def send_one(message_id: int) -> dict[str, Any]:
    with session() as s:
        m = _pending(s, message_id)
        try:
            mailer.send(s, m)
        except MailError as e:
            s.commit()
            raise HTTPException(409, str(e)) from e
        s.commit()
        return _msg(m)


@router.delete("/messages/{message_id}")
def cancel(message_id: int) -> dict[str, Any]:
    with session() as s:
        m = _pending(s, message_id)
        m.status, m.error = "cancelled", "cancelled by you"
        s.commit()
        return _msg(m)


# --- calls ---------------------------------------------------------------------------


@router.get("/calls")
def calls(kind: str = "agency") -> dict[str, Any]:
    with session() as s:
        return {"items": outreach.call_queue(s, kind)}


class CallIn(BaseModel):
    outcome: str
    note: str = ""
    callback_at: str | None = None


@router.post("/leads/{lead_id}/call")
def call(lead_id: int, c: CallIn) -> dict[str, Any]:
    with session() as s:
        lead = _lead(s, lead_id)
        try:
            text = outreach.log_call(s, lead, c.outcome, c.note, c.callback_at)
        except ValueError as e:
            raise HTTPException(422, "Unknown call outcome") from e
        s.commit()
        return {"message": text, "lead": summary(lead)}


# --- pipeline ------------------------------------------------------------------------


@router.get("/pipeline")
def pipeline(kind: str = "") -> dict[str, Any]:
    shared = shared_contacts()
    with session() as s:
        cols: dict[str, list[dict[str, Any]]] = {}
        counts: dict[str, int] = {}
        for st in (*[x for x in STATUSES if x != "shortlisted"], "lost"):
            q = select(Lead).where(Lead.status == st, Lead.do_not_contact.is_(False))
            c = select(func.count(Lead.id)).where(Lead.status == st, Lead.do_not_contact.is_(False))
            if kind:
                q, c = q.where(Lead.kind == kind), c.where(Lead.kind == kind)
            order = (Lead.score.desc(),) if st == "new" else (Lead.updated_at.desc(),)
            rows = s.scalars(q.order_by(*order).limit(8 if st == "new" else 80))
            cols[st] = [summary(l, shared) for l in rows]
            counts[st] = s.scalar(c) or 0
        return {"columns": cols, "counts": counts}


# --- templates -----------------------------------------------------------------------


def _tpl(t: Template) -> dict[str, Any]:
    return {"id": t.id, "key": t.key, "name": t.name, "segment": t.segment, "step": t.step,
            "subject": t.subject, "body": t.body, "attach_photo": t.attach_photo,
            "archived": t.archived}  # fmt: skip


@router.get("/templates")
def templates() -> dict[str, Any]:
    with session() as s:
        mailer.ensure_templates(s)
        rows = list(
            s.scalars(select(Template).order_by(Template.archived, Template.step, Template.id))
        )
        sent: dict[int | None, set[int]] = {}
        for tid, lead_id in s.execute(
            select(Message.template_id, Message.lead_id)
            .where(Message.direction == "out", Message.status == "sent")
        ):  # fmt: skip
            sent.setdefault(tid, set()).add(lead_id)
        replied = set(s.scalars(select(Message.lead_id).where(Message.direction == "in")))
        return {
            "items": [
                {**_tpl(t), "sent": len(sent.get(t.id, set())),
                 "replied": len(sent.get(t.id, set()) & replied)}
                for t in rows
            ],
            "signature": mailer.get_setting(s, "signature", mailer.DEFAULT_SIGNATURE),
            "variables": ["prenom", "agence", "ville", "lean", "n", "site", "signature"],
        }  # fmt: skip


class TemplateIn(BaseModel):
    name: str
    segment: str = "any"
    step: int = 0
    subject: str
    body: str
    attach_photo: bool = True
    archived: bool = False


@router.post("/templates")
def new_template(t: TemplateIn) -> dict[str, Any]:
    with session() as s:
        key = re.sub(r"\W+", "_", t.name.lower()).strip("_")[:28] + f"_{int(time.time()) % 10**6}"
        row = Template(key=key, **t.model_dump())
        s.add(row)
        s.commit()
        return _tpl(row)


@router.put("/templates/{template_id}")
def save_template(template_id: int, t: TemplateIn) -> dict[str, Any]:
    with session() as s:
        row = s.get(Template, template_id)
        if row is None:
            raise HTTPException(404, "No such template")
        for k, v in t.model_dump().items():
            setattr(row, k, v)
        s.commit()
        return _tpl(row)


# --- campaigns -----------------------------------------------------------------------


def _camp(s, c: Campaign) -> dict[str, Any]:
    return {"id": c.id, "name": c.name, "kind": c.kind, "filters": c.filters or {},
            "template_id": c.template_id, "daily_cap": c.daily_cap, "status": c.status,
            "stats": outreach.campaign_stats(s, c)}  # fmt: skip


@router.get("/campaigns")
def campaigns() -> dict[str, Any]:
    with session() as s:
        rows = s.scalars(select(Campaign).order_by(Campaign.id.desc()))
        return {"items": [_camp(s, c) for c in rows], "mailbox": mailbox_status(s)}


class CampaignIn(BaseModel):
    name: str
    kind: str
    filters: dict[str, Any] = {}
    template_id: int | None = None
    daily_cap: int = 20
    status: str = "draft"


@router.post("/campaigns")
def new_campaign(c: CampaignIn) -> dict[str, Any]:
    if c.kind not in KINDS:
        raise HTTPException(422, "Unknown segment")
    with session() as s:
        row = Campaign(**c.model_dump())
        s.add(row)
        s.commit()
        return _camp(s, row)


class CampaignPatch(BaseModel):
    name: str | None = None
    filters: dict[str, Any] | None = None
    template_id: int | None = None
    daily_cap: int | None = None
    status: str | None = None


@router.patch("/campaigns/{campaign_id}")
def edit_campaign(campaign_id: int, c: CampaignPatch) -> dict[str, Any]:
    with session() as s:
        row = s.get(Campaign, campaign_id)
        if row is None:
            raise HTTPException(404, "No such campaign")
        for k, v in c.model_dump(exclude_unset=True).items():
            setattr(row, k, v)
        if c.status == "paused":  # approved first emails go back to review
            q = select(Message).where(Message.campaign_id == row.id, Message.step == 0,
                                      Message.status == "approved")  # fmt: skip
            for m in s.scalars(q):
                m.status = "queued"
        s.commit()
        return _camp(s, row)


class PrepareIn(BaseModel):
    n: int | None = None


@router.post("/campaigns/{campaign_id}/prepare")
def prepare(campaign_id: int, p: PrepareIn) -> dict[str, Any]:
    """Write the next batch of first emails now; they wait for review in Today."""
    with session() as s:
        row = s.get(Campaign, campaign_id)
        if row is None:
            raise HTTPException(404, "No such campaign")
        n = outreach.prepare(s, row, p.n)
        s.commit()
        return {"written": n, "campaign": _camp(s, row)}


# --- insights and settings -----------------------------------------------------------


@router.get("/insights")
def insights(days: int = 14) -> dict[str, Any]:
    with session() as s:
        return outreach.insights(s, days)


@router.get("/settings")
def get_settings() -> dict[str, Any]:
    with session() as s:
        return {
            "goals": mailer.get_setting(s, "goals", mailer.DEFAULT_GOALS),
            "segment": mailer.get_setting(s, "segment", "photographer"),
            "signature": mailer.get_setting(s, "signature", mailer.DEFAULT_SIGNATURE),
            "followups": mailer.get_setting(s, "followups", mailer.DEFAULT_FOLLOWUPS),
            "mailbox": mailbox_status(s),
            "from_name": settings.mail_from_name,
        }


class SettingsIn(BaseModel):
    goals: dict[str, int] | None = None
    segment: str | None = None
    signature: str | None = None
    followups: list[dict[str, Any]] | None = None


@router.put("/settings")
def put_settings(b: SettingsIn) -> dict[str, Any]:
    with session() as s:
        for k, v in b.model_dump(exclude_none=True).items():
            if k == "segment" and v not in KINDS:
                raise HTTPException(422, "Unknown segment")
            mailer.set_setting(s, k, v)
        s.commit()
    return get_settings()


@router.post("/inbox/check")
def check_inbox() -> dict[str, Any]:
    """Read the inbox now instead of waiting for the next poll."""
    if not settings.imap_host:
        raise HTTPException(409, "Set PROSPECT_IMAP_HOST in .env to read replies.")
    try:
        with session() as s:
            found = inbox.poll(s)
            s.commit()
    except Exception as e:
        inbox.STATUS.update(last_poll=now(), last_error=str(e)[:300])
        raise HTTPException(502, f"Couldn't read the inbox: {e}") from e
    inbox.STATUS.update(last_poll=now(), last_error=None, last_found=found)
    return {"found": found}
