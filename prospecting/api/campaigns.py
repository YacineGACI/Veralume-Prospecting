"""Routes for campaigns (who, which email versions, how it goes, the results) and
dossiers (reusable sets of images)."""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from prospecting import campaigns as C
from prospecting import mailer, pilot
from prospecting.api.outreach import _msg as msg_dict
from prospecting.api.outreach import mailbox_status
from prospecting.config import settings
from prospecting.db import (
    KINDS, Asset, Campaign, CampaignLead, Dossier, Experiment, Lead, Message, Template, now,
    session,
)  # fmt: skip
from prospecting.languages import LANGUAGES, of_lead
from prospecting.mailer import MailError
from prospecting.outreach import summary

router = APIRouter(prefix="/api")


def _get(s, campaign_id: int) -> Campaign:
    c = s.get(Campaign, campaign_id)
    if c is None:
        raise HTTPException(404, "No such campaign")
    return c


def _version(t: Template, i: int) -> dict[str, Any]:
    return {"id": t.id, "letter": C._letter(i), "name": t.name, "subject": t.subject,
            "body": t.body, "asset_ids": t.asset_ids or [], "attach_photo": t.attach_photo,
            "language": t.language or "fr", "translations": t.translations or {}}  # fmt: skip


def _config(s, c: Campaign) -> dict[str, Any]:
    return {
        "id": c.id, "name": c.name, "kind": c.kind, "status": c.status,
        "filters": c.filters or {}, "size": c.size or 0, "order": c.lead_order or "small",
        "excluded": c.excluded or [], "included": c.included or [], "daily_cap": c.daily_cap or 20,
        "review": c.review or "daily", "metric": c.metric or "reply",
        "start_on": c.start_on, "followups_on": c.followups_on is not False,
        "started_at": c.started_at, "ended_at": c.ended_at, "created_at": c.created_at,
        "versions": [_version(t, i) for i, t in enumerate(C.versions(s, c))],
    }  # fmt: skip


def _card(s, c: Campaign) -> dict[str, Any]:
    out = _config(s, c)
    if c.status == "draft":
        leads, matching = C.audience(s, c)
        out["preview"] = {"chosen": len(leads), "matching": matching}
    else:
        r = C.results(s, c, with_leads=False)
        out["results"] = {"totals": r["totals"], "test": r["test"] and {
            "verdict": r["test"]["verdict"], "leader": r["test"]["leader"],
            "suggest": r["test"].get("suggest"), "status": r["test"]["status"]},
            "days_left": r["days_left"]}  # fmt: skip
    return out


@router.get("/campaigns")
def list_campaigns() -> dict[str, Any]:
    with session() as s:
        rows = list(s.scalars(select(Campaign).order_by(Campaign.id.desc())))
        order = {"running": 0, "paused": 1, "draft": 2, "done": 3}
        rows.sort(key=lambda c: order.get(c.status, 4))
        return {"items": [_card(s, c) for c in rows], "mailbox": mailbox_status(s)}


# --- who: the live count while you click filters -------------------------------------


class AudienceIn(BaseModel):
    kind: str = "photographer"
    filters: dict[str, Any] = {}
    order: str = "small"
    size: int = 50
    excluded: list[int] = []
    included: list[int] = []
    campaign_id: int | None = None
    needs_lean: bool = False
    languages: list[str] | None = None  # those every version is written in (None: all)
    shown: int = 25  # how many of the chosen leads to list


def _row(l: Lead) -> dict[str, Any]:
    """A lead in the campaign list: the summary plus what it does."""
    tags = set(l.tags or [])
    return {**summary(l), "specialties": [C.SPECIALTIES[t] for t in C.SPECIALTIES if t in tags][:3],
            "socials": [k for k in C.SOCIALS if (l.socials or {}).get(k)], "founded": l.founded}  # fmt: skip


@router.post("/campaigns/audience")
def audience(b: AudienceIn) -> dict[str, Any]:
    if b.kind not in KINDS:
        raise HTTPException(422, "Unknown segment")
    with session() as s:
        everyone = C.candidates(s, b.kind, b.filters, b.order, seed=b.campaign_id or 0,
                                campaign_id=b.campaign_id, needs_lean=b.needs_lean)  # fmt: skip
        langs = set(b.languages) if b.languages is not None else None
        lang_of = {l.id: of_lead(l) for l in everyone}
        every = [l for l in everyone if langs is None or lang_of[l.id] in langs]
        hand, blocked = C.hand_picked(s, b.included, b.campaign_id, langs)
        chosen = C.pick(every, hand, b.size, b.excluded)
        gone = set(b.excluded)
        n_dep = Counter((l.postcode or "")[:2] for l in every if l.postcode and (l.country or "FR") == "FR")
        deps = [{"dep": d, "n": n} for d, n in sorted(n_dep.items(), key=lambda x: (-x[1], x[0]))]
        hand_ids = {l.id for l in hand}
        return {
            "matching": len(every), "chosen": len(chosen), "from_filters": len(chosen) - len(hand),
            "preview": [{**_row(l), "by_hand": l.id in hand_ids} for l in chosen[: max(1, b.shown)]],
            "excluded": [summary(l) for l in every if l.id in gone][:50],
            "blocked": [{**summary(l), "why": why} for l, why in blocked],
            "deps": deps, "facets": C.facets(s, every),
            # Every lead the filters let in, by language, and those whose language no
            # text covers yet (left out until it's written).
            "by_language": dict(Counter(lang_of.values()).most_common()),
            "left_out": dict(Counter(lang_of[l.id] for l in everyone if l.id not in {x.id for x in every}).most_common()),
        }  # fmt: skip


@router.get("/campaigns/lookup")
def lookup(q: str = "", campaign_id: int | None = None) -> dict[str, Any]:
    """Find any lead by name, person, town or website to add it by hand, with whether it
    can be contacted."""
    q = q.strip()
    if len(q) < 2:
        return {"items": []}
    like = f"%{q}%"
    with session() as s:
        rows = list(s.scalars(
            select(Lead).where(Lead.name.ilike(like) | Lead.city.ilike(like) | Lead.website.ilike(like)
                               | Lead.legal_name.ilike(like) | Lead.postcode.startswith(q))
            .order_by(Lead.score.desc()).limit(20)
        ))  # fmt: skip
        return {"items": [{**_row(l), "why": C.why_not(s, l, campaign_id)} for l in rows]}


class AddLeadsIn(BaseModel):
    lead_ids: list[int]


@router.post("/campaigns/{campaign_id}/leads")
def add_leads_by_hand(campaign_id: int, b: AddLeadsIn) -> dict[str, Any]:
    """Add chosen leads: kept in a draft's list, or given a version at once once started."""
    with session() as s:
        c = _get(s, campaign_id)
        n, notes = C.add_specific(s, c, b.lead_ids)
        s.commit()
        return {"added": n, "notes": notes, "included": c.included or []}


@router.get("/leads/{lead_id}/campaigns")
def campaigns_for_lead(lead_id: int) -> dict[str, Any]:
    """The campaigns this lead could be added to, and where it already is."""
    with session() as s:
        lead = s.get(Lead, lead_id)
        if lead is None:
            raise HTTPException(404, "No such lead")
        inside = set(s.scalars(select(CampaignLead.campaign_id).where(CampaignLead.lead_id == lead_id)))
        rows = s.scalars(select(Campaign).where(Campaign.status != "done").order_by(Campaign.id.desc()))
        return {
            "why": C.why_not(s, lead),
            "items": [{"id": c.id, "name": c.name, "status": c.status, "kind": c.kind,
                       "inside": c.id in inside or lead_id in (c.included or [])} for c in rows],
        }  # fmt: skip


# --- create and edit -----------------------------------------------------------------


class VersionIn(BaseModel):
    id: int | None = None
    name: str = "Version"
    subject: str = ""
    body: str = ""
    asset_ids: list[int] = []
    attach_photo: bool = False
    language: str = "fr"  # of subject and body
    translations: dict[str, dict[str, str]] = {}


class CampaignIn(BaseModel):
    name: str | None = None
    kind: str | None = None
    filters: dict[str, Any] | None = None
    size: int | None = None
    order: str | None = None
    excluded: list[int] | None = None
    included: list[int] | None = None
    daily_cap: int | None = None
    review: str | None = None
    metric: str | None = None
    start_on: str | None = None
    followups_on: bool | None = None
    versions: list[VersionIn] | None = None


def _apply(s, c: Campaign, b: CampaignIn) -> None:
    draft = c.status == "draft"
    if b.name is not None:
        c.name = b.name.strip()[:160] or "Untitled campaign"
    if b.daily_cap is not None:
        c.daily_cap = max(1, min(int(b.daily_cap), settings.daily_send_cap or 60))
    if b.review is not None:
        if b.review not in ("daily", "auto"):
            raise HTTPException(422, "review is daily or auto")
        c.review = b.review
    if b.followups_on is not None:
        c.followups_on = b.followups_on
    if b.start_on is not None:
        try:
            c.start_on = date.fromisoformat(b.start_on).isoformat() if b.start_on else None
        except ValueError as e:
            raise HTTPException(422, "The start date isn't a date") from e
    locked = [k for k in ("kind", "filters", "order", "excluded", "included", "metric", "versions")
              if getattr(b, k) is not None]  # fmt: skip
    if locked and not draft:
        raise HTTPException(409, "This campaign has started: who it reaches and its emails "
                                 "can't change any more. Duplicate it to try something else.")  # fmt: skip
    if b.kind is not None:
        if b.kind not in KINDS:
            raise HTTPException(422, "Unknown segment")
        c.kind = b.kind
    if b.filters is not None:
        c.filters = b.filters
    if b.size is not None:
        if not draft and b.size < (c.size or 0):
            raise HTTPException(409, "A started campaign can grow, not shrink. Pause it instead.")
        c.size = max(1, min(int(b.size), 5000))
    if b.order is not None:
        if b.order not in C.ORDERS:
            raise HTTPException(422, "Unknown order")
        c.lead_order = b.order
    if b.excluded is not None:
        c.excluded = sorted(set(b.excluded))
    if b.included is not None:
        c.included = list(dict.fromkeys(b.included))
    if b.metric is not None:
        if b.metric not in ("reply", "interested"):
            raise HTTPException(422, "metric is reply or interested")
        c.metric = b.metric
    if b.versions is not None:
        if not 1 <= len(b.versions) <= C.MAX_VERSIONS:
            raise HTTPException(422, f"A campaign has 1 to {C.MAX_VERSIONS} email versions.")
        have = {t.id: t for t in C.versions(s, c)}
        keep = set()
        for v in b.versions:
            t = have.get(v.id) if v.id else None
            if v.language not in LANGUAGES:
                raise HTTPException(422, "Unknown language")
            if t is None:
                t = C.new_version(s, c, v.name, v.subject, v.body, v.asset_ids, v.attach_photo,
                                  v.language, v.translations)  # fmt: skip
            else:
                t.name, t.subject, t.body = v.name.strip()[:120] or "Version", v.subject, v.body
                t.asset_ids, t.attach_photo, t.segment = list(v.asset_ids), v.attach_photo, c.kind
                t.language = v.language
                t.translations = C.clean_translations(v.translations, v.language)
            keep.add(t.id)
        for tid, t in have.items():
            if tid not in keep:
                t.archived = True


@router.post("/campaigns")
def create(b: CampaignIn) -> dict[str, Any]:
    with session() as s:
        c = Campaign(name="New campaign", kind=b.kind or "photographer", filters={}, size=50,
                     lead_order="small", excluded=[], daily_cap=min(20, settings.daily_send_cap),
                     review="daily", metric="reply", followups_on=True, status="draft")  # fmt: skip
        s.add(c)
        s.flush()
        _apply(s, c, b)
        if b.versions is None:  # start from the segment's usual first email
            t = s.scalars(
                select(Template).where(Template.step == 0, Template.archived.is_(False),
                                       Template.campaign_id.is_(None),
                                       Template.segment.in_((c.kind, "any")),
                                       Template.key != "after_voicemail")
                .order_by(Template.id)
            ).first()  # fmt: skip
            C.new_version(s, c, "A", t.subject if t else "", t.body if t else "Bonjour {prenom},\n\n\n\n{signature}",
                          list(t.asset_ids or []) if t else [], bool(t and t.attach_photo))  # fmt: skip
        s.commit()
        return _config(s, c)


@router.get("/campaigns/{campaign_id}")
def get_one(campaign_id: int) -> dict[str, Any]:
    with session() as s:
        c = _get(s, campaign_id)
        out = _config(s, c)
        if c.status != "draft":
            out["results"] = C.results(s, c)
        out["mailbox"] = mailbox_status(s)
        return out


@router.put("/campaigns/{campaign_id}")
def save(campaign_id: int, b: CampaignIn) -> dict[str, Any]:
    with session() as s:
        c = _get(s, campaign_id)
        _apply(s, c, b)
        s.commit()
        return _config(s, c)


@router.delete("/campaigns/{campaign_id}")
def delete(campaign_id: int) -> dict[str, Any]:
    with session() as s:
        c = _get(s, campaign_id)
        if c.status != "draft":
            raise HTTPException(409, "Only a draft can be deleted. Pause a started campaign instead.")
        for t in C.versions(s, c):
            t.archived = True
        s.delete(c)
        s.commit()
        return {"deleted": campaign_id}


@router.post("/campaigns/{campaign_id}/duplicate")
def duplicate(campaign_id: int) -> dict[str, Any]:
    with session() as s:
        c = _get(s, campaign_id)
        d = Campaign(name=f"{c.name} (copy)", kind=c.kind, filters=dict(c.filters or {}),
                     size=c.size, lead_order=c.lead_order, excluded=[], daily_cap=c.daily_cap,
                     review=c.review, metric=c.metric, followups_on=c.followups_on, status="draft")  # fmt: skip
        s.add(d)
        s.flush()
        for t in C.versions(s, c):
            C.new_version(s, d, t.name, t.subject, t.body, list(t.asset_ids or []), t.attach_photo,
                          t.language or "fr", dict(t.translations or {}))  # fmt: skip
        s.commit()
        return _config(s, d)


# --- run it ----------------------------------------------------------------------------


@router.post("/campaigns/{campaign_id}/start")
def start(campaign_id: int) -> dict[str, Any]:
    with session() as s:
        c = _get(s, campaign_id)
        try:
            n = C.start(s, c)
        except C.CampaignError as e:
            raise HTTPException(409, str(e)) from e
        written = C.prepare(s, c) if mailer.in_window() else 0
        if written:
            mailer.set_setting(s, f"prepared:{c.id}", mailer.local().date().isoformat())
        s.commit()
        return {"leads": n, "written": written, "campaign": _config(s, c)}


class StatusIn(BaseModel):
    status: str


@router.post("/campaigns/{campaign_id}/status")
def set_status(campaign_id: int, b: StatusIn) -> dict[str, Any]:
    with session() as s:
        c = _get(s, campaign_id)
        if c.status == "draft":
            raise HTTPException(409, "Start it first.")
        if b.status == "paused":
            c.status = "paused"
            # First emails waiting for their slot go back to review.
            for m in s.scalars(select(Message).where(Message.campaign_id == c.id, Message.step == 0,
                                                     Message.status == "approved")):  # fmt: skip
                m.status = "queued"
        elif b.status == "running":
            c.status = "running"
        else:
            raise HTTPException(422, "status is paused or running")
        s.commit()
        return _config(s, c)


class WinnerIn(BaseModel):
    template_id: int


@router.post("/campaigns/{campaign_id}/winner")
def winner(campaign_id: int, b: WinnerIn) -> dict[str, Any]:
    with session() as s:
        c = _get(s, campaign_id)
        try:
            moved = C.pick_winner(s, c, b.template_id)
        except C.CampaignError as e:
            raise HTTPException(422, str(e)) from e
        s.commit()
        return {"moved": moved}


class MoreIn(BaseModel):
    n: int


@router.post("/campaigns/{campaign_id}/more")
def more(campaign_id: int, b: MoreIn) -> dict[str, Any]:
    with session() as s:
        c = _get(s, campaign_id)
        try:
            added = C.add_leads(s, c, max(1, min(b.n, 1000)))
        except C.CampaignError as e:
            raise HTTPException(409, str(e)) from e
        s.commit()
        return {"added": added}


@router.post("/campaigns/{campaign_id}/write-now")
def write_now(campaign_id: int) -> dict[str, Any]:
    """Write today's batch now instead of waiting for the morning."""
    with session() as s:
        c = _get(s, campaign_id)
        n = C.prepare(s, c)
        s.commit()
        return {"written": n}


@router.post("/campaigns/{campaign_id}/leads/{lead_id}/email")
def email_for_lead(campaign_id: int, lead_id: int) -> dict[str, Any]:
    """The lead's email, written if it isn't yet, to read and send from the campaign page."""
    with session() as s:
        c = _get(s, campaign_id)
        try:
            m = C.email_for(s, c, lead_id)
        except C.CampaignError as e:
            raise HTTPException(409, str(e)) from e
        s.commit()
        return {"lead": summary(s.get(Lead, lead_id)), "message": msg_dict(m)}


class TestIn(BaseModel):
    versions: list[VersionIn] | None = None  # unsaved edits; default: the saved ones
    language: str | None = None  # the language to test (default: each version's main one)


@router.post("/campaigns/{campaign_id}/test")
def send_test(campaign_id: int, b: TestIn) -> dict[str, Any]:
    """Each version, filled in for a real lead of the campaign, to your own address."""
    if not settings.mail_configured or not settings.sender:
        raise HTTPException(409, "Connect your mailbox first (Settings).")
    with session() as s:
        c = _get(s, campaign_id)
        leads, _ = C.audience(s, c) if c.status == "draft" else (None, 0)
        lead = next((l for l in leads or [] if b.language and of_lead(l) == b.language), None)
        lead = lead or (leads or [None])[0]
        if lead is None:
            row = s.scalars(select(CampaignLead).where(CampaignLead.campaign_id == c.id)
                            .order_by(CampaignLead.rank)).first()  # fmt: skip
            lead = s.get(Lead, row.lead_id) if row else None
        if lead is None:
            raise HTTPException(409, "No lead matches yet, so there's no one to fill the email in for.")
        vs = b.versions or [VersionIn(**{k: v for k, v in _version(t, 0).items() if k in VersionIn.model_fields})
                            for t in C.versions(s, c)]  # fmt: skip
        sent = 0
        for i, v in enumerate(vs):
            t = Template(key="test", name=v.name, segment=c.kind, step=0, subject=v.subject, body=v.body,
                         attach_photo=v.attach_photo, asset_ids=list(v.asset_ids), language=v.language,
                         translations=C.clean_translations(v.translations, v.language))  # fmt: skip
            d = mailer.draft(s, lead, t, b.language or v.language)
            msg = Message(lead_id=lead.id, direction="out", status="test", to_addr=settings.sender,
                          language=d["language"],
                          subject=f"[Test {C._letter(i)} · {lead.name}] {d['subject']}", body=d["body"],
                          step=0, attach_photo_id=d["attach_photo_id"], asset_ids=d["asset_ids"])  # fmt: skip
            try:
                mailer.deliver(mailer.build(msg, lead, s))
            except MailError as e:
                raise HTTPException(409, str(e)) from e
            sent += 1
        s.rollback()  # nothing about the test is kept
        return {"sent": sent, "to": settings.sender, "lead": lead.name}


# --- dossiers ------------------------------------------------------------------------


def _dossier(d: Dossier, assets: list[Asset], stats: dict[int, dict[str, int]]) -> dict[str, Any]:
    return {
        "id": d.id, "name": d.name, "note": d.note, "archived": d.archived,
        "images": [{**pilot.asset_dict(a), **stats.get(a.id, {"sent": 0, "replied": 0})}
                   for a in assets],
    }  # fmt: skip


@router.get("/dossiers")
def dossiers() -> dict[str, Any]:
    with session() as s:
        pilot.ensure_dossiers(s)
        s.commit()
        stats = pilot.image_stats(s)
        ds = list(s.scalars(select(Dossier).where(Dossier.archived.is_(False)).order_by(Dossier.id)))
        assets = list(s.scalars(select(Asset).where(Asset.archived.is_(False)).order_by(Asset.id)))
        used: dict[int, set[str]] = {}
        for t in s.scalars(select(Template).where(Template.archived.is_(False))):
            for a in t.asset_ids or []:
                name = s.get(Campaign, t.campaign_id).name if t.campaign_id and s.get(Campaign, t.campaign_id) else t.name
                used.setdefault(a, set()).add(name)
        items = []
        for d in ds:
            mine = [a for a in assets if a.dossier_id == d.id]
            row = _dossier(d, mine, stats)
            row["used_by"] = sorted(set().union(*[used.get(a.id, set()) for a in mine])) if mine else []
            items.append(row)
        return {"items": items}


class DossierIn(BaseModel):
    name: str | None = None
    note: str | None = None
    archived: bool | None = None


@router.post("/dossiers")
def new_dossier(b: DossierIn) -> dict[str, Any]:
    with session() as s:
        d = Dossier(name=(b.name or "New dossier").strip()[:120], note=(b.note or "")[:300])
        s.add(d)
        s.commit()
        return _dossier(d, [], {})


@router.patch("/dossiers/{dossier_id}")
def edit_dossier(dossier_id: int, b: DossierIn) -> dict[str, Any]:
    with session() as s:
        d = s.get(Dossier, dossier_id)
        if d is None:
            raise HTTPException(404, "No such dossier")
        if b.name is not None and b.name.strip():
            d.name = b.name.strip()[:120]
        if b.note is not None:
            d.note = b.note[:300]
        if b.archived is not None:
            d.archived = b.archived
        s.commit()
        assets = list(s.scalars(select(Asset).where(Asset.dossier_id == d.id, Asset.archived.is_(False))))
        return _dossier(d, assets, pilot.image_stats(s))


class PairIn(BaseModel):
    before_id: int
    after_id: int
    name: str | None = None


@router.post("/dossiers/{dossier_id}/pair")
def make_pair(dossier_id: int, b: PairIn) -> dict[str, Any]:
    """A side-by-side before/after from two images of the library."""
    with session() as s:
        d = s.get(Dossier, dossier_id)
        before, after = s.get(Asset, b.before_id), s.get(Asset, b.after_id)
        if d is None or before is None or after is None:
            raise HTTPException(404, "Pick two images of this dossier")
        try:
            data = pilot.make_pair(pilot.abs_path(before.path), pilot.abs_path(after.path))
            name = (b.name or f"{before.name} avant-après").strip()
            a = pilot.add_asset(s, f"{name}.jpg", data)
        except (pilot.FileError, OSError) as e:
            raise HTTPException(422, f"Couldn't make it: {e}") from e
        a.dossier_id = d.id
        s.commit()
        return pilot.asset_dict(a)


@router.get("/campaigns-summary")
def campaigns_summary() -> dict[str, Any]:
    """For Today: running campaigns and the ones whose test has a clear winner."""
    with session() as s:
        out = []
        for c in s.scalars(select(Campaign).where(Campaign.status.in_(("running", "paused")))):
            r = C.results(s, c, with_leads=False)
            out.append({"id": c.id, "name": c.name, "status": c.status, "totals": r["totals"],
                        "suggest": (r["test"] or {}).get("suggest")})  # fmt: skip
        n = s.scalar(select(func.count(Experiment.id)).where(Experiment.campaign_id.is_not(None)))
        return {"items": out, "tests": n or 0, "now": now()}
