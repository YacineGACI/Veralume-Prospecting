"""The free test ("pilot") with a lead: the photos they send us, our retouched versions,
the library of example images sent with templates, and the pilot's steps.

Files live under data/: assets/ for the library, leadfiles/<lead id>/ for each lead.
"""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from prospecting.config import settings
from prospecting.db import Activity, Asset, Lead, LeadFile, Message

STAGES = ("interested", "photos", "retouching", "sent_back", "feedback", "customer", "lost")
STAGE_LABELS = {
    "interested": "Said yes", "photos": "Photos received", "retouching": "Retouching",
    "sent_back": "Sent back", "feedback": "Feedback", "customer": "Customer", "lost": "Not now",
}  # fmt: skip
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tif", ".tiff", ".dng", ".cr2",
             ".cr3", ".nef", ".arw", ".raf", ".orf", ".rw2"}  # fmt: skip
MAX_FILE = 80 * 1024 * 1024
MAX_EMAIL = 20 * 1024 * 1024  # Gmail refuses more than 25 MB once encoded
# Links to big transfers, which can't be fetched for you (they expire, need a click…).
_LINK = re.compile(
    r"https?://(?:www\.)?(?:we\.tl|wetransfer\.com|drive\.google\.com|photos\.app\.goo\.gl|"
    r"dropbox\.com|db\.tt|swisstransfer\.com|smash\.gg|fromsmash\.com|transfernow\.net|"
    r"1drv\.ms|onedrive\.live\.com|icloud\.com|grosfichiers\.com|filemail\.com)[^\s<>\"')\]]*",
    re.IGNORECASE,
)


class FileError(Exception):
    """A message for the user."""


# --- storing files -------------------------------------------------------------------


def safe_name(name: str) -> str:
    name = unicodedata.normalize("NFKD", Path(name or "photo").name)
    name = name.encode("ascii", "ignore").decode()
    name = re.sub(r"[^\w.-]+", "-", name).strip("-.") or "photo"
    return name[:120]


def abs_path(rel: str) -> Path:
    p = (settings.data_dir / rel).resolve()
    if settings.data_dir.resolve() not in p.parents:
        raise FileError("Bad file path")
    return p


def _write(folder: str, name: str, data: bytes, key: int) -> str:
    rel = f"{folder}/{key}-{safe_name(name)}"
    p = abs_path(rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return rel


def is_image(name: str, content_type: str = "") -> bool:
    return content_type.startswith("image/") or Path(name).suffix.lower() in IMAGE_EXT


def add_asset(s: Session, name: str, data: bytes) -> Asset:
    if not data:
        raise FileError("This file is empty.")
    if not is_image(name):
        raise FileError("Only images go in the library (JPEG, PNG, WebP).")
    if len(data) > 15 * 1024 * 1024:
        raise FileError("This image is over 15 MB: export it smaller (2000 px wide is plenty).")
    a = Asset(name=Path(name).stem[:160] or "image", path="", size=len(data))
    s.add(a)
    s.flush()
    a.path = _write("assets", name, data, a.id)
    return a


def add_file(
    s: Session, lead: Lead, name: str, data: bytes, kind: str = "received",
    message_id: int | None = None,
) -> LeadFile:  # fmt: skip
    if not data:
        raise FileError("This file is empty.")
    if len(data) > MAX_FILE:
        raise FileError(f"{name} is over 80 MB.")
    f = LeadFile(lead_id=lead.id, kind=kind, filename=Path(name).name[:200] or "photo",
                 path="", size=len(data), message_id=message_id)  # fmt: skip
    s.add(f)
    s.flush()
    f.path = _write(f"leadfiles/{lead.id}", name, data, f.id)
    if kind == "retouched":
        f.original_id = match_original(s, lead.id, name)
    return f


def _stem(name: str) -> str:
    """'IMG_4102-retouche (1).JPG' → 'img4102'"""
    st = Path(name).stem.lower()
    st = re.sub(r"\(\d+\)|\b(copy|copie|final|edit(ed)?|retouch\w*|veralume|hdr|v\d+)\b", "", st)
    return re.sub(r"[^a-z0-9]", "", st)


def match_original(s: Session, lead_id: int, name: str) -> int | None:
    """The received photo a retouched file redoes, by file name."""
    st = _stem(name)
    if not st:
        return None
    rows = s.scalars(
        select(LeadFile).where(LeadFile.lead_id == lead_id, LeadFile.kind == "received")
        .order_by(LeadFile.id)
    )  # fmt: skip
    for f in rows:
        o = _stem(f.filename)
        if o and (o == st or st.startswith(o) or o.startswith(st)):
            return f.id
    return None


def thumb(rel: str, size: int = 480) -> Path:
    """A small JPEG of an image, cached next to it. Raises FileError if it can't be read."""
    src = abs_path(rel)
    out = src.parent / ".thumbs" / f"{src.stem}-{size}.jpg"
    if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
        return out
    try:
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((size, size))
            out.parent.mkdir(exist_ok=True)
            im.save(out, quality=82)
    except Exception as e:  # RAW files, HEIC without a plugin, broken files
        raise FileError("No preview for this file type") from e
    return out


def zip_files(rows: list[LeadFile]) -> bytes:
    buf = io.BytesIO()
    seen: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for f in rows:
            name = f.filename
            while name in seen:
                name = f"{f.id}-{name}"
            seen.add(name)
            z.write(abs_path(f.path), name)
    return buf.getvalue()


# --- email: attachments in and out ---------------------------------------------------


def save_inbound(s: Session, lead: Lead, msg: Message, em: EmailMessage) -> int:
    """Keep the photos attached to a lead's email, and the transfer links in it."""
    n = 0
    for part in em.iter_attachments():
        name = part.get_filename() or ""
        ctype = part.get_content_type()
        if not is_image(name, ctype) or ctype == "image/gif":
            continue  # signatures' logos are often GIFs; PDFs and docs aren't photos
        data = part.get_payload(decode=True) or b""
        if len(data) < 30_000 and part.get("Content-ID"):
            continue  # a small inline image: a logo in their signature
        if not name:
            name = f"photo-{n + 1}{'.png' if ctype == 'image/png' else '.jpg'}"
        try:
            add_file(s, lead, name, data, "received", msg.id)
            n += 1
        except FileError:
            continue
    text = ""
    for part in em.walk():
        if part.get_content_type() in ("text/plain", "text/html") and not part.get_filename():
            try:
                text += part.get_content()
            except (LookupError, ValueError):
                pass
    msg.links = sorted({m.rstrip(".,;") for m in _LINK.findall(text)})[:10]
    if n or msg.links:
        what = f"{n} photo{'s' if n != 1 else ''}" if n else "a transfer link"
        s.add(Activity(lead_id=lead.id, kind="photos_sent", text=f"They sent {what}"))
        if lead.pilot_stage in (None, "interested"):
            set_stage(s, lead, "photos" if n else "interested")
    return n


def attachments(s: Session, msg: Message) -> list[tuple[str, bytes]]:
    """(filename, bytes) for the library images and lead files of an outbound email."""
    out: list[tuple[str, bytes]] = []
    for i, aid in enumerate(msg.asset_ids or [], 1):
        a = s.get(Asset, aid)
        if a:
            p = abs_path(a.path)
            if p.exists():
                out.append((f"avant-apres-{i}{p.suffix.lower()}", p.read_bytes()))
    for fid in msg.file_ids or []:
        f = s.get(LeadFile, fid)
        if f and f.lead_id == msg.lead_id:
            p = abs_path(f.path)
            if p.exists():
                out.append((f.filename, p.read_bytes()))
    if sum(len(b) for _, b in out) > MAX_EMAIL:
        raise FileError(
            "These attachments are over 20 MB together: Gmail would refuse the email. "
            "Send fewer photos per email, or smaller exports."
        )
    return out


def after_send(s: Session, lead: Lead, msg: Message) -> None:
    """Retouched photos went out: mark them, and move the pilot to "sent back"."""
    sent = [f for fid in msg.file_ids or [] if (f := s.get(LeadFile, fid)) and f.kind == "retouched"]
    for f in sent:
        f.sent_at = msg.sent_at
    if sent and lead.pilot_stage in (None, "interested", "photos", "retouching"):
        set_stage(s, lead, "sent_back")


# --- the pilot's steps ---------------------------------------------------------------


def set_stage(s: Session, lead: Lead, stage: str | None, due_at: str | None = None) -> None:
    if stage is not None and stage not in STAGES:
        raise FileError("Unknown pilot step")
    if stage != lead.pilot_stage:
        old = STAGE_LABELS.get(lead.pilot_stage or "", "no pilot")
        s.add(Activity(lead_id=lead.id, kind="status",
                       text=f"pilot: {old} → {STAGE_LABELS.get(stage or '', 'no pilot')}"))  # fmt: skip
        lead.pilot_stage = stage
    if due_at is not None:
        lead.pilot_due_at = due_at or None
    # Keep the sales status in step: a running pilot is "pilot", the end is the decision.
    target = {"customer": "customer", "lost": "lost"}.get(stage or "", "pilot" if stage else None)
    order = ("new", "shortlisted", "contacted", "replied", "interview", "pilot", "customer")
    if target and lead.status != target and (
        target in ("customer", "lost") or (lead.status in order and order.index(lead.status) < 5)
    ):
        s.add(Activity(lead_id=lead.id, kind="status", text=f"{lead.status} → {target}"))
        lead.status = target


def on_reply(s: Session, lead: Lead) -> None:
    """They answered after we sent their photos back: that's their feedback."""
    if lead.pilot_stage == "sent_back":
        set_stage(s, lead, "feedback")


def counts(s: Session, lead_ids: list[int]) -> dict[int, dict[str, int]]:
    out = {i: {"received": 0, "retouched": 0, "sent": 0} for i in lead_ids}
    if not lead_ids:
        return out
    q = (
        select(LeadFile.lead_id, LeadFile.kind, func.count(LeadFile.id), func.count(LeadFile.sent_at))
        .where(LeadFile.lead_id.in_(lead_ids)).group_by(LeadFile.lead_id, LeadFile.kind)
    )  # fmt: skip
    for lid, kind, n, sent in s.execute(q):
        out[lid][kind] = n
        if kind == "retouched":
            out[lid]["sent"] = sent
    return out


def file_dict(f: LeadFile) -> dict[str, Any]:
    return {
        "id": f.id, "kind": f.kind, "filename": f.filename, "size": f.size,
        "message_id": f.message_id, "original_id": f.original_id, "sent_at": f.sent_at,
        "created_at": f.created_at, "src": f"/api/files/{f.id}", "thumb": f"/api/files/{f.id}/thumb",
    }  # fmt: skip


def asset_dict(a: Asset) -> dict[str, Any]:
    return {"id": a.id, "name": a.name, "size": a.size, "archived": a.archived,
            "src": f"/api/assets/{a.id}/file", "thumb": f"/api/assets/{a.id}/thumb"}  # fmt: skip


def next_step(lead: Lead, c: dict[str, int]) -> str | None:
    """What to do now for this pilot, in a few words."""
    left = c["received"] - c["retouched"]
    return {
        "interested": "Wait for their photos, or nudge them",
        "photos": f"Download and retouch their {c['received']} photos",
        "retouching": (f"Retouch the last {left} photo{'s' if left != 1 else ''}" if left > 0
                       else "Send their retouched photos back"),
        "sent_back": "Ask what they think",
        "feedback": "Decide: customer or not",
    }.get(lead.pilot_stage or "")  # fmt: skip
