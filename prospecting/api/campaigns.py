"""Routes for campaigns (who, which email versions, how it goes, the results) and
dossiers (reusable sets of images)."""

from __future__ import annotations

import csv
import io
from collections import Counter
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import func, select

from prospecting import campaigns as C
from prospecting import mailer, pilot
from prospecting.contacts import usable_emails
from prospecting.api.outreach import _msg as msg_dict
from prospecting.api.outreach import mailbox_status
from prospecting.config import settings
from prospecting.db import (
    KINDS, STATUSES, Activity, Asset, Campaign, CampaignLead, Dossier, Experiment, Lead, Message,
    Template, now, session,
)  # fmt: skip
from prospecting.languages import LANGUAGES, of_lead
from prospecting.mailer import MailError
from prospecting.translations import followup_translations
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
        "followup_days": C.days_of(c, 1), "followup": _followup(C.followup_of(s, c, 1)),
        "followup_default": _default(1),
        "followup2_on": c.followup2_on is not False, "followup2_days": C.days_of(c, 2),
        "followup2": _followup(C.followup_of(s, c, 2)), "followup2_default": _default(2),
        "close_on": c.close_on is not False, "close_days": C.close_days_of(c),
        "started_at": c.started_at, "ended_at": c.ended_at, "created_at": c.created_at,
        "versions": [_version(t, i) for i, t in enumerate(C.versions(s, c))],
        "send_days": list(mailer.window(c)[0]), "send_hours": list(mailer.window(c)[1]),
        "window_default": {"days": list(settings.send_weekdays), "hours": list(settings.send_hours)},
        "notes": c.notes or "", "bounce_pause": c.bounce_pause, "paused_reason": c.paused_reason,
        "send_mode": mailer.send_mode(s), "sender": settings.sender,
    }  # fmt: skip


def _default(step: int) -> dict[str, Any]:
    """The ready-made text of follow-up 1 or 2, in every language."""
    return {"language": "fr", "body": C.FOLLOWUP_BODY if step == 1 else C.FOLLOWUP2_BODY,
            "translations": {k: v["body"] for k, v in followup_translations(step).items()}}  # fmt: skip


def _followup(t: Template | None) -> dict[str, Any] | None:
    """The follow-up text: {language, body, translations: {lang: body}} (its subject is
    always "Re: " + the first email's)."""
    if t is None:
        return None
    return {"language": t.language or "fr", "body": t.body,
            "translations": {k: x.get("body", "") for k, x in (t.translations or {}).items()}}  # fmt: skip


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
        order = {"running": 0, "paused": 1, "draft": 2, "done": 3, "archived": 4}
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


class FollowupIn(BaseModel):
    language: str = "fr"  # of body
    body: str = ""
    translations: dict[str, str] = {}  # lang → text


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
    followup_days: int | None = None
    followup: FollowupIn | None = None  # can change on a started campaign
    followup2_on: bool | None = None
    followup2_days: int | None = None
    followup2: FollowupIn | None = None
    close_on: bool | None = None  # mark "No reply" those who never answered…
    close_days: int | None = None  # …this many days after the last email
    versions: list[VersionIn] | None = None
    send_days: list[int] | None = None  # weekdays, Monday = 0
    send_hours: list[int] | None = None  # [start, end)
    notes: str | None = None
    bounce_pause: int | None = None  # % of first emails bounced that pauses it; 0: never


def _apply_followups(s, c: Campaign, b: CampaignIn) -> None:
    """The follow-up automations: on/off, delay, text. They can change any time; follow-ups
    written but not sent yet are then written again as they now are (or not, if off)."""
    for step in C.STEPS:
        on, days, text = (b.followups_on, b.followup_days, b.followup) if step == 1 \
            else (b.followup2_on, b.followup2_days, b.followup2)
        changed = False
        if on is not None:
            changed |= on != C.is_on(c, step) if step == 1 else on != (c.followup2_on is not False)
            if step == 1:
                c.followups_on = on
            else:
                c.followup2_on = on
        if days is not None:
            if not 1 <= days <= 30:
                raise HTTPException(422, f"Follow-up {step} goes out 1 to 30 days after the email before it.")
            changed |= days != C.days_of(c, step)
            if step == 1:
                c.followup_days = days
            else:
                c.followup2_days = days
        if text is not None:
            if text.language not in LANGUAGES:
                raise HTTPException(422, "Unknown language")
            if not text.body.strip():
                raise HTTPException(422, f"Write the text of follow-up {step} first.")
            old = C.followup_of(s, c, step)
            new_tr = {k: {"subject": "Re:", "body": v} for k, v in text.translations.items()}
            changed |= old is None or old.body != text.body or (old.language or "fr") != text.language \
                or {k: x.get("body") for k, x in (old.translations or {}).items()} != \
                {k: v for k, v in text.translations.items() if v.strip()}
            C.set_followup(s, c, text.language, text.body, new_tr, step)
        if changed:
            C.unwrite_waiting(s, c, step)
            if step == 1 and not C.is_on(c, 1):  # follow-up 2 hangs on follow-up 1
                C.unwrite_waiting(s, c, 2)


def _only_translations(s, c: Campaign, vs: list[VersionIn]) -> bool:
    """True when these versions are the campaign's own, unchanged but for their
    translations (which a started campaign may still change: leads who speak a newly
    written language can then be added)."""
    have = {t.id: t for t in C.versions(s, c)}
    if {v.id for v in vs} != set(have):
        return False
    return all(
        (t := have[v.id]).subject == v.subject and t.body == v.body
        and (t.language or "fr") == v.language and list(t.asset_ids or []) == list(v.asset_ids)
        and bool(t.attach_photo) == v.attach_photo
        for v in vs
    )


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
    _apply_followups(s, c, b)
    if b.notes is not None:
        c.notes = b.notes[:5000]
    if b.send_days is not None:
        days = sorted({d for d in b.send_days if 0 <= d <= 6})
        if not days:
            raise HTTPException(422, "Pick at least one day.")
        c.send_days = days
    if b.send_hours is not None:
        if len(b.send_hours) != 2 or not 0 <= b.send_hours[0] < b.send_hours[1] <= 24:
            raise HTTPException(422, "The hours go from a start to a later end, 0 to 24.")
        c.send_hours = [int(b.send_hours[0]), int(b.send_hours[1])]
    if b.bounce_pause is not None:
        if not 0 <= b.bounce_pause <= 50:
            raise HTTPException(422, "The bounce limit is 1 to 50 %, or 0 for never.")
        c.bounce_pause = b.bounce_pause or None
    if b.close_on is not None:
        c.close_on = b.close_on
    if b.close_days is not None:
        if not 1 <= b.close_days <= 90:
            raise HTTPException(422, "\"No reply\" comes 1 to 90 days after the last email.")
        c.close_days = b.close_days
    if b.start_on is not None:
        try:
            c.start_on = date.fromisoformat(b.start_on).isoformat() if b.start_on else None
        except ValueError as e:
            raise HTTPException(422, "The start date isn't a date") from e
    if b.versions is not None and not draft and _only_translations(s, c, b.versions):
        for v in b.versions:
            t = s.get(Template, v.id)
            t.translations = C.clean_translations(v.translations, t.language or "fr")
        b.versions = None
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
        if b.followup is None:  # the follow-ups ready to edit, in every language
            C.set_followup(s, c, "fr", C.FOLLOWUP_BODY, followup_translations(1), 1)
        if b.followup2 is None:
            C.set_followup(s, c, "fr", C.FOLLOWUP2_BODY, followup_translations(2), 2)
        c.followup_days = c.followup_days or C.FOLLOWUP_DAYS
        c.followup2_days = c.followup2_days or C.FOLLOWUP2_DAYS
        c.followup2_on = c.followup2_on is not False
        c.close_on, c.close_days = c.close_on is not False, c.close_days or C.CLOSE_DAYS
        s.commit()
        return _config(s, c)


@router.get("/campaigns/{campaign_id}")
def get_one(campaign_id: int) -> dict[str, Any]:
    with session() as s:
        c = _get(s, campaign_id)
        out = _config(s, c)
        if c.status != "draft":
            out["results"] = C.results(s, c)
            out["followup_status"] = C.followup_status(s, c, 1)
            out["followup2_status"] = C.followup_status(s, c, 2)
            out["close_status"] = C.close_status(s, c)
            out["forecast"] = C.forecast(s, c)
            out["others"] = [{"id": x.id, "name": x.name, "status": x.status} for x in s.scalars(
                select(Campaign).where(Campaign.id != c.id, Campaign.status.in_(("draft", "running", "paused")))
                .order_by(Campaign.id.desc()))]  # fmt: skip
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
        if c.status != "draft":  # its unsent emails go; sent ones stay in each lead's history
            rows = list(s.scalars(select(CampaignLead).where(CampaignLead.campaign_id == c.id)))
            C._drop_unsent(s, c, [r.lead_id for r in rows], (0, *C.STEPS))
            for r in rows:
                s.delete(r)
        for t in [*C.versions(s, c), *(C.followup_of(s, c, k) for k in C.STEPS)]:
            if t is not None:
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
                     review=c.review, metric=c.metric, followups_on=c.followups_on, status="draft",
                     followup_days=C.days_of(c, 1), followup2_days=C.days_of(c, 2),
                     followup2_on=c.followup2_on is not False, close_on=c.close_on is not False,
                     close_days=C.close_days_of(c))  # fmt: skip
        s.add(d)
        s.flush()
        for t in C.versions(s, c):
            C.new_version(s, d, t.name, t.subject, t.body, list(t.asset_ids or []), t.attach_photo,
                          t.language or "fr", dict(t.translations or {}))  # fmt: skip
        for step in C.STEPS:
            fu = C.followup_of(s, c, step)
            if fu is not None:
                C.set_followup(s, d, fu.language or "fr", fu.body, dict(fu.translations or {}), step)
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
            c.paused_reason = None
            c.bounce_ack = C._bounces(s, c)[0]  # only a new bounce pauses it again
        elif b.status == "archived":
            c.status = "archived"  # nothing more goes out; hidden from the list
            for m in s.scalars(select(Message).where(Message.campaign_id == c.id, Message.step == 0,
                                                     Message.status == "approved")):  # fmt: skip
                m.status = "queued"
        elif b.status == "unarchive":
            c.status = "paused"
        else:
            raise HTTPException(422, "status is paused, running, archived or unarchive")
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


class BulkIn(BaseModel):
    action: str  # followup_now | skip | resume | remove | move | status
    lead_ids: list[int]
    target: int | None = None  # move: the campaign
    status: str | None = None  # status: the lead status


@router.post("/campaigns/{campaign_id}/leads/bulk")
def bulk(campaign_id: int, b: BulkIn) -> dict[str, Any]:
    """Act on some leads of a started campaign at once."""
    ids = list(dict.fromkeys(b.lead_ids))[:1000]
    with session() as s:
        c = _get(s, campaign_id)
        done, notes = 0, []
        try:
            if b.action == "followup_now":
                for lid in ids:
                    try:
                        C.followup_now(s, c, lid)
                        done += 1
                    except C.CampaignError as e:
                        lead = s.get(Lead, lid)
                        notes.append(f"{lead.name if lead else lid}: {e}")
            elif b.action in ("skip", "resume"):
                done = C.set_skip(s, c, ids, b.action == "skip")
            elif b.action == "remove":
                done = C.remove_leads(s, c, ids)
            elif b.action == "move":
                target = s.get(Campaign, b.target or 0)
                if target is None:
                    raise HTTPException(404, "No such campaign")
                done, notes = C.move_leads(s, c, target, ids)
            elif b.action == "status":
                if b.status not in STATUSES:
                    raise HTTPException(422, "Unknown status")
                for lead in s.scalars(select(Lead).where(Lead.id.in_(ids))):
                    if lead.status != b.status:
                        s.add(Activity(lead_id=lead.id, kind="status", text=f"{lead.status} → {b.status}"))
                        lead.status = b.status
                        done += 1
            else:
                raise HTTPException(422, "Unknown action")
        except C.CampaignError as e:
            raise HTTPException(409, str(e)) from e
        s.commit()
        return {"done": done, "notes": notes[:20]}


@router.get("/campaigns/{campaign_id}/export.csv")
def export(campaign_id: int) -> Response:
    """The campaign's leads and where each one is, for a spreadsheet."""
    with session() as s:
        c = _get(s, campaign_id)
        r = C.results(s, c)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["lead_id", "name", "person", "town", "country", "language", "email", "version",
                "state", "last_email", "next_step", "next_at", "follow_ups_skipped", "reply"])  # fmt: skip
    for x in r["leads"]:
        nx = x.get("next") or {}
        w.writerow([x["id"], x["name"], x["person"] or "", x["city"] or "", x["country"] or "",
                    x["language"] or "", x["email"] or "", x["letter"], x["state"], x["at"] or "",
                    f"follow-up {nx['step']}" if nx else "", nx.get("at", ""), "yes" if x["skip"] else "",
                    (x["reply"] or "").replace("\n", " ")])  # fmt: skip
    name = "".join(ch if ch.isalnum() else "-" for ch in c.name).strip("-")[:60] or f"campaign-{c.id}"
    return Response(buf.getvalue().encode("utf-8-sig"), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})  # fmt: skip


class PreviewIn(BaseModel):
    step: int = 0  # 0: the first email (version template_id); 1, 2: a follow-up
    template_id: int | None = None
    language: str | None = None
    body: str | None = None  # a follow-up's unsaved text
    lead_id: int | None = None
    test: bool = False  # also send it to your own address


def _pick_lead(s, c: Campaign, lang: str | None, lead_id: int | None) -> Lead | None:
    if lead_id:
        return s.get(Lead, lead_id)
    rows = list(s.scalars(select(CampaignLead).where(CampaignLead.campaign_id == c.id)
                          .order_by(CampaignLead.rank).limit(400)))  # fmt: skip
    leads = [l for l in (s.get(Lead, r.lead_id) for r in rows) if l is not None]
    if c.status == "draft" and not leads:
        leads = C.audience(s, c)[0][:400]
    return next((l for l in leads if lang and of_lead(l) == lang), None) or (leads[0] if leads else None)


@router.post("/campaigns/{campaign_id}/preview")
def preview(campaign_id: int, b: PreviewIn) -> dict[str, Any]:
    """One of the campaign's emails, filled in for a real lead of it, as they'd get it."""
    with session() as s:
        c = _get(s, campaign_id)
        vs = C.versions(s, c)
        first = next((t for t in vs if t.id == b.template_id), vs[0] if vs else None)
        if first is None:
            raise HTTPException(409, "Write the first email first.")
        lang = b.language or first.language or "fr"
        lead = _pick_lead(s, c, lang, b.lead_id)
        if lead is None:
            raise HTTPException(409, "No lead in this campaign yet to fill the email in for.")
        d0 = mailer.draft(s, lead, first, lang)
        if b.step in C.STEPS:
            saved = C.followup_of(s, c, b.step)
            if b.body is not None:
                tr = {lang: {"subject": "Re:", "body": b.body}} if lang != "fr" else {}
                t = Template(key="preview", name="Follow-up", segment=c.kind, step=b.step, subject="Re:",
                             body=b.body if lang == "fr" else (saved.body if saved else b.body),
                             language="fr", translations=tr, attach_photo=False, asset_ids=[])  # fmt: skip
            elif saved is not None:
                t = saved
            else:
                raise HTTPException(409, f"Follow-up {b.step} isn't written yet.")
            d = mailer.draft(s, lead, t, lang)
            subject, body, assets = f"Re: {d0['subject']}", d["body"], []
        else:
            subject, body, assets = d0["subject"], d0["body"], d0["asset_ids"]
        to = usable_emails(lead)
        out = {"subject": subject, "body": body, "language": d0["language"], "asset_ids": assets,
               "lead": {"id": lead.id, "name": lead.name, "person": C._person(lead)},
               "to": to[0] if to else None}  # fmt: skip
        if b.test:
            if not settings.mail_configured or not settings.sender:
                raise HTTPException(409, "Connect your mailbox first (Settings).")
            msg = Message(lead_id=lead.id, direction="out", status="test", to_addr=settings.sender,
                          language=d0["language"], subject=f"[Test · {lead.name}] {subject}", body=body,
                          step=b.step, asset_ids=assets)  # fmt: skip
            try:
                mailer.deliver(mailer.build(msg, lead, s))
            except MailError as e:
                raise HTTPException(409, str(e)) from e
            out["sent_to"] = settings.sender
        s.rollback()  # nothing about a preview is kept
        return out


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
