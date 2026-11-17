"""Campaigns: who to write to, which email versions they get, and how it goes.

A draft campaign is a set of filters, a size, an order and one to four email versions
(templates owned by the campaign). Starting it freezes the list of leads (campaign_leads,
in sending order) and hands out the versions in equal shares, at random. With two or more
versions, the campaign owns an A/B test (an Experiment) analysed like any other.

Each working morning the next batch is written: waiting in Today for your approval
(review "daily"), or approved at once (review "auto"). The follow-up, when on, goes out on
its own `followup_days` after a first email that got no reply, in the same language and
the same thread. It's worked out from dates each time the worker runs, so follow-ups
missed while the app was off go out once it's back (inside the sending hours).
"""

from __future__ import annotations

import random
import unicodedata
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from prospecting import experiments, languages, mailer, translations
from prospecting.config import settings
from prospecting.contacts import shared_contacts, usable_emails, usable_phones
from prospecting.db import (
    Activity, Campaign, CampaignLead, Experiment, Lead, Message, Template, now,
)  # fmt: skip
from prospecting.mailer import day_start, iso, local
from prospecting.text import email_is_personal

SMALL_NEW = {"new_business", "sole_trader", "recent_profile", "few_reviews", "social_only"}
ORDERS = {"small": "Small and new first", "score": "Best score first", "random": "At random"}
ACTIVE = ("queued", "approved", "sent", "failed")  # a first email that counts as written
MAX_VERSIONS = 4


class CampaignError(Exception):
    """A message for the user."""


# --- who ---------------------------------------------------------------------------


def versions(s: Session, c: Campaign) -> list[Template]:
    """The first-email versions (step 0); the follow-up is apart (followup_of)."""
    return list(s.scalars(
        select(Template).where(Template.campaign_id == c.id, Template.archived.is_(False),
                               Template.step == 0)
        .order_by(Template.id)
    ))  # fmt: skip


def covered(tpls: list[Template]) -> set[str]:
    """The languages every version is written in: only leads speaking one of them are
    written to (a lead must be able to get whichever version it's dealt)."""
    if not tpls:
        return {"fr"}
    out = set(mailer.languages_of(tpls[0]))
    for t in tpls[1:]:
        out &= set(mailer.languages_of(t))
    return out


def _busy_elsewhere(s: Session, campaign_id: int | None) -> set[int]:
    """Leads waiting in another running or paused campaign."""
    q = (
        select(CampaignLead.lead_id).join(Campaign, Campaign.id == CampaignLead.campaign_id)
        .where(Campaign.status.in_(("running", "paused")))
    )  # fmt: skip
    if campaign_id:
        q = q.where(CampaignLead.campaign_id != campaign_id)
    return set(s.scalars(q))


def _in_campaigns(s: Session, campaign_id: int | None) -> dict[int, set[int]]:
    """lead id → the other campaigns it's in, whatever their status: started ones (their
    list of leads) and drafts (the leads added to them by hand)."""
    out: dict[int, set[int]] = {}
    q = select(CampaignLead.lead_id, CampaignLead.campaign_id)
    if campaign_id:
        q = q.where(CampaignLead.campaign_id != campaign_id)
    for lid, cid in s.execute(q):
        out.setdefault(lid, set()).add(cid)
    for c in s.scalars(select(Campaign).where(Campaign.status == "draft")):
        if c.id != campaign_id:
            for lid in c.included or []:
                out.setdefault(lid, set()).add(c.id)
    return out


def _not_in(f: dict[str, Any]) -> set[int]:
    """The campaigns whose leads the filters leave out (`not_in`, by id)."""
    return {int(x) for x in f.get("not_in") or [] if str(x).isdigit()}


def _started(s: Session) -> set[int]:
    """Leads who already have a first email written or sent."""
    return set(s.scalars(
        select(Message.lead_id).where(Message.direction == "out", Message.step == 0,
                                      Message.status.in_(ACTIVE))
    ))  # fmt: skip


def why_not(s: Session, lead: Lead, campaign_id: int | None = None,
            langs: set[str] | None = None) -> str | None:
    """Why this lead can't be added to a campaign by hand, or None if it can."""
    if lead.do_not_contact:
        return "marked do-not-contact"
    if lead.last_contacted_at or lead.id in _started(s):
        return "already contacted"
    if lead.status not in ("new", "shortlisted"):
        return f"already {lead.status.replace('_', ' ')}"
    if lead.id in _busy_elsewhere(s, campaign_id):
        return "waiting in another campaign"
    se, _ = shared_contacts()
    if not usable_emails(lead, se):
        return "no email address of their own"
    if langs is not None and (lang := languages.of_lead(lead)) not in langs:
        return f"speaks {languages.LANGUAGES.get(lang, lang)}: the email isn't written in it yet"
    return None


def facets(s: Session, leads: list[Lead], campaign_id: int | None = None) -> dict[str, Any]:
    """How many of these leads have each trait: shown beside each filter, so you see what
    a filter would leave before clicking it."""
    se, sp = shared_contacts()
    inside = _in_campaigns(s, campaign_id)
    per = Counter(cid for l in leads for cid in inside.get(l.id, ()))
    camps = [{"id": c.id, "name": c.name, "status": c.status, "n": per.get(c.id, 0)}
             for c in s.scalars(select(Campaign).where(Campaign.id != (campaign_id or 0)).order_by(Campaign.id.desc()))]  # fmt: skip
    tags = Counter(t for l in leads for t in set(l.tags or []) if t in SPECIALTIES)
    soc = Counter(k for l in leads for k in SOCIALS if (l.socials or {}).get(k))
    nets = Counter(l.network for l in leads if l.network)
    young = {y: sum(1 for l in leads if (a := _years_since(l.founded)) is not None and a <= y) for y in (1, 2, 5)}
    return {
        "total": len(leads),
        "first_name": sum(1 for l in leads if mailer.first_name(l)),
        "email_personal": sum(1 for l in leads if any(email_is_personal(e) for e in usable_emails(l, se))),
        "phone": sum(1 for l in leads if usable_phones(l, sp)),
        "website_yes": sum(1 for l in leads if l.website), "website_no": sum(1 for l in leads if not l.website),
        "small": sum(1 for l in leads if SMALL_NEW & set(l.tags or [])),
        "solo": sum(1 for l in leads if "sole_trader" in (l.tags or []) or (l.headcount or "") in SOLO_HEADCOUNT),
        "measured": sum(1 for l in leads if (l.photos_measured or 0) > 0),
        "lean": {v: sum(1 for l in leads if (l.lean_median or 0) >= v) for v in (0.5, 1, 2, 3)},
        "score": {v: sum(1 for l in leads if (l.score or 0) >= v) for v in (40, 60, 80)},
        "shortlisted": sum(1 for l in leads if l.status == "shortlisted"),
        "independent": sum(1 for l in leads if not l.network), "network": sum(1 for l in leads if l.network),
        "founded": young,
        "tags": [{"tag": t, "label": SPECIALTIES[t], "n": n} for t, n in tags.most_common()],
        "socials": {k: soc.get(k, 0) for k in SOCIALS},
        "networks": [{"name": k, "n": n} for k, n in nets.most_common(20)],
        "labels": dict(Counter(x for l in leads for x in set(l.labels or []))),
        "countries": dict(Counter(l.country or "FR" for l in leads)),
        "languages": dict(Counter(l.language or "fr" for l in leads)),
        # Leads in no other campaign, and how many of these leads each other campaign has.
        "in_no_campaign": sum(1 for l in leads if l.id not in inside),
        "campaigns": camps,
    }  # fmt: skip


# What a lead does, from its tags, in the words people use. Filters and the facet counts.
SPECIALTIES = {
    "transaction": "Sales", "rental": "Rentals", "short_stay": "Holiday lets", "prestige": "Luxury",
    "new_build": "New builds", "commercial": "Commercial", "home_staging": "Home staging",
    "drone": "Drone", "virtual_tour": "Virtual tours", "pro_photos": "Pro photo service",
    "management": "Property management", "syndic": "Building management",
}  # fmt: skip
SOCIALS = ("instagram", "facebook", "linkedin")
SOLO_HEADCOUNT = {"NN", "00", "01"}  # INSEE: no employee, or 1-2


def _fold(text: str | None) -> str:
    """Lowercase without accents, for 'contains' searches."""
    t = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in t if not unicodedata.combining(ch)).lower()


def _years_since(day: str | None) -> float | None:
    try:
        return (date.today() - date.fromisoformat((day or "")[:10])).days / 365.25
    except ValueError:
        return None


def _matches(lead: Lead, f: dict[str, Any], se: set[str], sp: set[str]) -> bool:
    """The filters that need Python (JSON fields, text, derived facts). SQL did the rest."""
    tags = set(lead.tags or [])
    if f.get("first_name") and not mailer.first_name(lead):
        return False
    if f.get("email_personal") and not any(email_is_personal(e) for e in usable_emails(lead, se)):
        return False
    if f.get("phone") and not usable_phones(lead, sp):
        return False
    if f.get("small") and not (SMALL_NEW & tags):
        return False
    if f.get("solo") and not ("sole_trader" in tags or (lead.headcount or "") in SOLO_HEADCOUNT):
        return False
    if f.get("founded_years"):
        age = _years_since(lead.founded)
        if age is None or age > float(f["founded_years"]):
            return False
    for net in f.get("socials") or []:
        if not (lead.socials or {}).get(net):
            return False
    if f.get("tags_any") and not (set(f["tags_any"]) & tags):
        return False
    if f.get("tags_none") and set(f["tags_none"]) & tags:
        return False
    mine = set(lead.labels or [])
    if f.get("labels_any") and not (set(f["labels_any"]) & mine):
        return False
    if f.get("labels_none") and set(f["labels_none"]) & mine:
        return False
    if f.get("network") == "independent" and lead.network:
        return False
    if f.get("network") == "network" and not lead.network:
        return False
    if f.get("networks") and lead.network not in f["networks"]:
        return False
    if f.get("town") and _fold(f["town"]) not in _fold(lead.city):
        return False
    if f.get("q"):
        people = " ".join(p.get("name", "") for p in lead.people or [])
        hay = _fold(" ".join(filter(None, [lead.name, lead.legal_name, lead.city, lead.website, lead.notes, people])))
        if not all(w in hay for w in _fold(f["q"]).split()):
            return False
    return True


def candidates(
    s: Session, kind: str, filters: dict[str, Any], order: str = "small", seed: int = 0,
    campaign_id: int | None = None, needs_lean: bool = False, langs: set[str] | None = None,
) -> list[Lead]:  # fmt: skip
    """Every lead the filters let in, never contacted, with an address of their own, in
    sending order. With `langs`, only those whose language is among them."""
    f = filters or {}
    statuses = ("shortlisted",) if f.get("shortlisted") else ("new", "shortlisted")
    q = select(Lead).where(
        Lead.kind == kind, Lead.do_not_contact.is_(False),
        Lead.status.in_(statuses), Lead.last_contacted_at.is_(None),
    )  # fmt: skip
    if f.get("countries"):
        q = q.where(func.coalesce(Lead.country, "FR").in_([str(c) for c in f["countries"]]))
    if f.get("languages"):
        q = q.where(func.coalesce(Lead.language, "fr").in_([str(x) for x in f["languages"]]))
    if f.get("deps"):  # French départements: a German 67xxx postcode is not the Bas-Rhin
        q = q.where(func.coalesce(Lead.country, "FR") == "FR",
                    or_(*[Lead.postcode.startswith(str(d)) for d in f["deps"]]))
    if f.get("max_km"):
        q = q.where(Lead.distance_km <= float(f["max_km"]))
    lean_min = max(float(f.get("lean_min") or 0), 1.0 if needs_lean else 0)
    if lean_min:
        q = q.where(Lead.lean_median >= lean_min)
    if f.get("measured"):
        q = q.where(Lead.photos_measured > 0)
    if f.get("website") in (True, "yes"):
        q = q.where(Lead.website.is_not(None))
    elif f.get("website") == "no":
        q = q.where(Lead.website.is_(None))
    if f.get("score_min"):
        q = q.where(Lead.score >= int(f["score_min"]))
    started = _started(s)
    busy = _busy_elsewhere(s, campaign_id)
    inside = _in_campaigns(s, campaign_id) if f.get("not_in_any") or f.get("not_in") else {}
    avoid = _not_in(f)
    se, sp = shared_contacts()
    out = []
    for lead in s.scalars(q):
        if lead.id in started or lead.id in busy or not usable_emails(lead, se):
            continue
        if f.get("not_in_any") and lead.id in inside:
            continue
        if avoid & inside.get(lead.id, set()):
            continue
        if not _matches(lead, f, se, sp):
            continue
        if langs is not None and languages.of_lead(lead) not in langs:
            continue
        out.append(lead)
    if order == "random":
        out.sort(key=lambda l: l.id)
        random.Random(seed).shuffle(out)
    elif order == "score":
        out.sort(key=lambda l: (-(l.score or 0), l.id))
    else:
        out.sort(key=lambda l: (not (SMALL_NEW & set(l.tags or [])), -(l.score or 0), l.id))
    return out


def needs_lean(tpls: list[Template]) -> bool:
    """A version quoting their measured lean only fits leads with one."""
    return any("{lean}" in (t.subject + t.body) for t in tpls)


def hand_picked(s: Session, ids: list[int] | None, campaign_id: int | None = None,
                langs: set[str] | None = None) -> tuple[list[Lead], list[tuple[Lead, str]]]:
    """Leads added by hand: those that can be contacted, and the others with the reason."""
    ok, blocked = [], []
    for i in ids or []:
        lead = s.get(Lead, i)
        if lead is None:
            continue
        why = why_not(s, lead, campaign_id, langs)
        (blocked.append((lead, why)) if why else ok.append(lead))
    return ok, blocked


def pick(every: list[Lead], hand: list[Lead], size: int, excluded: list[int] | None) -> list[Lead]:
    """Who gets written to: the leads added by hand first, then the first `size` the
    filters let in (minus those unticked)."""
    gone = set(excluded or [])
    have = {l.id for l in hand}
    keep = [l for l in every if l.id not in gone and l.id not in have]
    return hand + keep[: max(0, size)]


def audience(s: Session, c: Campaign) -> tuple[list[Lead], int]:
    """The leads this campaign would contact now, and how many match in all."""
    tpls = versions(s, c)
    langs = covered(tpls)
    every = candidates(s, c.kind, c.filters or {}, c.lead_order or "small", seed=c.id or 0,
                       campaign_id=c.id, needs_lean=needs_lean(tpls), langs=langs)  # fmt: skip
    hand, _ = hand_picked(s, c.included, c.id, langs)
    return pick(every, hand, c.size or 0, c.excluded), len(every)


def add_specific(s: Session, c: Campaign, lead_ids: list[int]) -> tuple[int, list[str]]:
    """Add chosen leads to a campaign. A draft keeps them in its hand-picked list; a
    started campaign hands them a version at once. Returns how many were added and why
    the others weren't."""
    hand, blocked = hand_picked(s, lead_ids, c.id, covered(versions(s, c)))
    notes = [f"{l.name}: {why}" for l, why in blocked]
    if c.status == "draft":
        before = set(c.included or [])
        c.included = list(c.included or []) + [l.id for l in hand if l.id not in before]
        c.excluded = [x for x in c.excluded or [] if x not in {l.id for l in hand}]
        return len(set(c.included) - before), notes
    have = set(s.scalars(select(CampaignLead.lead_id).where(CampaignLead.campaign_id == c.id)))
    new = [l for l in hand if l.id not in have]
    notes += [f"{l.name}: already in this campaign" for l in hand if l.id in have]
    tpls = versions(s, c)
    exp = s.get(Experiment, c.experiment_id) if c.experiment_id else None
    if exp and exp.status == "stopped" and exp.winner_id:
        tpls = [t for t in tpls if t.id == exp.winner_id] or tpls
    top = s.scalar(select(func.max(CampaignLead.rank)).where(CampaignLead.campaign_id == c.id)) or 0
    picks = _deal(len(new), len(tpls), seed=c.id + top + 7)
    for i, (lead, v) in enumerate(zip(new, picks), start=1):
        s.add(CampaignLead(campaign_id=c.id, lead_id=lead.id, template_id=tpls[v].id, rank=top + i))
        lead.campaign_id = c.id
    c.size = (c.size or 0) + len(new)
    if new and c.status == "done":
        c.status = "running"
    return len(new), notes


# --- start, write, finish ----------------------------------------------------------


def _deal(n: int, k: int, seed: int) -> list[int]:
    """Version index for n leads in sending order: equal shares, at random, balanced
    within every block of k so an early stop is still fair."""
    rng = random.Random(seed)
    out: list[int] = []
    while len(out) < n:
        block = list(range(k))
        rng.shuffle(block)
        out += block
    return out[:n]


def start(s: Session, c: Campaign) -> int:
    if c.status != "draft":
        raise CampaignError("This campaign has already started.")
    tpls = versions(s, c)
    if not tpls:
        raise CampaignError("Write the email first (step 2).")
    for t in tpls:
        if not t.subject.strip() or not t.body.strip():
            raise CampaignError(f"Version “{t.name}” needs a subject and a text.")
    for step in STEPS:
        fu = followup_of(s, c, step)
        if is_on(c, step) and (fu is None or not fu.body.strip()):
            raise CampaignError(f"Set the automations first (step 3): write follow-up {step}, or turn it off.")
    leads, _ = audience(s, c)
    if not leads:
        raise CampaignError("No lead matches. Loosen the filters in step 1.")
    picks = _deal(len(leads), len(tpls), seed=c.id)
    for i, (lead, v) in enumerate(zip(leads, picks)):
        s.add(CampaignLead(campaign_id=c.id, lead_id=lead.id, template_id=tpls[v].id, rank=i))
        lead.campaign_id = c.id
    if len(tpls) >= 2:
        exp = Experiment(name=c.name, kind=c.kind, template_ids=[t.id for t in tpls],
                         metric=c.metric or "reply", status="running", campaign_id=c.id)  # fmt: skip
        s.add(exp)
        s.flush()
        c.experiment_id = exp.id
    c.status, c.started_at = "running", now()
    s.flush()
    return len(leads)


def add_leads(s: Session, c: Campaign, n: int) -> int:
    """Grow a started campaign by the next n matching leads."""
    if c.status not in ("running", "paused", "done"):
        raise CampaignError("Only a started campaign can grow.")
    tpls = versions(s, c)
    exp = s.get(Experiment, c.experiment_id) if c.experiment_id else None
    if exp and exp.status == "stopped" and exp.winner_id:
        tpls = [t for t in tpls if t.id == exp.winner_id] or tpls
    have = set(s.scalars(select(CampaignLead.lead_id).where(CampaignLead.campaign_id == c.id)))
    every = candidates(s, c.kind, c.filters or {}, c.lead_order or "small", seed=c.id,
                       campaign_id=c.id, needs_lean=needs_lean(tpls), langs=covered(versions(s, c)))  # fmt: skip
    new = [l for l in every if l.id not in have and l.id not in set(c.excluded or [])][:n]
    top = s.scalar(select(func.max(CampaignLead.rank)).where(CampaignLead.campaign_id == c.id)) or 0
    picks = _deal(len(new), len(tpls), seed=c.id + top)
    for i, (lead, v) in enumerate(zip(new, picks), start=1):
        s.add(CampaignLead(campaign_id=c.id, lead_id=lead.id, template_id=tpls[v].id, rank=top + i))
        lead.campaign_id = c.id
    c.size = (c.size or 0) + len(new)
    if c.status == "done":
        c.status = "running"
    return len(new)


def _written(s: Session, c: Campaign) -> dict[int, Message]:
    """lead id → its first email in this campaign (the live one)."""
    out: dict[int, Message] = {}
    for m in s.scalars(
        select(Message).where(Message.campaign_id == c.id, Message.step == 0,
                              Message.direction == "out", Message.status.in_(ACTIVE))
        .order_by(Message.id)
    ):  # fmt: skip
        out.setdefault(m.lead_id, m)
    return out


def _why_unwritable(s: Session, row: CampaignLead, lead: Lead, se: set[str]) -> str | None:
    t = s.get(Template, row.template_id) if row.template_id else None
    if t is None:
        return "its email version is gone"
    if lead.do_not_contact:
        return "they asked not to be contacted"
    if not usable_emails(lead, se):
        return "no email address of their own any more"
    if lead.last_contacted_at:
        return "you already wrote to them some other way"
    if mailer.text_in(t, languages.of_lead(lead)) is None:  # their language changed since
        return "the email isn't written in their language"
    return None


def _write(s: Session, c: Campaign, row: CampaignLead, lead: Lead) -> Message:
    """Write this lead's first email from its version, in its language (waiting to be sent)."""
    t = s.get(Template, row.template_id)
    d = mailer.draft(s, lead, t)
    exp = s.get(Experiment, c.experiment_id) if c.experiment_id else None
    return mailer.new_outbound(
        s, lead, subject=d["subject"], body=d["body"], template_id=t.id, campaign_id=c.id,
        attach_photo_id=d["attach_photo_id"], asset_ids=d["asset_ids"], language=d["language"],
        experiment_id=exp.id if exp and exp.status == "running" else None,
        status="approved" if c.review == "auto" and mailer.send_mode(s) == "scheduled" else "queued",
    )  # fmt: skip


def email_for(s: Session, c: Campaign, lead_id: int) -> Message:
    """This lead's first email, ready to read and send by hand: the one already written,
    or written now."""
    if c.status not in ("running", "paused", "done"):
        raise CampaignError("Start the campaign first: that fixes its list of leads.")
    row = s.scalar(select(CampaignLead).where(CampaignLead.campaign_id == c.id,
                                              CampaignLead.lead_id == lead_id))  # fmt: skip
    lead = s.get(Lead, lead_id)
    if row is None or lead is None:
        raise CampaignError("This lead isn't in this campaign.")
    m = _written(s, c).get(lead_id)
    if m is not None and m.status == "sent":
        raise CampaignError("Already sent to this lead.")
    if m is not None:
        if m.status == "failed":
            if m.error == "bounced":  # that address doesn't exist: another of theirs, or none
                other = next(iter(usable_emails(lead, shared_contacts()[0])), None)
                if other is None:
                    raise CampaignError(f"{lead.name}'s address bounced and they have no other. "
                                        "Find their own address on their site and add it in their file.")  # fmt: skip
                m.to_addr = other
            m.status, m.error = "queued", None  # try again
        return m
    why = _why_unwritable(s, row, lead, shared_contacts()[0])
    if why:
        raise CampaignError(f"Can't write to {lead.name}: {why}.")
    if c.status == "done":
        c.status = "running"
    return _write(s, c, row, lead)


def prepare(s: Session, c: Campaign, n: int | None = None) -> int:
    """Write the next first emails of a running campaign (today's share of the cap)."""
    if c.status != "running":
        return 0
    if c.start_on and c.start_on > local().date().isoformat():
        return 0
    if n is None:
        today = s.scalar(
            select(func.count(Message.id)).where(
                Message.campaign_id == c.id, Message.step == 0, Message.status.in_(ACTIVE),
                Message.created_at >= iso(day_start()),
            )
        ) or 0  # fmt: skip
        n = max(0, (c.daily_cap or 20) - today)
    written = _written(s, c)
    rows = list(s.scalars(
        select(CampaignLead).where(CampaignLead.campaign_id == c.id).order_by(CampaignLead.rank)
    ))  # fmt: skip
    se, _ = shared_contacts()
    made = 0
    for row in rows:
        if made >= n:
            break
        if row.lead_id in written:
            continue
        lead = s.get(Lead, row.lead_id)
        if lead is None or _why_unwritable(s, row, lead, se):
            continue
        _write(s, c, row, lead)
        made += 1
    if not made and all(r.lead_id in written for r in rows):
        waiting = any(m.status in ("queued", "approved") for m in written.values())
        if rows and not waiting:
            c.status, c.ended_at = "done", now()
    return made


def prepare_running(s: Session) -> int:
    """Once a working day, write each running campaign's batch."""
    today = local().date().isoformat()
    n = 0
    for c in s.scalars(select(Campaign).where(Campaign.status == "running")):
        if not mailer.in_window(c=c):
            continue
        key = f"prepared:{c.id}"
        if mailer.get_setting(s, key, None) != today:
            n += prepare(s, c)
            if not c.start_on or c.start_on <= today:
                mailer.set_setting(s, key, today)
    return n


def add_version(s: Session, c: Campaign, name: str, subject: str, body: str, asset_ids: list[int],
                attach_photo: bool, language: str = "fr",
                translations: dict[str, dict[str, str]] | None = None) -> tuple[Template, int]:  # fmt: skip
    """A new version in a started campaign: the leads not emailed yet are shared out again
    between all its versions (those speaking one of its languages). Their unsent first
    emails are dropped and written again. Emails already sent stay as they were. With one
    version so far, this starts the campaign's A/B test, counted from now so both versions
    are compared over the same days. Returns the version and how many leads it gets."""
    if c.status not in ("running", "paused"):
        raise CampaignError("Only a running or paused campaign takes a new version.")
    tpls = versions(s, c)
    if len(tpls) >= MAX_VERSIONS:
        raise CampaignError(f"A campaign has at most {MAX_VERSIONS} versions.")
    exp = s.get(Experiment, c.experiment_id) if c.experiment_id else None
    if exp and exp.status == "stopped":
        raise CampaignError("This campaign's test has ended. Duplicate the campaign to test again.")
    if not subject.strip() or not body.strip():
        raise CampaignError("The new version needs a subject and a text.")
    t = new_version(s, c, name, subject, body, asset_ids, attach_photo, language, translations)
    if exp is None:
        exp = Experiment(name=c.name, kind=c.kind, template_ids=[x.id for x in tpls] + [t.id],
                         metric=c.metric or "reply", status="running", campaign_id=c.id)  # fmt: skip
        s.add(exp)
        s.flush()
        c.experiment_id = exp.id
    else:
        exp.template_ids = [*exp.template_ids, t.id]
    every = [*tpls, t]
    written = _written(s, c)
    rows, unsent = [], []
    for row in s.scalars(select(CampaignLead).where(CampaignLead.campaign_id == c.id).order_by(CampaignLead.rank)):
        m = written.get(row.lead_id)
        if m is not None and m.status not in ("queued", "approved"):
            continue  # sent, or failed: theirs is decided
        lead = s.get(Lead, row.lead_id)
        if lead is None or mailer.text_in(t, languages.of_lead(lead)) is None:
            continue  # the new version isn't in their language: they keep theirs
        rows.append(row)
        if m is not None:
            unsent.append(m)
    for m in unsent:
        m.status, m.error = "cancelled", f"new version {t.name} added: dealt again"
    got = 0
    for row, v in zip(rows, _deal(len(rows), len(every), seed=c.id + t.id)):
        row.template_id = every[v].id
        got += every[v].id == t.id
    s.flush()
    return t, got


def pick_winner(s: Session, c: Campaign, template_id: int) -> int:
    """End the test: everyone not emailed yet gets the winner. Returns how many."""
    tpls = {t.id: t for t in versions(s, c)}
    if template_id not in tpls:
        raise CampaignError("That version isn't in this campaign.")
    exp = s.get(Experiment, c.experiment_id) if c.experiment_id else None
    if exp:
        exp.status, exp.winner_id, exp.ended_at = "stopped", template_id, now()
    written = _written(s, c)
    moved = 0
    for m in written.values():  # not sent yet: written again with the winner
        if m.status in ("queued", "approved") and m.template_id != template_id:
            m.status, m.error = "cancelled", f"test ended: {tpls[template_id].name} won"
    written = _written(s, c)
    for row in s.scalars(select(CampaignLead).where(CampaignLead.campaign_id == c.id)):
        if row.lead_id not in written and row.template_id != template_id:
            row.template_id = template_id
            moved += 1
    return moved


# --- how it's going ----------------------------------------------------------------


def _letter(i: int) -> str:
    return experiments.LETTERS[i] if i < len(experiments.LETTERS) else str(i + 1)


def results(s: Session, c: Campaign, with_leads: bool = True) -> dict[str, Any]:
    tpls = versions(s, c)
    se = shared_contacts()[0]
    letters = {t.id: _letter(i) for i, t in enumerate(tpls)}
    rows = list(s.scalars(
        select(CampaignLead).where(CampaignLead.campaign_id == c.id).order_by(CampaignLead.rank)
    ))  # fmt: skip
    written = _written(s, c)
    sent = {lid: m for lid, m in written.items() if m.status == "sent"}
    outcome = experiments._report_outcomes(s, sent)
    leads = {l.id: l for l in s.scalars(select(Lead).where(Lead.id.in_([r.lead_id for r in rows])))} if rows else {}
    replies: dict[int, Message] = {}
    if sent:
        for m in s.scalars(
            select(Message).where(Message.direction == "in", Message.lead_id.in_(list(sent)))
            .order_by(Message.id)
        ):  # fmt: skip
            if m.sent_at and sent[m.lead_id].sent_at and (
                local(m.sent_at) >= local(sent[m.lead_id].sent_at)
            ):
                replies[m.lead_id] = m
    followed: dict[int, tuple[int, str]] = {}  # lead → (last follow-up sent, when)
    for lid, step, at in s.execute(
        select(Message.lead_id, Message.step, Message.sent_at).where(
            Message.campaign_id == c.id, Message.step.in_(STEPS), Message.status == "sent")
        .order_by(Message.step)
    ):  # fmt: skip
        followed[lid] = (step, at)

    def state(row: CampaignLead) -> tuple[str, str | None]:
        m = written.get(row.lead_id)
        lead = leads.get(row.lead_id)
        if m is None:
            if lead and (lead.do_not_contact or lead.last_contacted_at):
                return "skipped", None
            return "not_sent", None
        if m.status in ("queued", "approved"):
            return ("to_review" if m.status == "queued" else "approved"), m.due_at
        if m.status == "failed":
            if m.error == "bounced":  # sendable again only to another address of theirs
                other = lead is not None and bool(usable_emails(lead, se))
                return ("bounced_retry" if other else "bounced"), m.sent_at or m.due_at
            return "failed", m.due_at
        o = outcome.get(row.lead_id, {})
        if o.get("stopped"):
            return "stopped", m.sent_at
        if o.get("interested"):
            return "interested", m.sent_at
        if o.get("reply"):
            return "replied", (replies.get(row.lead_id) or m).sent_at
        if lead and lead.status == "no_reply":
            return "no_reply", m.sent_at
        if row.lead_id in followed:
            step, at = followed[row.lead_id]
            return ("followed_up" if step == 1 else "followed_up_2"), at
        return "waiting", m.sent_at

    states = [(r, *state(r)) for r in rows]
    count = Counter(st for _, st, _ in states)
    replied = sum(count[k] for k in ("replied", "interested"))
    totals = {
        "leads": len(rows), "sent": len(sent), "to_review": count["to_review"],
        "approved": count["approved"], "not_sent": count["not_sent"],
        "replied": replied, "interested": count["interested"], "stopped": count["stopped"],
        "skipped": count["skipped"], "failed": count["failed"], "no_reply": count["no_reply"],
        "pilots": sum(1 for l in leads.values() if l.pilot_stage),
        "bounced": count["bounced"] + count["bounced_retry"],
    }  # fmt: skip

    # Versions: the campaign's A/B test, or the one email's own numbers.
    exp = s.get(Experiment, c.experiment_id) if c.experiment_id else None
    test = None
    if exp:
        r = experiments.results(s, exp)
        test = {
            "id": exp.id, "status": exp.status, "winner_id": exp.winner_id,
            "metric": exp.metric, "verdict": r["verdict"], "versions": r["versions"],
            "needed_each": r["needed_each"], "leader": r["leader"],
        }  # fmt: skip
        unsent = totals["not_sent"] + totals["to_review"] + totals["approved"]
        if exp.status == "running" and r["verdict"]["state"] == "winner" and unsent:
            top = max(r["versions"], key=lambda v: v["chance_best"])
            test["suggest"] = {"template_id": top["template_id"], "letter": top["letter"],
                               "name": top["name"], "unsent": unsent}  # fmt: skip
    per_version = []
    for t in tpls:
        mine = [lid for lid, m in sent.items() if m.template_id == t.id]
        o = [outcome.get(lid, {}) for lid in mine]
        per_version.append({
            "template_id": t.id, "letter": letters[t.id], "name": t.name, "subject": t.subject,
            "asset_ids": t.asset_ids or [], "sent": len(mine),
            "replied": sum(1 for x in o if x.get("reply")),
            "interested": sum(1 for x in o if x.get("interested")),
            "planned": sum(1 for r in rows if r.template_id == t.id),
        })  # fmt: skip

    # Per language: who is in the campaign, who got it, who answered.
    by_lang: dict[str, dict[str, int]] = defaultdict(lambda: {"leads": 0, "sent": 0, "replied": 0})
    for r in rows:
        lead = leads.get(r.lead_id)
        m = sent.get(r.lead_id)
        lang = (m.language if m and m.language else None) or (languages.of_lead(lead) if lead else "fr")
        by_lang[lang]["leads"] += 1
        if m:
            by_lang[lang]["sent"] += 1
            by_lang[lang]["replied"] += 1 if outcome.get(r.lead_id, {}).get("reply") else 0

    # Day by day, the last two weeks of this campaign.
    days: dict[str, dict[str, int]] = defaultdict(lambda: {"sent": 0, "replies": 0})
    for m in sent.values():
        days[(m.sent_at or "")[:10]]["sent"] += 1
    for m in replies.values():
        days[(m.sent_at or "")[:10]]["replies"] += 1
    first = date.fromisoformat((c.started_at or now())[:10])
    span = []
    d = max(first, local().date() - timedelta(days=13))
    while d <= local().date():
        span.append({"day": d.isoformat(), **days.get(d.isoformat(), {"sent": 0, "replies": 0})})
        d += timedelta(days=1)

    out: dict[str, Any] = {"totals": totals, "test": test, "versions": per_version, "days": span,
                           "languages": dict(sorted(by_lang.items(), key=lambda x: -x[1]["leads"]))}
    left = totals["not_sent"]
    cap = c.daily_cap or 20
    out["days_left"] = -(-left // cap) if left else 0
    if with_leads:
        # What comes next for each (the earliest follow-up still to go), and every email
        # of this campaign each lead got, for the lead list's timeline.
        nxt: dict[int, dict[str, Any]] = {}
        for step in STEPS:
            for base, what in _followup_rows(s, c, step):
                if what in ("later", "due", "waiting") and base.lead_id not in nxt:
                    nxt[base.lead_id] = {"step": step, "at": followup_due_at(c, base, step)}
        mails: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for m in s.scalars(select(Message).where(Message.campaign_id == c.id, Message.direction == "out")
                           .order_by(Message.id)):  # fmt: skip
            mails[m.lead_id].append({"step": m.step, "status": m.status, "error": m.error,
                                     "at": m.sent_at or m.due_at, "to": m.to_addr, "id": m.id})  # fmt: skip
        out["leads"] = [
            {"id": r.lead_id, "name": leads[r.lead_id].name if r.lead_id in leads else "?",
             "person": _person(leads.get(r.lead_id)), "city": leads[r.lead_id].city if r.lead_id in leads else None,
             "country": leads[r.lead_id].country if r.lead_id in leads else None,
             "language": (written[r.lead_id].language if r.lead_id in written and written[r.lead_id].language
                          else languages.of_lead(leads[r.lead_id]) if r.lead_id in leads else None),
             "email": written[r.lead_id].to_addr if r.lead_id in written else None,
             "letter": letters.get(r.template_id, "?"), "state": st, "at": at,
             "reply": (replies[r.lead_id].body[:160] if r.lead_id in replies else None),
             "reply_at": replies[r.lead_id].sent_at if r.lead_id in replies else None,
             "message_id": written[r.lead_id].id if r.lead_id in written else None,
             "next": nxt.get(r.lead_id), "skip": bool(r.no_followups), "emails": mails.get(r.lead_id, [])}
            for r, st, at in states
        ]  # fmt: skip
    return out


def _person(lead: Lead | None) -> str | None:
    if lead is None:
        return None
    from prospecting.outreach import person

    return person(lead)


def new_version(s: Session, c: Campaign, name: str, subject: str, body: str,
                asset_ids: list[int], attach_photo: bool, language: str = "fr",
                translations: dict[str, dict[str, str]] | None = None) -> Template:  # fmt: skip
    t = Template(
        key=f"camp{c.id}_{uuid.uuid4().hex[:8]}", name=name.strip()[:120] or "Version",
        segment=c.kind, step=0, subject=subject, body=body, attach_photo=attach_photo,
        asset_ids=list(asset_ids), campaign_id=c.id, language=language,
        translations=clean_translations(translations, language),
    )  # fmt: skip
    s.add(t)
    s.flush()
    return t


def clean_translations(tr: dict[str, Any] | None, main: str) -> dict[str, dict[str, str]]:
    """Known languages only, never the main one, subject and text as strings."""
    out = {}
    for lang, x in (tr or {}).items():
        if lang in languages.LANGUAGES and lang != main and isinstance(x, dict):
            out[lang] = {"subject": str(x.get("subject") or ""), "body": str(x.get("body") or "")}
    return out


# --- the follow-ups (automations) ------------------------------------------------------------
# Follow-up 1 goes `followup_days` after the first email; follow-up 2, the last one,
# `followup2_days` after follow-up 1. Each only to those who haven't replied, in the first
# email's language, in its thread, without images. Their texts: templates with step 1 and 2.

STEPS = (1, 2)
FOLLOWUP_DAYS = 7
FOLLOWUP2_DAYS = 10
FOLLOWUP_BODY = translations.FOLLOWUP["fr"]  # in every language: translations.FOLLOWUP
FOLLOWUP2_BODY = translations.FOLLOWUP2["fr"]
_RE = "Re:"  # a follow-up's subject is "Re: <the first email's>"; this marks a written language


def followup_of(s: Session, c: Campaign, step: int = 1) -> Template | None:
    return s.scalars(
        select(Template).where(Template.campaign_id == c.id, Template.step == step,
                               Template.archived.is_(False)).order_by(Template.id.desc())
    ).first()  # fmt: skip


def days_of(c: Campaign, step: int = 1) -> int:
    return (c.followup_days or FOLLOWUP_DAYS) if step == 1 else (c.followup2_days or FOLLOWUP2_DAYS)


def is_on(c: Campaign, step: int = 1) -> bool:
    """Follow-up 2 follows follow-up 1: off when follow-up 1 is."""
    one = c.followups_on is not False
    return one if step == 1 else one and c.followup2_on is not False


def set_followup(s: Session, c: Campaign, language: str, body: str,
                 trans: dict[str, Any] | None, step: int = 1) -> Template:  # fmt: skip
    """Save a follow-up's text (main language + translations; only the text, the subject
    is always the first email's with "Re:")."""
    tr = {k: {"subject": _RE, "body": x["body"]}
          for k, x in clean_translations(trans, language).items() if x["body"].strip()}  # fmt: skip
    t = followup_of(s, c, step)
    if t is None:
        t = Template(key=f"camp{c.id}_fu{step}_{uuid.uuid4().hex[:6]}",
                     name="Follow-up" if step == 1 else "Follow-up 2", segment=c.kind, step=step,
                     subject=_RE, body=body, attach_photo=False, asset_ids=[], campaign_id=c.id,
                     language=language, translations=tr)  # fmt: skip
        s.add(t)
        s.flush()
    else:
        t.body, t.language, t.translations, t.subject = body, language, tr, _RE
    return t


def unwrite_waiting(s: Session, c: Campaign, step: int) -> int:
    """Forget the follow-ups written but not sent yet (switched off, new text or delay):
    they're written again, as they now are, when due."""
    rows = list(s.scalars(
        select(Message).where(Message.campaign_id == c.id, Message.step == step,
                              Message.direction == "out", Message.status.in_(("queued", "approved")))
    ))  # fmt: skip
    for m in rows:
        s.delete(m)
    return len(rows)


def _followup_rows(s: Session, c: Campaign, step: int = 1) -> list[tuple[Message, str]]:
    """Each lead's sent email before this step (the first email, or follow-up 1), with what
    happens to this follow-up: "sent", "waiting" (written, goes out at the next free slot),
    "cancelled", "due" (to write now), "later" (not yet the day), "replied", "language" (not
    written in theirs) or "off"."""
    bases = {m.lead_id: m for m in s.scalars(
        select(Message).where(Message.campaign_id == c.id, Message.step == step - 1,
                              Message.direction == "out", Message.status == "sent")
        .order_by(Message.id.desc())
    )}  # fmt: skip
    if not bases:
        return []
    # Only leads still in the campaign; those you skipped by hand get no follow-up.
    members = {r.lead_id: r for r in s.scalars(select(CampaignLead).where(CampaignLead.campaign_id == c.id))}
    bases = {lid: m for lid, m in bases.items() if lid in members}
    mine: dict[int, Message] = {}
    for m in s.scalars(
        select(Message).where(Message.campaign_id == c.id, Message.step == step,
                              Message.direction == "out").order_by(Message.id)
    ):  # fmt: skip
        mine[m.lead_id] = m
    ids = set(bases)
    outcome = experiments._outcomes(s, ids)
    leads = {l.id: l for l in s.scalars(select(Lead).where(Lead.id.in_(ids)))}
    # Anything else written to them since (by hand, a reply of yours…).
    later_out = {
        lid: at for lid, at in s.execute(
            select(Message.lead_id, func.max(Message.sent_at))
            .where(Message.lead_id.in_(ids), Message.direction == "out",
                   Message.status == "sent", Message.step != step)
            .group_by(Message.lead_id)
        )
    }  # fmt: skip
    fu = followup_of(s, c, step)
    langs = set(mailer.languages_of(fu)) if fu else set()
    due_by = local() - timedelta(days=days_of(c, step))
    out = []
    for lid, base in bases.items():
        m = mine.get(lid)
        lead = leads.get(lid)
        if m is not None:
            what = {"sent": "sent", "approved": "waiting", "queued": "waiting"}.get(m.status, "cancelled")
        elif lead is None or lead.do_not_contact or lead.status in ("lost", "customer") \
                or outcome.get(lid, {}).get("reply") or (later_out.get(lid) or "") > (base.sent_at or ""):
            what = "replied"
        elif members[lid].no_followups:
            what = "skipped"
        elif fu is None or not fu.body.strip() or not is_on(c, step) or c.status not in ("running", "done"):
            what = "off"
        elif (base.language or "fr") not in langs:
            what = "language"
        else:
            what = "due" if local(base.sent_at) <= due_by else "later"
        out.append((base, what))
    return out


def followup_due_at(c: Campaign, base: Message, step: int = 1) -> str:
    """When it goes out: its delay after the email before it, moved to the next sending
    hours when that falls on an evening or a weekend."""
    due = max(local(base.sent_at) + timedelta(days=days_of(c, step)), local())  # due already: now
    return iso(mailer.first_slot(due, c))


def _write_followup(s: Session, c: Campaign, step: int, base: Message, fu: Template) -> Message:
    """Follow-up `step` to the lead of `base` (the email before it), approved: the worker
    sends it at the next free slot, as a reply in the same conversation."""
    lead = s.get(Lead, base.lead_id)
    d = mailer.draft(s, lead, fu, base.language or "fr")
    subj = base.subject if base.subject.lower().startswith("re:") else f"Re: {base.subject}"
    return mailer.new_outbound(
        s, lead, subject=subj, body=d["body"], to=base.to_addr, step=step,
        template_id=fu.id, campaign_id=c.id, language=d["language"],
        in_reply_to=base.message_id, status="approved", asset_ids=[],
        experiment_id=base.experiment_id,
    )  # fmt: skip


def write_due_followups(s: Session) -> int:
    """Write every follow-up whose day has come (approved: the worker sends them one by one
    inside the sending hours, under the daily cap). The caller commits."""
    n = 0
    for c in s.scalars(select(Campaign).where(Campaign.status.in_(("running", "done")),
                                              Campaign.followups_on.isnot(False))):  # fmt: skip
        for step in STEPS:
            fu = followup_of(s, c, step)
            if fu is None or not is_on(c, step):
                continue
            for base, what in _followup_rows(s, c, step):
                if what == "due":
                    _write_followup(s, c, step, base, fu)
                    n += 1
    return n


# --- acting on some leads of a started campaign -------------------------------------------


def followup_now(s: Session, c: Campaign, lead_id: int) -> Message:
    """Write this lead's next follow-up now instead of on its day (it goes out at the next
    free slot inside the sending hours)."""
    for step in STEPS:
        fu = followup_of(s, c, step)
        row = next(((b, w) for b, w in _followup_rows(s, c, step) if b.lead_id == lead_id), None)
        if row is None or fu is None:
            continue
        base, what = row
        if what in ("later", "due"):
            return _write_followup(s, c, step, base, fu)
        if what == "waiting":
            raise CampaignError("Their follow-up is already written: it goes out at the next free slot.")
        if what in ("replied", "skipped", "language", "off"):
            raise CampaignError({"replied": "They replied (or were contacted since): no follow-up.",
                                 "skipped": "Their follow-ups are skipped. Resume them first.",
                                 "language": "The follow-up isn't written in their language yet.",
                                 "off": "This follow-up is switched off."}[what])  # fmt: skip
    raise CampaignError("No follow-up is left for them: their first email may not be sent yet.")


def _members(s: Session, c: Campaign, lead_ids: list[int]) -> list[CampaignLead]:
    return list(s.scalars(select(CampaignLead).where(CampaignLead.campaign_id == c.id,
                                                     CampaignLead.lead_id.in_(lead_ids))))  # fmt: skip


def _drop_unsent(s: Session, c: Campaign, lead_ids: list[int], steps: tuple[int, ...]) -> int:
    """Delete this campaign's emails to these leads written but not sent yet."""
    rows = list(s.scalars(
        select(Message).where(Message.campaign_id == c.id, Message.lead_id.in_(lead_ids),
                              Message.step.in_(steps), Message.direction == "out",
                              Message.status.in_(("queued", "approved")))
    ))  # fmt: skip
    for m in rows:
        s.delete(m)
    return len(rows)


def set_skip(s: Session, c: Campaign, lead_ids: list[int], skip: bool) -> int:
    """Skip (or resume) the follow-ups of these leads. Skipping drops any already written."""
    rows = _members(s, c, lead_ids)
    for r in rows:
        r.no_followups = skip
    if skip and rows:
        _drop_unsent(s, c, [r.lead_id for r in rows], STEPS)
    return len(rows)


def remove_leads(s: Session, c: Campaign, lead_ids: list[int]) -> int:
    """Take these leads out of the campaign: nothing more goes to them from it (emails
    already sent stay in their history), and "Add leads" won't pick them again."""
    rows = _members(s, c, lead_ids)
    ids = [r.lead_id for r in rows]
    if ids:
        _drop_unsent(s, c, ids, (0, *STEPS))
        for r in rows:
            s.delete(r)
        c.excluded = sorted(set(c.excluded or []) | set(ids))
        c.included = [x for x in (c.included or []) if x not in ids]
    return len(ids)


def move_leads(s: Session, c: Campaign, target: Campaign, lead_ids: list[int]) -> tuple[int, list[str]]:
    """Move leads not emailed yet by this campaign to another one."""
    if target.id == c.id:
        raise CampaignError("Pick another campaign.")
    if target.status not in ("draft", "running", "paused"):
        raise CampaignError("That campaign is finished or archived.")
    sent = set(s.scalars(select(Message.lead_id).where(
        Message.campaign_id == c.id, Message.lead_id.in_(lead_ids), Message.direction == "out",
        Message.status == "sent")))  # fmt: skip
    notes = [f"{len(sent)} already got this campaign's email, so they stay."] if sent else []
    free = [i for i in lead_ids if i not in sent]
    if not free:
        return 0, notes
    remove_leads(s, c, free)
    s.flush()
    if target.status == "draft":
        target.included = list(dict.fromkeys([*(target.included or []), *free]))
        target.excluded = [x for x in (target.excluded or []) if x not in free]
        return len(free), notes
    added, more = add_specific(s, target, free)
    return added, notes + more


# --- what goes out on its own in the next days ----------------------------------------------


def forecast(s: Session, c: Campaign, days: int = 12) -> list[dict[str, Any]]:
    """The follow-ups each coming sending day will carry, if nobody replies: each one on
    its day (or the first free day after it), at most `daily_cap` a day."""
    if c.status not in ("running", "done", "paused"):
        return []
    cap = c.daily_cap or settings.daily_send_cap
    queue: list[tuple[datetime, int, int]] = []  # (due, step, lead)
    planned1: set[int] = set()
    for step in STEPS:
        for base, what in _followup_rows(s, c, step):
            if what in ("later", "due", "waiting"):
                due = local() if what != "later" else local(base.sent_at) + timedelta(days=days_of(c, step))
                queue.append((due, step, base.lead_id))
                if step == 1:
                    planned1.add(base.lead_id)
    two = is_on(c, 2) and followup_of(s, c, 2) is not None
    out: list[dict[str, Any]] = []
    d = local().date()
    today_left = max(0, cap - mailer._sent_today_for(s, c.id))
    wdays, _ = mailer.window(c)
    while len(out) < days:
        if d.weekday() in wdays:
            room = today_left if d == local().date() else cap
            ready = sorted((q for q in queue if q[0].date() <= d), key=lambda q: q[0])
            row = {"day": d.isoformat(), "fu1": 0, "fu2": 0}
            for q in ready[:room]:
                queue.remove(q)
                row[f"fu{q[1]}"] += 1
                if q[1] == 1 and two and q[2] in planned1:  # if still silent, follow-up 2 then
                    queue.append((datetime.combine(d, datetime.min.time(), tzinfo=mailer.TZ)
                                  + timedelta(days=days_of(c, 2)), 2, q[2]))  # fmt: skip
            out.append(row)
        d += timedelta(days=1)
    return out


# --- pause on its own when too many addresses bounce ----------------------------------------


def _bounces(s: Session, c: Campaign) -> tuple[int, int]:
    """(first emails bounced, first emails that went out) for this campaign."""
    rows = s.execute(select(Message.status, Message.error).where(
        Message.campaign_id == c.id, Message.step == 0, Message.direction == "out",
        Message.status.in_(("sent", "failed")))).all()  # fmt: skip
    bounced = sum(1 for st, err in rows if st == "failed" and err == "bounced")
    return bounced, sum(1 for st, _ in rows if st == "sent") + bounced


BOUNCE_MIN = 20  # first emails out before the rate means anything


def pause_on_bounces(s: Session) -> int:
    """Pause running campaigns whose bounce rate passed their limit (only on a new bounce
    since they were last resumed). The caller commits."""
    n = 0
    for c in s.scalars(select(Campaign).where(Campaign.status == "running", Campaign.bounce_pause.isnot(None))):
        bounced, out = _bounces(s, c)
        if out < BOUNCE_MIN or bounced <= (c.bounce_ack or 0) or bounced * 100 < c.bounce_pause * out:
            continue
        c.status = "paused"
        c.paused_reason = (f"Paused on its own: {bounced} of {out} first emails bounced "
                           f"({round(bounced * 100 / out)} %, your limit is {c.bounce_pause} %). "
                           "Fix or remove those addresses, then resume.")  # fmt: skip
        n += 1
    return n


# --- "No reply": the campaign is over for those who never answered ------------------------

CLOSE_DAYS = 15


def close_days_of(c: Campaign) -> int:
    return c.close_days or CLOSE_DAYS


def _silent(s: Session, c: Campaign) -> list[tuple[Lead, Message]]:
    """Leads of this campaign still "contacted" with nothing more to come from it (no email
    waiting, no follow-up planned), each with the last email it got from the campaign."""
    coming: set[int] = set()
    for step in STEPS:
        coming |= {b.lead_id for b, w in _followup_rows(s, c, step) if w in ("later", "due", "waiting")}
    last: dict[int, Message] = {}
    for m in s.scalars(
        select(Message).where(Message.campaign_id == c.id, Message.direction == "out",
                              Message.status == "sent").order_by(Message.sent_at)
    ):  # fmt: skip
        last[m.lead_id] = m
    pending = set(s.scalars(
        select(Message.lead_id).where(Message.lead_id.in_(list(last)), Message.direction == "out",
                                      Message.status.in_(("queued", "approved")))
    )) if last else set()  # fmt: skip
    outcome = experiments._outcomes(s, set(last))
    out = []
    for lead in s.scalars(select(Lead).where(Lead.id.in_(list(last)))) if last else []:
        if lead.status != "contacted" or lead.do_not_contact or lead.id in coming or lead.id in pending \
                or outcome.get(lead.id, {}).get("reply"):
            continue
        out.append((lead, last[lead.id]))
    return out


def close_silent(s: Session) -> int:
    """Mark "no_reply" the leads who never answered any of a campaign's emails, `close_days`
    after the last one (when no follow-up is still to come). The caller commits."""
    n = 0
    for c in s.scalars(select(Campaign).where(Campaign.status.in_(("running", "done")),
                                              Campaign.close_on.isnot(False))):  # fmt: skip
        by = local() - timedelta(days=close_days_of(c))
        for lead, m in _silent(s, c):
            if local(m.sent_at) > by:
                continue
            emails = s.scalar(select(func.count(Message.id)).where(
                Message.campaign_id == c.id, Message.lead_id == lead.id,
                Message.direction == "out", Message.status == "sent")) or 0  # fmt: skip
            s.add(Activity(lead_id=lead.id, kind="status",
                           text=f"contacted → no reply: no answer to {emails} email{'s' if emails > 1 else ''} "
                                f"of “{c.name}”"))  # fmt: skip
            lead.status = "no_reply"
            n += 1
    return n


def close_status(s: Session, c: Campaign) -> dict[str, Any]:
    """For the campaign page: how many were marked "No reply", and when the next ones are."""
    done = s.scalar(select(func.count(CampaignLead.id)).join(Lead, Lead.id == CampaignLead.lead_id)
                    .where(CampaignLead.campaign_id == c.id, Lead.status == "no_reply")) or 0  # fmt: skip
    nxt = sorted(iso(max(local(m.sent_at) + timedelta(days=close_days_of(c)), local())) for _, m in _silent(s, c))
    return {"done": done, "later": len(nxt), "next_at": nxt[0] if nxt else None}


def followup_status(s: Session, c: Campaign, step: int = 1) -> dict[str, Any]:
    """For the campaign page: how this follow-up goes, and who won't get it."""
    rows = _followup_rows(s, c, step)
    count = Counter(w for _, w in rows)
    coming = sorted(followup_due_at(c, b, step) for b, w in rows if w in ("later", "due"))
    missing: Counter[str] = Counter((b.language or "fr") for b, w in rows if w == "language")
    return {
        "sent": count["sent"], "waiting": count["waiting"] + count["due"],
        "later": count["later"], "next_at": coming[0] if coming else None,
        "replied": count["replied"], "cancelled": count["cancelled"],
        "missing": dict(missing),
    }


def followup_plan(s: Session, lead_id: int) -> list[dict[str, Any]]:
    """The follow-ups still to come for this lead (the next one per campaign), with their day."""
    out = []
    cids = set(s.scalars(
        select(Message.campaign_id).where(Message.lead_id == lead_id, Message.step == 0,
                                          Message.status == "sent", Message.campaign_id.isnot(None))
    ))  # fmt: skip
    for cid in cids:
        c = s.get(Campaign, cid)
        if c is None:
            continue
        for step in STEPS:
            row = next(((b, w) for b, w in _followup_rows(s, c, step) if b.lead_id == lead_id), None)
            if row and row[1] in ("later", "due"):
                out.append({"campaign_id": c.id, "campaign": c.name, "step": step,
                            "due_at": followup_due_at(c, row[0], step), "language": row[0].language or "fr"})  # fmt: skip
    return out
