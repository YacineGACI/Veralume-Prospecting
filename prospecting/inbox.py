"""Replies, read from your mailbox over IMAP (read-only: nothing is moved or marked read).

A reply moves the lead to *replied* and cancels its pending follow-ups. A reply that
says "stop" marks the lead Do not contact. A bounce flags the address.
"""

from __future__ import annotations

import email
import imaplib
import re
from datetime import datetime, timedelta
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
from prospecting.mailer import cancel_pending, get_setting, iso, local, set_setting

STATUS: dict[str, Any] = {"last_poll": None, "last_error": None}
_STOP = re.compile(
    r"^\s*(stopp?\b|désinscri|desinscri|unsubscribe|ne plus (me )?(contacter|écrire)|"
    r"retirez[- ]moi|merci de ne plus|"
    r"remove me|please (don't|do not) (contact|email|write)|no more emails|"  # English
    r"abmelden|austragen|bitte keine|"  # German
    r"afmelden|uitschrijven|niet meer (mailen|contacteren)|"  # Dutch
    r"disiscriv|cancellami|non (mi )?(scriva|contatt)|"  # Italian
    r"baja\b|darme de baja|no me (escriba|contacte)|"  # Spanish
    r"cancelar|não (me )?(contact|escrev)|"  # Portuguese
    r"avregistrera|afmeld|avmeld|avslutt|"  # Swedish, Danish, Norwegian
    r"älä lähetä|peru tilaus|"  # Finnish
    r"wypisz|proszę nie (pisać|kontaktować)|"  # Polish
    r"odhlásit|odhlaste|nepište)",  # Czech
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
    """Every readable line of a bounce: its text parts, the lines of its delivery report
    ("Final-Recipient: rfc822; x@y") and the headers of the email it returns (its
    Message-ID), which the parser keeps as headers, not as text."""
    out = []
    for part in em.walk():
        ctype = part.get_content_type()
        if ctype == "message/delivery-status":
            for block in part.get_payload() or []:
                out.extend(f"{k}: {v}" for k, v in block.items())
            continue
        if ctype == "message/rfc822":
            for inner in part.get_payload() or []:
                out.extend(
                    f"{k}: {v}" for k, v in inner.items() if k.lower() in ("message-id", "to")
                )
            continue
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
    failed += [
        a.lower()
        for a in re.findall(
            r"(?:Final|Original)-Recipient:\s*rfc822;\s*<?([^\s>;]+)", text, re.IGNORECASE
        )
    ]
    q = select(Message).where(Message.direction == "out", Message.status == "sent")
    ours = list(s.scalars(q.where(Message.message_id.in_(refs)))) if refs else []
    if not ours and failed:
        ours = list(s.scalars(q.where(Message.to_addr.in_(failed))))
    if not ours:  # a plain-text bounce: an address we wrote to in the last month, named in it
        since = iso(local() - timedelta(days=30))
        named = {a.lower() for a in _ADDR.findall(text)} - {str(settings.sender or "").lower()}
        if named:
            ours = list(s.scalars(q.where(Message.to_addr.in_(named), Message.sent_at >= since)))
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
    if lead.status in ("new", "shortlisted", "contacted", "no_reply"):
        s.add(Activity(lead_id=lead.id, kind="status", text=f"{lead.status} → replied"))
        lead.status = "replied"
    pilot.on_reply(s, lead)
    pilot.save_inbound(s, lead, incoming, em)
    return "reply"


FULL_EVERY = timedelta(hours=1)  # the whole window is re-read this often, and on start
BATCH = 500  # headers fetched per request
_FULL_DONE = {"since_start": False}  # the first read after the app starts is always full


def _window_start(state: dict[str, Any], days: int) -> Any:
    """14 days back, or further when the last good check is older: after a long stop the
    window reaches back to it (with a day's overlap), so nothing that came meanwhile is lost."""
    start = local() - timedelta(days=days)
    last_ok = state.get("last_ok")
    if last_ok:
        try:
            start = min(start, datetime.fromisoformat(last_ok) - timedelta(days=1))
        except ValueError:
            pass
    return start


def poll(s: Session, days: int = 14, full: bool | None = None) -> dict[str, int]:
    """Read new mail; record replies and bounces. The caller commits.

    Every call reads only what arrived since the last one (Gmail numbers each email, the
    highest one read is kept). Once an hour, on start, or if Gmail renumbered the mailbox,
    the whole window is read again (`full`), in batches, so nothing is ever cut off. Each
    email is recorded once, by its Message-ID, whichever read sees it."""
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
    state = dict(get_setting(s, "inbox_state", None) or {})
    counts: dict[str, int] = {}
    with imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port) as imap:
        imap.login(settings.smtp_user, settings.smtp_password)
        imap.select("INBOX", readonly=True)
        validity = (imap.response("UIDVALIDITY")[1] or [None])[0]
        validity = validity.decode() if isinstance(validity, bytes) else validity
        last_uid = int(state.get("last_uid") or 0)
        if full is None:
            last_full = state.get("last_full")
            full = (
                not last_uid
                or not _FULL_DONE["since_start"]
                or validity != state.get("uidvalidity")  # renumbered: the old number means nothing
                or not last_full
                or datetime.fromisoformat(last_full) < datetime.fromisoformat(now()) - FULL_EVERY
            )
        if full:
            since = _window_start(state, days).strftime("%d-%b-%Y")
            _, data = imap.uid("SEARCH", None, "SINCE", since)
        else:
            _, data = imap.uid("SEARCH", None, "UID", f"{last_uid + 1}:*")
        uids = [u for u in (data[0] or b"").split() if full or int(u) > last_uid]  # "n:*" always
        fields = "(MESSAGE-ID FROM IN-REPLY-TO REFERENCES)"  # returns the last one
        wanted = []
        for i in range(0, len(uids), BATCH):
            _, heads = imap.uid("FETCH", b",".join(uids[i : i + BATCH]),
                                f"(UID BODY.PEEK[HEADER.FIELDS {fields}])")  # fmt: skip
            for item in heads:
                if not isinstance(item, tuple):
                    continue
                uid = re.search(rb"UID (\d+)", item[0])
                h = email.message_from_bytes(item[1], policy=default_policy)
                refs = set(_ids(h["In-Reply-To"]) + _ids(h["References"]))
                sender = parseaddr(str(h["From"] or ""))[1].lower()
                if uid and (
                    refs & our_ids or sender in our_to or _BOUNCER.search(str(h["From"] or ""))
                ):
                    wanted.append(uid.group(1))
        for uid in wanted:
            _, msg = imap.uid("FETCH", uid, "(BODY.PEEK[])")
            em = email.message_from_bytes(msg[0][1], policy=default_policy)
            what = handle(s, em)
            if what:
                counts[what] = counts.get(what, 0) + 1
                s.flush()
    seen = max([int(u) for u in uids], default=0)
    state.update(uidvalidity=validity, last_ok=now(),
                 last_uid=max(seen, last_uid if validity == state.get("uidvalidity") else 0))  # fmt: skip
    if full:
        state["last_full"] = now()
        _FULL_DONE["since_start"] = True
    set_setting(s, "inbox_state", state)
    return counts
