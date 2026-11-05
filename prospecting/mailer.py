"""Emails from your own mailbox: templates, the marked photo, sending, follow-ups.

Every email names you and Veralume, says where the address came from and offers a
one-word opt-out (French B2B prospecting rules). Sending is capped per day, kept to
working hours and spaced out, so the mailbox keeps a good reputation.
"""

from __future__ import annotations

import io
import math
import random
import re
import smtplib
import ssl
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from prospecting import pilot
from prospecting.config import settings
from prospecting.contacts import shared_contacts, usable_emails
from prospecting.db import Activity, Campaign, Lead, Message, Photo, Setting, Template, now
from prospecting.text import host

TZ = ZoneInfo(settings.timezone)
UTC = ZoneInfo("UTC")
DEFAULT_SIGNATURE = "Yacine\nVeralume · Strasbourg"
DEFAULT_FOLLOWUPS = [{"days": 4, "template": "fu1"}, {"days": 10, "template": "fu2"}]
DEFAULT_GOALS = {"email": 30, "call": 15, "reply": 3}
OPT_OUT = (
    "Vous ne souhaitez plus recevoir de message de ma part ? "
    "Répondez « stop » et je ne vous écrirai plus."
)


class MailError(Exception):
    """Shown to the user as is: what went wrong and how to fix it."""


# --- settings stored in the database -------------------------------------------------


def get_setting(s: Session, key: str, default: Any) -> Any:
    row = s.get(Setting, key)
    return default if row is None else row.value


def set_setting(s: Session, key: str, value: Any) -> None:
    row = s.get(Setting, key)
    if row is None:
        s.add(Setting(key=key, value=value))
    else:
        row.value = value


# --- time ---------------------------------------------------------------------------


def local(iso_str: str | None = None) -> datetime:
    dt = datetime.fromisoformat(iso_str) if iso_str else datetime.now(TZ)
    return dt.astimezone(TZ)


def day_start(dt: datetime | None = None) -> datetime:
    return (dt or local()).replace(hour=0, minute=0, second=0, microsecond=0)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def in_window(dt: datetime | None = None) -> bool:
    dt = dt or local()
    start, end = settings.send_hours
    return dt.weekday() in settings.send_weekdays and start <= dt.hour < end


def morning(days_from_now: int) -> datetime:
    """A working-day morning, `days_from_now` days ahead, between 9:05 and 9:55."""
    dt = day_start() + timedelta(days=days_from_now, hours=settings.send_hours[0])
    while dt.weekday() not in settings.send_weekdays:
        dt += timedelta(days=1)
    return dt + timedelta(minutes=random.randint(5, 55))


def sent_today(s: Session) -> int:
    return s.scalar(
        select(func.count(Message.id)).where(
            Message.direction == "out", Message.status == "sent",
            Message.sent_at >= iso(day_start()),
        )
    ) or 0  # fmt: skip


# --- rendering -----------------------------------------------------------------------

_NAME = re.compile(r"^[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ'’-]+$")


def first_name(lead: Lead) -> str:
    """The contact's first name if we know it ("" otherwise: the email says "Bonjour,")."""
    for p in lead.people or []:
        parts = p["name"].split()
        if len(parts) >= 2:
            cand = parts[0].capitalize() if parts[0].isupper() else parts[0]
            if _NAME.match(cand):
                return cand
    return ""


def best_photo(s: Session, lead_id: int) -> Photo | None:
    """Their most leaning measured photo: the opener of the email."""
    return s.scalars(
        select(Photo)
        .where(Photo.lead_id == lead_id, Photo.path.is_not(None), Photo.lean_deg.is_not(None),
               Photo.lean_deg >= 1, Photo.lean_deg < 15,
               (Photo.n_lines.is_(None)) | (Photo.n_lines >= 15))
        .order_by(Photo.lean_deg.desc())
    ).first()  # fmt: skip


def variables(s: Session, lead: Lead) -> dict[str, str]:
    lean = lead.lean_median
    return {
        "prenom": first_name(lead),
        "agence": lead.name,
        "ville": lead.city or "",
        "lean": f"{lean:.1f}".replace(".", ",") + "°" if lean is not None else "quelques degrés",
        "n": str(lead.photos_measured or "plusieurs"),
        "site": host(lead.website) if lead.website else "",
        "signature": get_setting(s, "signature", DEFAULT_SIGNATURE),
    }


def render(text: str, values: dict[str, str]) -> str:
    def fill(m: re.Match[str]) -> str:
        v = values.get(m.group(2))
        if v is None:
            return m.group(0)
        # An empty value takes its leading space with it: "Bonjour {prenom}," → "Bonjour,".
        # Spaces before ? ! : in the template itself stay (French typography).
        return m.group(1) + v if v else ""

    out = re.sub(r"([ \t]*)\{(\w+)\}", fill, text)
    # "{prenom}, votre avis ?" with no first name: don't start a line with a comma.
    out = re.sub(r"(?m)^[ \t]*[,;][ \t]*(\w?)", lambda m: m.group(1).upper(), out)
    return re.sub(r"[ \t]{2,}", " ", out)


def footer(lead: Lead, to: str) -> str:
    src = next(
        (e.get("source") for e in lead.emails or [] if e["value"].lower() == to.lower()), None
    )
    if src and src.startswith("http"):
        where = host(src)
    elif lead.website:
        where = host(lead.website)
    else:
        where = None
    origin = f" · votre adresse figure sur {where}" if where else ""
    return f"{OPT_OUT}\n{settings.mail_from_name}, {settings.sender_city}{origin}"


def fits_lead(t: Template, lead: Lead) -> bool:
    """Whether a template says nothing untrue about this lead: it may quote the lean only
    when we measured a visible one, and call the photos straight only when measured so."""
    if t.key == "after_voicemail":
        return False
    lean = lead.lean_median
    if "{lean}" in t.body + t.subject and (lean is None or lean < 1):
        return False
    if t.key == "ph_straight" and (lean is None or lean >= 1):
        return False
    return True


def pick_template(s: Session, lead: Lead, step: int = 0) -> Template | None:
    """The template that fits this lead: its segment first, and one that talks about the
    lean only when we measured a visible lean (1° or more) on their photos. A first email
    that won an A/B test for this segment comes first whenever it fits."""
    if step == 0:
        won = get_setting(s, f"first_template:{lead.kind}", None)
        t = s.get(Template, won) if won else None
        if t is not None and not t.archived and t.step == 0 and fits_lead(t, lead):
            return t
    rows = list(
        s.scalars(select(Template).where(Template.step == step, Template.archived.is_(False),
                                  Template.campaign_id.is_(None))
                  .order_by(Template.id))
    )
    rows = [t for t in rows if t.key != "after_voicemail"]
    if lead.lean_median is None:  # never claim their photos are straight without measuring
        rows = [t for t in rows if t.key != "ph_straight"]
    else:
        rows = [t for t in rows if t.key != "ph_plain"]
    leaning = lead.lean_median is not None and lead.lean_median >= 1
    fits = [t for t in rows if ("{lean}" in t.body) == leaning] or rows
    seg = "agency" if lead.kind == "independent_agent" else lead.kind
    for pool in (
        [t for t in fits if t.segment == lead.kind],
        [t for t in fits if t.segment == seg],
        [t for t in rows if t.segment == lead.kind],
        [t for t in fits if t.segment == "any"],
        [t for t in rows if t.segment == "any"],
    ):
        if pool:
            return pool[0]
    return None


def draft(s: Session, lead: Lead, template: Template) -> dict[str, Any]:
    """Subject and body for one lead, ready to edit."""
    v = variables(s, lead)
    assets = list(template.asset_ids or [])
    v["nb_images"] = str(len(assets))
    photo = best_photo(s, lead.id) if template.attach_photo else None
    return {
        "subject": render(template.subject, v),
        "body": render(template.body, v),
        "attach_photo_id": photo.id if photo else None,
        "asset_ids": assets,
    }


# --- the marked photo ----------------------------------------------------------------


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("DejaVuSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def marked_photo(photo: Photo) -> bytes:
    """Their photo with a true vertical (green, dashed) beside a line leaning by the
    measured median (red), and the angle written on it. JPEG bytes."""
    im = Image.open(settings.data_dir / photo.path).convert("RGB")
    im.thumbnail((1400, 1400))
    w, h = im.size
    d = ImageDraw.Draw(im)
    lean = photo.lean_deg or 0.0
    top, bottom = h * 0.08, h * 0.80
    dx = math.tan(math.radians(lean)) * (bottom - top) / 2
    lw = max(3, w // 300)
    for x, sign in ((w * 0.12, -1), (w * 0.88, 1)):
        y = top
        while y < bottom:  # dashed true vertical
            d.line([(x, y), (x, min(y + h * 0.03, bottom))], fill=(32, 212, 122), width=lw)
            y += h * 0.05
        d.line([(x - sign * dx, top), (x + sign * dx, bottom)], fill=(255, 92, 70), width=lw + 1)
    label = f"Verticales inclinées de {lean:.1f}°".replace(".", ",")
    f = _font(max(18, w // 38))
    size = getattr(f, "size", 18)
    tw = d.textlength(label, font=f)
    pad = max(8, w // 80)
    box = (pad, h - pad * 2 - size * 1.6, pad * 3 + tw, h - pad)
    d.rounded_rectangle(box, radius=pad, fill=(10, 15, 13))
    d.text((pad * 2, box[1] + (box[3] - box[1] - size) / 2 - 2), label, font=f, fill="white")
    out = io.BytesIO()
    im.save(out, "JPEG", quality=85)
    return out.getvalue()


# --- sending -------------------------------------------------------------------------


def build(msg: Message, lead: Lead, s: Session) -> EmailMessage:
    sender = settings.sender
    if not sender:
        raise MailError("Set PROSPECT_SMTP_USER (or PROSPECT_MAIL_FROM) in .env to send emails.")
    em = EmailMessage()
    em["From"] = formataddr((settings.mail_from_name, sender))
    em["To"] = msg.to_addr
    em["Subject"] = msg.subject
    em["Message-ID"] = msg.message_id or make_msgid(domain=sender.rsplit("@", 1)[-1])
    if msg.in_reply_to:
        em["In-Reply-To"] = msg.in_reply_to
        em["References"] = msg.in_reply_to
    em["Date"] = formatdate(localtime=True)
    # No List-Unsubscribe header: Gmail files mail carrying it under Promotions. A personal
    # one-to-one email doesn't need it; the opt-out sentence in the footer is what the law asks.
    em.set_content(f"{msg.body.rstrip()}\n\n--\n{footer(lead, msg.to_addr or '')}\n")
    if msg.attach_photo_id:
        photo = s.get(Photo, msg.attach_photo_id)
        if photo and photo.path:
            name = re.sub(r"[^a-z0-9-]", "", (lead.city or "annonce").lower().replace(" ", "-"))
            em.add_attachment(
                marked_photo(photo), maintype="image", subtype="jpeg",
                filename=f"votre-photo-{name or 'annonce'}.jpg",
            )  # fmt: skip
    try:
        files = pilot.attachments(s, msg)
    except pilot.FileError as e:
        raise MailError(str(e)) from e
    for fname, data in files:
        sub = {".png": "png", ".webp": "webp"}.get(Path(fname).suffix.lower(), "jpeg")
        em.add_attachment(data, maintype="image", subtype=sub, filename=fname)
    return em


def deliver(em: EmailMessage) -> None:
    if settings.mail_dry_run:
        settings.outbox_dir.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^\w.-]", "_", em["Message-ID"].strip("<>"))
        (settings.outbox_dir / f"{name}.eml").write_bytes(bytes(em))
        return
    if not settings.mail_configured:
        raise MailError(
            "Your mailbox isn't set up. Add PROSPECT_SMTP_HOST, PROSPECT_SMTP_USER and "
            "PROSPECT_SMTP_PASSWORD to .env, then restart the app."
        )
    ctx = ssl.create_default_context()
    try:
        if settings.smtp_port == 465:
            with smtplib.SMTP_SSL(settings.smtp_host, 465, context=ctx, timeout=30) as smtp:
                smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.send_message(em)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
                smtp.starttls(context=ctx)
                smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.send_message(em)
    except smtplib.SMTPAuthenticationError as e:
        raise MailError("The mailbox refused the login. Check the app password in .env.") from e
    except (smtplib.SMTPException, OSError) as e:
        raise MailError(f"Sending failed: {e}") from e


def send(s: Session, msg: Message, followups: bool = True) -> Message:
    """Send one outbound message now (within the daily cap) and record what follows.
    The caller commits."""
    lead = s.get(Lead, msg.lead_id)
    if followups and msg.campaign_id:  # a campaign can go without follow-ups
        camp = s.get(Campaign, msg.campaign_id)
        followups = not (camp and camp.followups_on is False)
    if lead is None or lead.do_not_contact:
        msg.status, msg.error = "cancelled", "do not contact"
        raise MailError("This lead asked not to be contacted.")
    if sent_today(s) >= settings.daily_send_cap:
        raise MailError(
            f"Today's cap of {settings.daily_send_cap} emails is reached. It protects your "
            "mailbox: approve the rest and they go out on the next working day."
        )
    if not msg.to_addr:
        raise MailError("This lead has no address to send to. Add one in the lead file.")
    em = build(msg, lead, s)
    msg.message_id = em["Message-ID"]
    try:
        deliver(em)
    except MailError as e:
        msg.status, msg.error = "failed", str(e)[:500]
        raise
    msg.from_addr = settings.sender
    return record_sent(s, msg, lead, followups)


def log_external(s: Session, msg: Message) -> Message:
    """You sent this one from your own mail app: record it like a sent email (it counts in
    goals, campaigns and A/B tests) without follow-ups, which the app couldn't send."""
    lead = s.get(Lead, msg.lead_id)
    msg.from_addr = "your mail app"
    return record_sent(s, msg, lead, followups=False, note=f" (from your mail app to {msg.to_addr})")


def record_sent(
    s: Session, msg: Message, lead: Lead, followups: bool = True, note: str = ""
) -> Message:
    msg.status, msg.sent_at, msg.error = "sent", now(), None
    lead.last_contacted_at = msg.sent_at
    label = {0: "Email", 1: "Follow-up 1", 2: "Follow-up 2", -1: "Reply"}.get(msg.step, "Email")
    s.add(Activity(lead_id=lead.id, kind="email", text=f"{label} · {msg.subject}{note}"))
    if lead.status in ("new", "shortlisted"):
        s.add(Activity(lead_id=lead.id, kind="status", text=f"{lead.status} → contacted"))
        lead.status = "contacted"
    if msg.step == 0 and followups:
        schedule_followups(s, lead, msg)
    pilot.after_send(s, lead, msg)
    return msg


def new_outbound(
    s: Session, lead: Lead, *, subject: str, body: str, to: str | None = None, step: int = 0,
    template_id: int | None = None, campaign_id: int | None = None,
    attach_photo_id: int | None = None, in_reply_to: str | None = None,
    status: str = "queued", due_at: str | None = None,
    experiment_id: int | None = None, edited: bool | None = None,
    asset_ids: list[int] | None = None, file_ids: list[int] | None = None,
) -> Message:  # fmt: skip
    to = to or next(iter(usable_emails(lead, shared_contacts()[0])), None)
    msg = Message(
        lead_id=lead.id, direction="out", status=status, to_addr=to, subject=subject, body=body,
        step=step, template_id=template_id, campaign_id=campaign_id,
        experiment_id=experiment_id, edited=edited,
        asset_ids=list(asset_ids or []), file_ids=list(file_ids or []),
        attach_photo_id=attach_photo_id, in_reply_to=in_reply_to, due_at=due_at or now(),
    )  # fmt: skip
    s.add(msg)
    s.flush()
    return msg


def schedule_followups(s: Session, lead: Lead, first: Message) -> list[Message]:
    """Write the follow-ups now; each waits for review in Today on its day."""
    out = []
    for i, f in enumerate(get_setting(s, "followups", DEFAULT_FOLLOWUPS), start=1):
        t = s.scalar(select(Template).where(Template.key == f["template"]))
        if t is None or t.archived:
            continue
        d = draft(s, lead, t)
        subj = first.subject if first.subject.lower().startswith("re:") else f"Re: {first.subject}"
        out.append(new_outbound(
            s, lead, subject=subj, body=d["body"], to=first.to_addr, step=i,
            template_id=t.id, campaign_id=first.campaign_id,
            attach_photo_id=d["attach_photo_id"] or (first.attach_photo_id if i == 1 else None),
            asset_ids=d["asset_ids"],
            in_reply_to=first.message_id, due_at=iso(morning(int(f["days"]))),
        ))  # fmt: skip
    return out


def cancel_pending(s: Session, lead_id: int, reason: str) -> int:
    rows = list(
        s.scalars(
            select(Message).where(
                Message.lead_id == lead_id, Message.direction == "out",
                Message.status.in_(("queued", "approved")),
            )
        )
    )  # fmt: skip
    for m in rows:
        m.status, m.error = "cancelled", reason
    return len(rows)


_NEXT_SLOT: dict[str, datetime] = {}


def send_due(s: Session) -> Message | None:
    """Send the oldest approved message if the window, the cap and the gap allow.
    Called every few seconds by the worker; sends at most one. The caller commits."""
    if not settings.mail_configured or not in_window():
        return None
    if _NEXT_SLOT.get("at") and local() < _NEXT_SLOT["at"]:
        return None
    if sent_today(s) >= settings.daily_send_cap:
        return None
    msg = s.scalars(
        select(Message)
        .where(Message.direction == "out", Message.status == "approved", Message.due_at <= now())
        .order_by(Message.due_at)
    ).first()
    if msg is None:
        return None
    try:
        send(s, msg)
    except MailError:
        pass  # recorded on the message as failed or cancelled
    _NEXT_SLOT["at"] = local() + timedelta(seconds=random.randint(*settings.send_gap_s))
    return msg


def ensure_templates(s: Session) -> None:
    """Seed the French templates (first run, and any added in a later version)."""
    have = set(s.scalars(select(Template.key)))
    for t in SEED_TEMPLATES:
        if t["key"] not in have:
            s.add(Template(**t))
    s.commit()


def outbox_files() -> list[Path]:
    return sorted(settings.outbox_dir.glob("*.eml")) if settings.outbox_dir.exists() else []


SEED_TEMPLATES: list[dict[str, Any]] = [
    {"key": "ph_first", "name": "Photographers · first email", "segment": "photographer",
     "step": 0, "subject": "Vos photos à {ville}", "body": """Bonjour {prenom},

J'ai regardé les photos immobilières sur votre site. Sur {n} d'entre elles, les verticales penchent en moyenne de {lean} : l'effet grand-angle classique, les murs « tombent ». Je vous joins une de vos photos avec les lignes tracées.

Je lance Veralume, un service qui redresse les verticales et la perspective de vos photos, livrées en 24 h. Je cherche une dizaine de photographes pour un pilote gratuit : vous m'envoyez une séance, je vous renvoie les photos corrigées.

Ça vous dirait d'essayer sur votre prochain shooting ?

{signature}"""},
    {"key": "ph_straight", "name": "Photographers · straight already", "segment": "photographer",
     "step": 0, "attach_photo": False, "subject": "Retouche immobilière en 24 h",
     "body": """Bonjour {prenom},

Vos verticales sont déjà impeccables, ça se voit tout de suite sur votre site.

Je lance Veralume, un service de retouche pour photographes immobiliers : redressement et perspective, livrées en 24 h. L'idée est de vous rendre les heures passées sur Lightroom après chaque visite.

Je cherche une dizaine de photographes pour un pilote gratuit. Une séance test vous intéresserait ?

{signature}"""},
    {"key": "ph_plain", "name": "Photographers · photos not measured", "segment": "photographer",
     "step": 0, "attach_photo": False, "subject": "Retouche immobilière en 24 h",
     "body": """Bonjour {prenom},

Je lance Veralume, un service de retouche pour photographes immobiliers : verticales redressées, perspective corrigée, photos livrées en 24 h. L'idée est de vous rendre les heures passées sur Lightroom après chaque visite.

Je cherche une dizaine de photographes pour un pilote gratuit : vous m'envoyez une séance, je vous renvoie les photos corrigées.

Ça vous dirait d'essayer sur votre prochain shooting ?

{signature}"""},
    {"key": "ag_first", "name": "Agencies · first email", "segment": "agency", "step": 0,
     "subject": "Les photos de vos annonces à {ville}", "body": """Bonjour {prenom},

En parcourant les annonces de {agence}, j'ai mesuré les photos : les verticales y penchent en moyenne de {lean}. Des murs qui tombent, ça se remarque sur une annonce. Je vous joins un exemple avec les lignes tracées.

Je lance Veralume, un service qui redresse vos photos d'annonce en 24 h, sans repasser chez le vendeur. Je cherche quelques agences du Grand Est pour un pilote gratuit.

Je peux vous montrer le résultat sur une de vos annonces ?

{signature}"""},
    {"key": "ag_plain", "name": "Agencies · photos not measured", "segment": "agency",
     "step": 0, "attach_photo": False, "subject": "Les photos de vos annonces à {ville}",
     "body": """Bonjour {prenom},

Je lance Veralume, un service qui retouche les photos d'annonces immobilières en 24 h : verticales redressées, perspective corrigée, sans repasser chez le vendeur.

Je cherche quelques professionnels de la région pour un pilote gratuit : vous m'envoyez les photos d'un bien, je vous les renvoie corrigées.

Ça vous dirait d'essayer sur votre prochaine annonce ?

{signature}"""},
    {"key": "ind_first", "name": "Independent agents · first email",
     "segment": "independent_agent", "step": 0, "subject": "Les photos de vos annonces",
     "body": """Bonjour {prenom},

J'ai regardé les photos de vos annonces : les verticales y penchent en moyenne de {lean}, l'effet grand-angle du téléphone. Je vous joins une de vos photos avec les lignes tracées.

Je lance Veralume, un service qui redresse vos photos en 24 h, sans logiciel à apprendre. Je cherche quelques conseillers pour un pilote gratuit : vous m'envoyez les photos d'un bien, je vous les renvoie corrigées.

Ça vous tente ?

{signature}"""},
    {"key": "fu1", "name": "Follow-up 1 · day 4", "segment": "any", "step": 1,
     "subject": "Re: (subject of the first email)", "body": """Bonjour {prenom},

Je me permets de revenir vers vous. Je vous remets votre photo en pièce jointe : redressée, elle met vraiment le bien en valeur.

Un essai gratuit sur vos prochaines photos, ça vous dit ?

{signature}"""},
    {"key": "fu2", "name": "Follow-up 2 · day 10", "segment": "any", "step": 2,
     "attach_photo": False, "subject": "Re: (subject of the first email)",
     "body": """Bonjour {prenom},

Dernier message de ma part, promis. Si la retouche de vos photos n'est pas un sujet en ce moment, aucun souci.

Si ça le devient, répondez simplement à ce mail : le pilote gratuit reste ouvert.

{signature}"""},
    {"key": "after_voicemail", "name": "After a voicemail", "segment": "any", "step": 0,
     "subject": "Suite à mon message vocal", "body": """Bonjour {prenom},

Je viens de vous laisser un message. Je lance Veralume, un service qui redresse les photos immobilières en 24 h, et je cherche quelques professionnels de la région pour un pilote gratuit.

Je vous joins une de vos photos avec les lignes tracées : les verticales penchent de {lean}. Ça vous intéresse d'essayer ?

{signature}"""},
    {"key": "reply", "name": "Reply · offer the free trial", "segment": "any", "step": -1,
     "attach_photo": False, "subject": "Re:", "body": """Bonjour {prenom},

Merci pour votre retour !

Le plus simple : envoyez-moi une séance ou les photos d'un bien par WeTransfer, je vous les renvoie corrigées sous 24 h, gratuitement.

{signature}"""},
]  # fmt: skip
