"""The prospecting app: leads, the lead file, and the outreach routes (api/outreach.py)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func, or_, select

from prospecting import enrich, mailer, pilot, scoring, screen, worker
from prospecting.api.campaigns import router as campaigns_router
from prospecting.api.experiments import router as experiments_router
from prospecting.api.labels import router as labels_router
from prospecting.api.outreach import router as outreach_router
from prospecting.api.pilot import router as pilot_router
from prospecting.api.writer import router as writer_router
from prospecting.config import settings
from prospecting.contacts import (
    email_problem,
    flags,
    phone_problem,
    shared_contacts,
    usable_emails,
    usable_phones,
)  # fmt: skip
from prospecting.countries import COUNTRIES
from prospecting.db import ACTIVITY_KINDS, KINDS, STATUSES, Activity, Lead, Message, Photo, session
from prospecting.languages import LANGUAGES
from prospecting.outreach import SMALL_NEW, summary
from prospecting.scoring import HEADCOUNT_LABELS
from prospecting.text import NOT_OWN_SITE, domain_in

STATIC = Path(__file__).with_name("static")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    if settings.run_worker:
        worker.start()
    yield
    worker.stop()


app = FastAPI(title="Veralume Prospecting", lifespan=lifespan)
settings.photos_dir.mkdir(parents=True, exist_ok=True)
app.mount("/photos", StaticFiles(directory=settings.photos_dir), name="photos")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.include_router(outreach_router)
app.include_router(experiments_router)
app.include_router(writer_router)
app.include_router(pilot_router)
app.include_router(campaigns_router)
app.include_router(labels_router)


def _get(s, lead_id: int) -> Lead:
    lead = s.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(404, "No lead with this id")
    return lead


def detail(s, lead: Lead) -> dict[str, Any]:
    se, sp = shared_contacts()
    msgs = s.scalars(select(Message).where(Message.lead_id == lead.id).order_by(Message.id.desc()))
    return {
        **summary(lead, (se, sp)),
        "legal_name": lead.legal_name, "siren": lead.siren, "siret": lead.siret,
        "address": lead.address, "postcode": lead.postcode,
        "headcount": HEADCOUNT_LABELS.get(lead.headcount or "", None),
        "company_category": lead.company_category, "branches": lead.branches,
        "founded": lead.founded, "website_source": lead.website_source,
        "emails": [{**e, "problem": email_problem(e, lead, se)} for e in lead.emails or []],
        "phones": [{**p, "problem": phone_problem(p, sp)} for p in lead.phones or []],
        "best_email": next(iter(usable_emails(lead, se)), None),
        "best_phone": next(iter(usable_phones(lead, sp)), None),
        "people": lead.people or [], "socials": lead.socials or {}, "tags": lead.tags or [],
        "sources": lead.sources or [], "listings_seen": lead.listings_seen,
        "rating": lead.rating, "rating_count": lead.rating_count,
        "enrich_note": lead.enrich_note, "screen_status": lead.screen_status,
        "lean_max": lead.lean_max, "score_reasons": lead.score_reasons or [],
        "notes": lead.notes,
        "photos": [
            {"id": p.id, "src": "/" + p.path, "url": p.url, "page_url": p.page_url,
             "lean_deg": p.lean_deg, "n_lines": p.n_lines, "error": p.error}
            for p in sorted(lead.photos, key=lambda p: -(p.lean_deg if p.lean_deg is not None else -1))
            if p.path
        ],
        "activities": [{"id": a.id, "at": a.at, "kind": a.kind, "text": a.text}
                       for a in lead.activities],
        "messages": [
            {"id": m.id, "direction": m.direction, "status": m.status, "subject": m.subject,
             "body": m.body, "to": m.to_addr, "from": m.from_addr, "step": m.step,
             "due_at": m.due_at, "sent_at": m.sent_at, "error": m.error,
             "attach_photo_id": m.attach_photo_id, "asset_ids": m.asset_ids or [],
             "file_ids": m.file_ids or [], "links": m.links or []}
            for m in msgs
        ],
    }  # fmt: skip


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    se, sp = shared_contacts()
    by_kind: dict[str, dict[str, int]] = {k: {"total": 0, "email": 0, "phone": 0} for k in KINDS}
    deps: set[str] = set()
    with session() as s:
        rows = s.execute(
            select(Lead.kind, Lead.emails, Lead.phones, Lead.do_not_contact, Lead.postcode,
                   Lead.country, Lead.language)
        )
        by_language: dict[str, dict[str, int]] = {}
        by_country: dict[str, dict[str, int]] = {}
        for kind, emails, phones, dnc, pc, country, lang in rows:
            bl = by_language.setdefault(lang or "fr", {})
            bl[kind] = bl.get(kind, 0) + 1
            k = by_kind.setdefault(kind, {"total": 0, "email": 0, "phone": 0})
            k["total"] += 1
            country = country or "FR"
            bc = by_country.setdefault(country, {})
            bc[kind] = bc.get(kind, 0) + 1
            if pc and country == "FR":
                deps.add(pc[:2])
            if dnc:
                continue
            k["email"] += any(e["value"].lower() not in se for e in emails or [])
            k["phone"] += any(p["value"] not in sp for p in phones or [])
        by_status = dict(
            s.execute(select(Lead.status, func.count(Lead.id)).group_by(Lead.status)).all()
        )
    return {
        "kinds": KINDS, "statuses": STATUSES, "activity_kinds": ACTIVITY_KINDS,
        "by_kind": by_kind, "by_status": {st: by_status.get(st, 0) for st in (*STATUSES, "lost")},
        "total": sum(v["total"] for v in by_kind.values()), "departements": sorted(deps),
        "center": settings.center_name, "by_country": by_country,
        "by_language": by_language, "languages": LANGUAGES,
        "countries": {c.code: c.name for c in COUNTRIES.values()},
    }  # fmt: skip


SORTS = {
    "score": (Lead.score.desc(), Lead.id),
    "lean": (Lead.lean_median.desc().nulls_last(), Lead.score.desc()),
    "distance": (Lead.distance_km.asc().nulls_last(), Lead.score.desc()),
    "name": (Lead.name.asc(),),
    "updated": (Lead.updated_at.desc(),),
    "contacted": (Lead.last_contacted_at.desc().nulls_last(), Lead.score.desc()),
}


@app.get("/api/leads")
def leads(
    q: str = "", kind: str = "", status: str = "", city: str = "", dep: str = "",
    reach: str = "", contacted: str = "", measured: bool = False, min_lean: float | None = None,
    needs_check: bool = False, small_first: bool = False, hide_dnc: bool = True, label: int | None = None,
    country: str = "", language: str = "",
    sort: str = "score", offset: int = 0, limit: int = 100,
) -> dict[str, Any]:  # fmt: skip
    shared = shared_contacts()
    se, sp = shared
    with session() as s:
        stmt = select(Lead)
        if q:
            like = f"%{q}%"
            stmt = stmt.where(
                or_(Lead.name.ilike(like), Lead.legal_name.ilike(like), Lead.city.ilike(like),
                    Lead.website.ilike(like), Lead.network.ilike(like), Lead.notes.ilike(like),
                    Lead.postcode.startswith(q))
            )  # fmt: skip
        if kind:
            stmt = stmt.where(Lead.kind == kind)
        if status:
            stmt = stmt.where(Lead.status == status)
        if city:
            stmt = stmt.where(Lead.city == city)
        if country:
            stmt = stmt.where(func.coalesce(Lead.country, "FR") == country)
        if language:
            stmt = stmt.where(func.coalesce(Lead.language, "fr") == language)
        if dep:
            stmt = stmt.where(func.coalesce(Lead.country, "FR") == "FR", Lead.postcode.startswith(dep))
        if measured:
            stmt = stmt.where(Lead.lean_median.is_not(None))
        if min_lean is not None:
            stmt = stmt.where(Lead.lean_median >= min_lean)
        if contacted == "yes":
            stmt = stmt.where(Lead.last_contacted_at.is_not(None))
        elif contacted == "no":
            stmt = stmt.where(Lead.last_contacted_at.is_(None), Lead.status == "new")
        if hide_dnc:
            stmt = stmt.where(Lead.do_not_contact.is_(False))
        rows = list(s.scalars(stmt.order_by(*SORTS.get(sort, SORTS["score"]))))
        if reach == "email":
            rows = [l for l in rows if usable_emails(l, se)]
        elif reach == "phone":
            rows = [l for l in rows if usable_phones(l, sp)]
        elif reach == "any":
            rows = [l for l in rows if usable_emails(l, se) or usable_phones(l, sp)]
        elif reach == "none":
            rows = [l for l in rows if not usable_emails(l, se) and not usable_phones(l, sp)]
        if needs_check:
            rows = [l for l in rows if flags(l, se, sp)]
        if label is not None:
            rows = [l for l in rows if label in (l.labels or [])]
        if small_first:  # stable sort: keeps the chosen order inside each group
            rows.sort(key=lambda l: not (SMALL_NEW & set(l.tags or [])))
        return {
            "total": len(rows),
            "items": [summary(l, shared) for l in rows[offset : offset + limit]],
        }


@app.get("/api/leads/{lead_id}")
def lead(lead_id: int) -> dict[str, Any]:
    with session() as s:
        return detail(s, _get(s, lead_id))


class LeadPatch(BaseModel):
    status: str | None = None
    notes: str | None = None
    do_not_contact: bool | None = None
    kind: str | None = None
    name: str | None = None
    next_action: str | None = None
    next_action_at: str | None = None
    snoozed_until: str | None = None
    clear_next_action: bool = False
    language: str | None = None


def _apply(s, l: Lead, patch: LeadPatch) -> None:
    if patch.status and patch.status != l.status:
        if patch.status not in (*STATUSES, "lost"):
            raise HTTPException(422, "Unknown status")
        s.add(Activity(lead_id=l.id, kind="status", text=f"{l.status} → {patch.status}"))
        l.status = patch.status
        if patch.status == "pilot" and not l.pilot_stage:  # it shows on the pilots board
            pilot.set_stage(s, l, "interested")
        elif patch.status in ("new", "shortlisted", "contacted", "replied", "interview") and l.pilot_stage:
            pilot.set_stage(s, l, None)  # moved back before the pilot: it leaves the pilots board
        if patch.status in ("lost", "customer", "replied", "interview", "pilot"):
            mailer.cancel_pending(s, l.id, f"moved to {patch.status}")
    if patch.language is not None:
        if patch.language not in LANGUAGES:
            raise HTTPException(422, "Unknown language")
        l.language, l.language_source = patch.language, "you"
    if patch.kind and patch.kind in KINDS:
        l.kind = patch.kind
    if patch.name:
        l.name = patch.name.strip()
    if patch.notes is not None:
        l.notes = patch.notes
    if patch.next_action is not None:
        l.next_action = patch.next_action.strip() or None
        l.next_action_at = (patch.next_action_at or mailer.iso(mailer.local())) if l.next_action else None
    if patch.clear_next_action:
        l.next_action = l.next_action_at = None
    if patch.snoozed_until is not None:
        l.snoozed_until = patch.snoozed_until or None
    if patch.do_not_contact is not None and patch.do_not_contact != l.do_not_contact:
        l.do_not_contact = patch.do_not_contact
        if l.do_not_contact:
            mailer.cancel_pending(s, l.id, "do not contact")
            l.next_action = l.next_action_at = None
        s.add(Activity(lead_id=l.id, kind="status",
                       text="marked do not contact" if l.do_not_contact else "contact allowed again"))  # fmt: skip
    l.score, l.score_reasons = scoring.score(l, shared_contacts())


@app.patch("/api/leads/{lead_id}")
def update(lead_id: int, patch: LeadPatch) -> dict[str, Any]:
    with session() as s:
        l = _get(s, lead_id)
        _apply(s, l, patch)
        s.commit()
        s.refresh(l)
        return detail(s, l)


class BulkIn(BaseModel):
    ids: list[int]
    action: str  # status | dnc | call | contacted | snooze
    value: str | None = None


@app.post("/api/leads/bulk")
def bulk(b: BulkIn) -> dict[str, Any]:
    with session() as s:
        n = 0
        for l in s.scalars(select(Lead).where(Lead.id.in_(b.ids))):
            if b.action == "status" and b.value:
                _apply(s, l, LeadPatch(status=b.value))
            elif b.action == "dnc":
                _apply(s, l, LeadPatch(do_not_contact=True))
            elif b.action == "call":
                l.next_action, l.next_action_at = "call", mailer.iso(mailer.local())
            elif b.action == "contacted":
                if l.status in ("new", "shortlisted"):
                    _apply(s, l, LeadPatch(status="contacted"))
                l.last_contacted_at = l.last_contacted_at or mailer.iso(mailer.local())
            elif b.action == "snooze":
                l.snoozed_until = b.value
            else:
                raise HTTPException(422, "Unknown action")
            n += 1
        s.commit()
    return {"updated": n}


class ContactIn(BaseModel):
    field: str  # emails | phones | people
    value: str
    role: str | None = None


@app.post("/api/leads/{lead_id}/contacts")
def add_contact(lead_id: int, c: ContactIn) -> dict[str, Any]:
    with session() as s:
        l = _get(s, lead_id)
        value = c.value.strip()
        if not value:
            raise HTTPException(422, "Type a value first")
        if c.field == "people":
            l.add_person(value, c.role, "added by hand")
        elif c.field == "emails":
            l.add_contact("emails", value.lower(), "added by hand")
        elif c.field == "phones":
            l.add_contact("phones", value, "added by hand")
        else:
            raise HTTPException(422, "field must be emails, phones or people")
        l.score, l.score_reasons = scoring.score(l, shared_contacts())
        s.commit()
        s.refresh(l)
        return detail(s, l)


class ActivityIn(BaseModel):
    kind: str
    text: str = ""


@app.post("/api/leads/{lead_id}/activities")
def add_activity(lead_id: int, a: ActivityIn) -> dict[str, Any]:
    if a.kind not in ACTIVITY_KINDS:
        raise HTTPException(422, "Unknown activity kind")
    with session() as s:
        l = _get(s, lead_id)
        s.add(Activity(lead_id=l.id, kind=a.kind, text=a.text.strip()))
        if a.kind in ("email", "call", "visit"):
            l.last_contacted_at = mailer.iso(mailer.local())
            if l.status in ("new", "shortlisted"):
                s.add(Activity(lead_id=l.id, kind="status", text=f"{l.status} → contacted"))
                l.status = "contacted"
        if a.kind == "interview" and l.status in ("new", "shortlisted", "contacted", "replied"):
            l.status = "interview"
        s.commit()
        s.refresh(l)
        return detail(s, l)


@app.delete("/api/activities/{activity_id}")
def delete_activity(activity_id: int) -> dict[str, str]:
    with session() as s:
        a = s.get(Activity, activity_id)
        if a is not None:
            s.delete(a)
            s.commit()
    return {"ok": "deleted"}


class WebsiteIn(BaseModel):
    website: str


@app.post("/api/leads/{lead_id}/website")
def set_website(lead_id: int, w: WebsiteIn) -> dict[str, Any]:
    """Set the website by hand, then read it and measure its photos (takes ~30 s)."""
    url = w.website.strip()
    if not url.startswith("http"):
        url = "https://" + url
    if domain_in(url, NOT_OWN_SITE):
        raise HTTPException(422, "That's a portal or directory, not the lead's own site")
    with session() as s:
        l = _get(s, lead_id)
        l.website, l.website_source = url, "added by hand"
        l.add_source("website", url)
        s.commit()
    return refresh(lead_id)


@app.post("/api/leads/{lead_id}/refresh")
def refresh(lead_id: int) -> dict[str, Any]:
    """Re-read the website (and re-measure the photos, if measuring is on) for one lead."""
    enrich.run(lead_ids=[lead_id], retry=True, progress=lambda _: None)
    if settings.measure_photos:
        screen.run(lead_ids=[lead_id], retry=True, progress=lambda _: None)
    scoring.rescore([lead_id])
    return lead(lead_id)


class LeadIn(BaseModel):
    name: str
    kind: str = "agency"
    city: str | None = None
    website: str | None = None
    email: str | None = None
    phone: str | None = None


@app.post("/api/leads")
def create(l: LeadIn) -> dict[str, Any]:
    if l.kind not in KINDS:
        raise HTTPException(422, "Unknown kind")
    with session() as s:
        lead_ = Lead(kind=l.kind, name=l.name.strip(), city=l.city, website=l.website or None)
        lead_.website_source = "added by hand" if l.website else None
        lead_.add_source("manual")
        if l.email:
            lead_.add_contact("emails", l.email.strip().lower(), "added by hand")
        if l.phone:
            lead_.add_contact("phones", l.phone.strip(), "added by hand")
        s.add(lead_)
        s.commit()
        new_id = lead_.id
    if l.website:
        return refresh(new_id)
    scoring.rescore([new_id])
    return lead(new_id)


@app.get("/api/photos/{photo_id}/marked.jpg")
def marked(photo_id: int) -> Response:
    """The photo as attached to emails: the true vertical and their lean drawn on it."""
    with session() as s:
        p = s.get(Photo, photo_id)
        if p is None or not p.path:
            raise HTTPException(404, "No such photo")
        return Response(mailer.marked_photo(p), media_type="image/jpeg",
                        headers={"Cache-Control": "max-age=3600"})  # fmt: skip
