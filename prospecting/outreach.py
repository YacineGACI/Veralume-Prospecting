"""Outreach logic behind the app: who to contact today, the call queue, call outcomes,
campaigns, and the numbers in Insights."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from prospecting import mailer
from prospecting.contacts import flags, mark, shared_contacts, usable_emails, usable_phones
from prospecting.db import (
    CALL_OUTCOMES,
    STATUSES,
    Activity,
    Lead,
    Message,
    Photo,
    Template,
    now,
)  # fmt: skip
from prospecting.mailer import day_start, iso, local

SMALL_NEW = {"new_business", "sole_trader", "recent_profile", "few_reviews", "social_only"}
OUT_KINDS = ("email", "call", "visit")
KIND_LABEL = {"agency": "Agencies", "photographer": "Photographers",
              "independent_agent": "Independents", "other": "Other"}  # fmt: skip


def person(lead: Lead) -> str | None:
    return (lead.people or [{}])[0].get("name") or None


def summary(lead: Lead, shared: tuple[set[str], set[str]] | None = None) -> dict[str, Any]:
    se, sp = shared or shared_contacts()
    return {
        "id": lead.id, "name": lead.name, "person": person(lead), "kind": lead.kind,
        "network": lead.network, "city": lead.city, "dep": (lead.postcode or "")[:2] or None,
        "distance_km": lead.distance_km, "website": lead.website,
        "emails": len(lead.emails or []), "phones": len(lead.phones or []),
        "email_ok": bool(usable_emails(lead, se)), "phone_ok": bool(usable_phones(lead, sp)),
        "flags": flags(lead, se, sp), "small_new": sorted(SMALL_NEW & set(lead.tags or [])),
        "score": lead.score, "lean_median": lead.lean_median,
        "photos_measured": lead.photos_measured, "status": lead.status,
        "do_not_contact": lead.do_not_contact, "enrich_status": lead.enrich_status,
        "last_contacted_at": lead.last_contacted_at, "next_action": lead.next_action,
        "next_action_at": lead.next_action_at, "snoozed_until": lead.snoozed_until,
        "updated_at": lead.updated_at, "pilot_stage": lead.pilot_stage, "pilot_due_at": lead.pilot_due_at,
    }  # fmt: skip


def _not_snoozed():
    return or_(Lead.snoozed_until.is_(None), Lead.snoozed_until <= now())


def _pending_lead_ids(s: Session) -> set[int]:
    return set(
        s.scalars(select(Message.lead_id).where(Message.status.in_(("queued", "approved"))))
    )


def fresh_leads(s: Session, kind: str, n: int = 8, exclude: set[int] | None = None) -> list[Lead]:
    """Best leads never contacted with a usable email, small and new businesses first."""
    se, _ = shared_contacts()
    skip = (exclude or set()) | _pending_lead_ids(s)
    rows = s.scalars(
        select(Lead)
        .where(Lead.kind == kind, Lead.status == "new", Lead.do_not_contact.is_(False),
               Lead.last_contacted_at.is_(None), _not_snoozed())
        .order_by(Lead.score.desc())
    )  # fmt: skip
    out = []
    for lead in rows:
        if lead.id in skip or not usable_emails(lead, se):
            continue
        out.append(lead)
        if len(out) >= n * 4:
            break
    out.sort(key=lambda l: (not (SMALL_NEW & set(l.tags or [])), -l.score))
    return out[:n]


# --- today ---------------------------------------------------------------------------


def count_today(s: Session, kinds: tuple[str, ...]) -> int:
    return s.scalar(
        select(func.count(Activity.id))
        .where(Activity.kind.in_(kinds), Activity.at >= iso(day_start()))
    ) or 0  # fmt: skip


def streak(s: Session) -> int:
    """Working days in a row with at least one email or call (weekends don't break it)."""
    since = iso(day_start() - timedelta(days=120))
    days = {
        local(a).date()
        for a in s.scalars(
            select(Activity.at).where(Activity.kind.in_(OUT_KINDS), Activity.at >= since)
        )
    }
    d, n = local().date(), 0
    if d not in days:
        d -= timedelta(days=1)
    for _ in range(200):
        if d not in days:
            if d.weekday() >= 5:
                d -= timedelta(days=1)
                continue
            break
        n += 1
        d -= timedelta(days=1)
    return n


def week(s: Session, days: int = 7) -> list[dict[str, Any]]:
    start = day_start() - timedelta(days=days - 1)
    rows = s.execute(
        select(Activity.at, Activity.kind).where(
            Activity.at >= iso(start), Activity.kind.in_(("email", "call", "email_in"))
        )
    )
    by: dict[str, Counter[str]] = defaultdict(Counter)
    for at, kind in rows:
        by[local(at).date().isoformat()][kind] += 1
    out = []
    for i in range(days):
        d = (start + timedelta(days=i)).date().isoformat()
        c = by[d]
        out.append({"day": d, "emails": c["email"], "calls": c["call"], "replies": c["email_in"]})
    return out


def funnel(s: Session) -> dict[str, int]:
    rows = dict(
        s.execute(
            select(Lead.status, func.count(Lead.id))
            .where(Lead.do_not_contact.is_(False))
            .group_by(Lead.status)
        ).all()
    )
    return {st: rows.get(st, 0) for st in STATUSES}


def replies_to_answer(s: Session) -> list[tuple[Lead, Message]]:
    """Leads whose latest message is theirs: they are waiting for you."""
    latest = (
        select(Message.lead_id, func.max(Message.id).label("mid"))
        .where(Message.status.in_(("sent", "received")))
        .group_by(Message.lead_id)
        .subquery()
    )
    msgs = s.scalars(
        select(Message)
        .join(latest, Message.id == latest.c.mid)
        .where(Message.direction == "in")
        .order_by(Message.sent_at.desc())
    )
    out = []
    for m in msgs:
        lead = s.get(Lead, m.lead_id)
        if lead and not lead.do_not_contact and lead.status not in ("lost", "customer"):
            out.append((lead, m))
    return out


def ready_to_send(s: Session) -> list[Message]:
    """Written emails waiting for review: follow-ups on their day, campaign batches,
    emails after a voicemail."""
    return list(
        s.scalars(
            select(Message)
            .where(Message.direction == "out", Message.status == "queued", Message.due_at <= now())
            .order_by(Message.step.desc(), Message.due_at)
        )
    )


def approved_waiting(s: Session) -> int:
    return s.scalar(
        select(func.count(Message.id))
        .where(Message.direction == "out", Message.status == "approved")
    ) or 0  # fmt: skip


def due_actions(s: Session) -> list[Lead]:
    """Leads with a call-back or a reminder due by the end of today."""
    end = iso(day_start() + timedelta(days=1))
    return list(
        s.scalars(
            select(Lead)
            .where(Lead.next_action.is_not(None), Lead.next_action_at <= end,
                   Lead.do_not_contact.is_(False))
            .order_by(Lead.next_action_at)
        )
    )  # fmt: skip


def opener(s: Session) -> dict[str, Any] | None:
    """A striking measured photo from a lead worth contacting: the hook shown in Today."""
    row = s.execute(
        select(Photo, Lead)
        .join(Lead, Lead.id == Photo.lead_id)
        .where(Photo.path.is_not(None), Photo.lean_deg.between(3, 12), Photo.n_lines >= 40,
               Lead.do_not_contact.is_(False), Lead.score >= 70)
        .order_by(Lead.score.desc(), Photo.lean_deg.desc())
    ).first()  # fmt: skip
    if row is None:
        return None
    p, lead = row
    return {"lead_id": lead.id, "name": lead.name, "photo_id": p.id, "src": "/" + p.path,
            "lean": p.lean_deg}  # fmt: skip


# --- calls ---------------------------------------------------------------------------


def call_queue(s: Session, kind: str = "agency", n: int = 25) -> list[dict[str, Any]]:
    """Call-backs due first, then leads you put in the queue, then contacted leads who
    haven't answered in 5 days, then the best new leads with a phone."""
    se, sp = shared_contacts()
    end = iso(day_start() + timedelta(days=1))
    out: list[tuple[Lead, str]] = []
    seen: set[int] = set()

    def add(lead: Lead, why: str) -> None:
        if lead.id not in seen and not lead.do_not_contact and usable_phones(lead, sp):
            seen.add(lead.id)
            out.append((lead, why))

    for lead in s.scalars(
        select(Lead).where(Lead.next_action == "call", Lead.next_action_at <= end)
        .order_by(Lead.next_action_at)
    ):  # fmt: skip
        add(lead, "call back" if lead.last_contacted_at else "in your queue")
    five = iso(local() - timedelta(days=5))
    for lead in s.scalars(
        select(Lead)
        .where(Lead.status == "contacted", Lead.last_contacted_at <= five,
               Lead.next_action.is_(None), _not_snoozed())
        .order_by(Lead.score.desc()).limit(60)
    ):  # fmt: skip
        add(lead, "emailed, no reply")
    for lead in s.scalars(
        select(Lead)
        .where(Lead.kind == kind, Lead.status == "new", Lead.do_not_contact.is_(False),
               _not_snoozed())
        .order_by(Lead.score.desc()).limit(400)
    ):  # fmt: skip
        if len(out) >= n:
            break
        add(lead, "new lead")
    res = []
    for lead, why in out[:n]:
        d = summary(lead, (se, sp))
        d.update(why=why, phone=usable_phones(lead, sp)[0],
                 email=next(iter(usable_emails(lead, se)), None))  # fmt: skip
        res.append(d)
    return res


def log_call(
    s: Session, lead: Lead, outcome: str, note: str = "", callback_at: str | None = None
) -> str:
    """Record a call and what follows from it. Returns the message for the toast."""
    if outcome not in CALL_OUTCOMES:
        raise ValueError("unknown outcome")
    _, sp = shared_contacts()
    text = CALL_OUTCOMES[outcome] + (f" · {note.strip()}" if note.strip() else "")
    s.add(Activity(lead_id=lead.id, kind="call", text=text))
    lead.next_action = lead.next_action_at = None
    if outcome not in ("no_answer", "wrong_number"):
        lead.last_contacted_at = now()
        if lead.status in ("new", "shortlisted"):
            s.add(Activity(lead_id=lead.id, kind="status", text=f"{lead.status} → contacted"))
            lead.status = "contacted"
    if outcome == "no_answer":
        lead.next_action, lead.next_action_at = "call", iso(mailer.morning(1) + timedelta(hours=1))
        return "No answer · back in the queue tomorrow morning"
    if outcome == "callback":
        at = callback_at or iso(day_start() + timedelta(days=1, hours=14))
        lead.next_action, lead.next_action_at = "call", at
        return f"Call-back set for {local(at).strftime('%a %d %b, %H:%M')}"
    if outcome == "voicemail":
        t = s.scalar(select(Template).where(Template.key == "after_voicemail"))
        if t and not t.archived and usable_emails(lead, shared_contacts()[0]):
            d = mailer.draft(s, lead, t)
            mailer.new_outbound(s, lead, subject=d["subject"], body=d["body"], template_id=t.id,
                                attach_photo_id=d["attach_photo_id"], asset_ids=d["asset_ids"])  # fmt: skip
            return "Voicemail logged · a short email waits for your review in Today"
        return "Voicemail logged"
    if outcome == "interested":
        order = [*STATUSES]
        if lead.status not in order or order.index(lead.status) < order.index("interview"):
            s.add(Activity(lead_id=lead.id, kind="status", text=f"{lead.status} → interview"))
            lead.status = "interview"
        lead.next_action, lead.next_action_at = "Book the interview", now()
        mailer.cancel_pending(s, lead.id, "interested on the phone")
        return f"{lead.name} → Interview. Set the date in the lead file."
    if outcome == "not_interested":
        s.add(Activity(lead_id=lead.id, kind="status", text=f"{lead.status} → lost"))
        lead.status = "lost"
        mailer.cancel_pending(s, lead.id, "not interested")
        return f"{lead.name} marked lost"
    phone = next(iter(usable_phones(lead, sp)), None)
    if phone:
        mark(lead, "phones", phone, wrong=True)
    return "Phone flagged as wrong"


# --- campaigns -----------------------------------------------------------------------


# --- insights ------------------------------------------------------------------------


def insights(s: Session, days: int = 14) -> dict[str, Any]:
    first = list(
        s.scalars(
            select(Message).where(Message.direction == "out", Message.status == "sent",
                                  Message.step == 0)
        )
    )  # fmt: skip
    replied = set(s.scalars(select(Message.lead_id).where(Message.direction == "in")))
    ids = {m.lead_id for m in first}
    leads = {l.id: l for l in s.scalars(select(Lead).where(Lead.id.in_(ids)))} if ids else {}
    names = {t.id: t.name for t in s.scalars(select(Template))}
    by_kind: dict[str, set[int]] = defaultdict(set)
    by_lean: dict[str, set[int]] = defaultdict(set)
    by_tpl: dict[str, set[int]] = defaultdict(set)
    for m in first:
        lead = leads.get(m.lead_id)
        if lead is None:
            continue
        by_kind[KIND_LABEL.get(lead.kind, "Other")].add(lead.id)
        v = lead.lean_median
        bucket = "Not measured" if v is None else "≥ 3°" if v >= 3 else "1° to 3°" if v >= 1 else "Under 1°"
        by_lean[bucket].add(lead.id)
        by_tpl[names.get(m.template_id, "Written by hand")].add(lead.id)

    def rate(group: dict[str, set[int]], order: list[str] | None = None) -> list[dict[str, Any]]:
        keys = [k for k in (order or sorted(group)) if group.get(k)]
        return [{"label": k, "sent": len(group[k]), "replied": len(group[k] & replied)} for k in keys]

    calls = Counter(
        a.split(" · ")[0] for a in s.scalars(select(Activity.text).where(Activity.kind == "call"))
    )
    return {
        "days": week(s, days), "by_kind": rate(by_kind), "by_template": rate(by_tpl),
        "by_lean": rate(by_lean, ["≥ 3°", "1° to 3°", "Under 1°", "Not measured"]),
        "calls": dict(calls), "funnel": funnel(s),
    }  # fmt: skip
