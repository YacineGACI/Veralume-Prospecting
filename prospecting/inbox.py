"""Replies, read from your mailbox over IMAP (read-only: nothing is moved or marked read).

A reply moves the lead to *replied* and cancels its pending follow-ups. A reply that
says "stop" marks the lead Do not contact. A bounce flags the address.
"""

from __future__ import annotations

import email
import imaplib
import re
from datetime import timedelta
from email.message import EmailMessage
from email.policy import default as default_policy
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from typing import Any

from selectolax.lexbor import LexborHTMLParser as HTMLParser
from sqlalchemy import select
from sqlalchemy.orm import Session

from prospecting import pilot
from prospecting.config import settings
from prospecting.contacts import mark
from prospecting.db import Activity, Lead, Message, now
from prospecting.mailer import cancel_pending, iso, local

STATUS: dict[str, Any] = {"last_poll": None, "last_error": None}
_STOP = re.compile(
    r"^\s*(stop\b|désinscri|desinscri|unsubscribe|ne plus (me )?(contacter|écrire)|"
    r"retirez[- ]moi|merci de ne plus)",
    re.IGNORECASE,
)
_QUOTE_START = re.compile(
    r"^(>|-{2,}\s*(original|message d'origine)|le .{5,120} a écrit\s*:|on .{5,120} wrote:|"
    r"de\s*:|from\s*:|envoyé\s*:|--\s*$)",
    re.IGNORECASE,
)
_BOUNCER = re.compile(r"mailer-daemon|postmaster|mail delivery", re.IGNORECASE)
_ADDR = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def body_text(em: EmailMessage) -> str:
    part = em.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    text = part.get_content()
    if part.get_content_type() == "text/html":
        text = HTMLParser(text).text(separator="\n")
    return text


def new_text(text: str) -> str:
    """The reply itself, without the quoted thread below it."""
    lines = []
    raw = text.splitlines()
    for i, line in enumerate(raw):
        # Gmail wraps "Le mer. 4 nov. 2026 à 13:58, Name <addr>" / "a écrit :" on two lines.
        joined = f"{line.strip()} {raw[i + 1].strip()}" if i + 1 < len(raw) else line.strip()
        if _QUOTE_START.match(line.strip()) or (
            re.match(r"^(le|on) ", line.strip(), re.IGNORECASE) and _QUOTE_START.match(joined)
        ):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _ids(value: Any) -> list[str]:
    return re.findall(r"<[^>]+>", str(value or ""))


def _all_text(em: EmailMessage) -> str:
    out = []
    for part in em.walk():
        if part.is_multipart():
            continue
        payload = part.get_payload(decode=True)
        if isinstance(payload, bytes):
            out.append(payload.decode("utf-8", "ignore"))
    return "\n".join(out)


def _bounce(s: Session, em: EmailMessage) -> str | None:
    text = _all_text(em)
    refs = _ids(em["In-Reply-To"]) + _ids(em["References"]) + _ids(text)
    failed = [a.lower() for a in _ADDR.findall(str(em["X-Failed-Recipients"] or ""))]
    failed += [a.lower() for a in re.findall(r"Final-Recipient:\s*rfc822;\s*<?([^\s>]+)", text, re.I)]
    q = select(Message).where(Message.direction == "out", Message.status == "sent")
    ours = list(s.scalars(q.where(Message.message_id.in_(refs)))) if refs else []
    if not ours and failed:
        ours = list(s.scalars(q.where(Message.to_addr.in_(failed))))
    for m in ours:
        lead = s.get(Lead, m.lead_id)
        m.status, m.error = "failed", "bounced"
        mark(lead, "emails", m.to_addr or "", bounced=True)
        cancel_pending(s, lead.id, "address bounced")
        s.add(Activity(lead_id=lead.id, kind="note", text=f"Email to {m.to_addr} bounced"))
    return "bounce" if ours else None


def handle(s: Session, em: EmailMessage) -> str | None:
    """Record one incoming email if it concerns a lead: "reply", "stop", "bounce" or None."""
    mid = str(em["Message-ID"] or "").strip()
    if mid and s.scalar(select(Message.id).where(Message.message_id == mid)):
        return None  # already recorded
    if _BOUNCER.search(str(em["From"] or "")) or em.get_content_type() == "multipart/report":
        return _bounce(s, em)

    # A reply: matched by its thread first, then by the sender's address.
    sender = parseaddr(str(em["From"] or ""))[1].lower()
    refs = _ids(em["In-Reply-To"]) + _ids(em["References"])
    sent = None
    if refs:
        sent = s.scalars(
            select(Message).where(Message.direction == "out", Message.message_id.in_(refs))
        ).first()
    if sent is None and sender:
        sent = s.scalars(
            select(Message)
            .where(Message.direction == "out", Message.status == "sent",
                   Message.to_addr.ilike(sender))
            .order_by(Message.sent_at.desc())
        ).first()  # fmt: skip
    if sent is None:
        return None
    lead = s.get(Lead, sent.lead_id)
    text = new_text(body_text(em))
    try:
        at = iso(parsedate_to_datetime(str(em["Date"]))) if em["Date"] else now()
    except (TypeError, ValueError):
        at = now()
    incoming = Message(
        lead_id=lead.id, direction="in", status="received", from_addr=sender,
        to_addr=", ".join(a for _, a in getaddresses([str(em["To"] or "")])),
        subject=str(em["Subject"] or ""), body=text[:5000], message_id=mid or None,
        in_reply_to=sent.message_id, sent_at=at,
    )  # fmt: skip
    s.add(incoming)
    s.flush()
    s.add(Activity(lead_id=lead.id, kind="email_in", text=re.sub(r"\s+", " ", text)[:280]))
    cancel_pending(s, lead.id, "they replied")
    if _STOP.match(text):
        lead.do_not_contact = True
        lead.next_action = lead.next_action_at = None
        s.add(Activity(lead_id=lead.id, kind="status", text="asked to stop → do not contact"))
        return "stop"
    if lead.status in ("new", "shortlisted", "contacted"):
        s.add(Activity(lead_id=lead.id, kind="status", text=f"{lead.status} → replied"))
        lead.status = "replied"
    pilot.on_reply(s, lead)
    pilot.save_inbound(s, lead, incoming, em)
    return "reply"


def poll(s: Session, days: int = 14) -> dict[str, int]:
    """Read the last `days` of the inbox; record replies and bounces. The caller commits."""
    if not (settings.imap_host and settings.smtp_user and settings.smtp_password):
        return {}
    out_q = select(Message.message_id, Message.to_addr).where(
        Message.direction == "out", Message.status.in_(("sent", "failed"))
    )
    rows = list(s.execute(out_q))
    our_ids = {r[0] for r in rows if r[0]}
    our_to = {r[1].lower() for r in rows if r[1]}
    if not our_ids:
        return {}
    counts: dict[str, int] = {}
    since = (local() - timedelta(days=days)).strftime("%d-%b-%Y")
    with imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port) as imap:
        imap.login(settings.smtp_user, settings.smtp_password)
        imap.select("INBOX", readonly=True)
        _, data = imap.search(None, "SINCE", since)
        nums = data[0].split()[-500:]
        if not nums:
            return counts
        fields = "(MESSAGE-ID FROM IN-REPLY-TO REFERENCES)"
        _, heads = imap.fetch(b",".join(nums), f"(BODY.PEEK[HEADER.FIELDS {fields}])")
        wanted = []
        for item in heads:
            if not isinstance(item, tuple):
                continue
            h = email.message_from_bytes(item[1], policy=default_policy)
            refs = set(_ids(h["In-Reply-To"]) + _ids(h["References"]))
            sender = parseaddr(str(h["From"] or ""))[1].lower()
            if refs & our_ids or sender in our_to or _BOUNCER.search(str(h["From"] or "")):
                wanted.append(item[0].split()[0])
        for num in wanted:
            _, msg = imap.fetch(num, "(BODY.PEEK[])")
            em = email.message_from_bytes(msg[0][1], policy=default_policy)
            what = handle(s, em)
            if what:
                counts[what] = counts.get(what, 0) + 1
                s.flush()
    return counts
