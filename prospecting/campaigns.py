"""Campaigns: who to write to, which email versions they get, and how it goes.

A draft campaign is a set of filters, a size, an order and one to four email versions
(templates owned by the campaign). Starting it freezes the list of leads (campaign_leads,
in sending order) and hands out the versions in equal shares, at random. With two or more
versions, the campaign owns an A/B test (an Experiment) analysed like any other.

Each working morning the next batch is written: waiting in Today for your approval
(review "daily"), or approved at once (review "auto"). Follow-ups, when on, are written
after each first email and always wait for you.
"""

from __future__ import annotations

import random
import unicodedata
import uuid
from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from prospecting import experiments, mailer
from prospecting.contacts import shared_contacts, usable_emails, usable_phones
from prospecting.db import (
    Campaign, CampaignLead, Experiment, Lead, Message, Template, now,
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
    return list(s.scalars(
        select(Template).where(Template.campaign_id == c.id, Template.archived.is_(False))
        .order_by(Template.id)
    ))  # fmt: skip


def _busy_elsewhere(s: Session, campaign_id: int | None) -> set[int]:
    """Leads waiting in another running or paused campaign."""
    q = (
        select(CampaignLead.lead_id).join(Campaign, Campaign.id == CampaignLead.campaign_id)
        .where(Campaign.status.in_(("running", "paused")))
    )  # fmt: skip
    if campaign_id:
        q = q.where(CampaignLead.campaign_id != campaign_id)
    return set(s.scalars(q))


def _started(s: Session) -> set[int]:
    """Leads who already have a first email written or sent."""
    return set(s.scalars(
        select(Message.lead_id).where(Message.direction == "out", Message.step == 0,
                                      Message.status.in_(ACTIVE))
    ))  # fmt: skip


def why_not(s: Session, lead: Lead, campaign_id: int | None = None) -> str | None:
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
    return None


def facets(s: Session, leads: list[Lead]) -> dict[str, Any]:
    """How many of these leads have each trait: shown beside each filter, so you see what
    a filter would leave before clicking it."""
    se, sp = shared_contacts()
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
    campaign_id: int | None = None, needs_lean: bool = False,
) -> list[Lead]:  # fmt: skip
    """Every lead the filters let in, never contacted, with an address of their own, in
    sending order."""
    f = filters or {}
    statuses = ("shortlisted",) if f.get("shortlisted") else ("new", "shortlisted")
    q = select(Lead).where(
        Lead.kind == kind, Lead.do_not_contact.is_(False),
        Lead.status.in_(statuses), Lead.last_contacted_at.is_(None),
    )  # fmt: skip
    if f.get("countries"):
        q = q.where(func.coalesce(Lead.country, "FR").in_([str(c) for c in f["countries"]]))
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
    se, sp = shared_contacts()
    out = []
    for lead in s.scalars(q):
        if lead.id in started or lead.id in busy or not usable_emails(lead, se):
            continue
        if not _matches(lead, f, se, sp):
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


def hand_picked(s: Session, ids: list[int] | None, campaign_id: int | None = None) -> tuple[list[Lead], list[tuple[Lead, str]]]:
    """Leads added by hand: those that can be contacted, and the others with the reason."""
    ok, blocked = [], []
    for i in ids or []:
        lead = s.get(Lead, i)
        if lead is None:
            continue
        why = why_not(s, lead, campaign_id)
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
    every = candidates(s, c.kind, c.filters or {}, c.lead_order or "small", seed=c.id or 0,
                       campaign_id=c.id, needs_lean=needs_lean(versions(s, c)))  # fmt: skip
    hand, _ = hand_picked(s, c.included, c.id)
    return pick(every, hand, c.size or 0, c.excluded), len(every)


def add_specific(s: Session, c: Campaign, lead_ids: list[int]) -> tuple[int, list[str]]:
    """Add chosen leads to a campaign. A draft keeps them in its hand-picked list; a
    started campaign hands them a version at once. Returns how many were added and why
    the others weren't."""
    hand, blocked = hand_picked(s, lead_ids, c.id)
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
                       campaign_id=c.id, needs_lean=needs_lean(tpls))  # fmt: skip
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
        t = s.get(Template, row.template_id) if row.template_id else None
        if lead is None or t is None or lead.do_not_contact or not usable_emails(lead, se):
            continue
        if lead.last_contacted_at:  # you wrote to them some other way meanwhile
            continue
        d = mailer.draft(s, lead, t)
        exp_id = c.experiment_id if c.experiment_id else None
        exp = s.get(Experiment, exp_id) if exp_id else None
        mailer.new_outbound(
            s, lead, subject=d["subject"], body=d["body"], template_id=t.id, campaign_id=c.id,
            attach_photo_id=d["attach_photo_id"], asset_ids=d["asset_ids"],
            experiment_id=exp_id if exp and exp.status == "running" else None,
            status="approved" if c.review == "auto" and mailer.send_mode(s) == "scheduled" else "queued",
        )  # fmt: skip
        made += 1
    if not made and all(r.lead_id in written for r in rows):
        waiting = any(m.status in ("queued", "approved") for m in written.values())
        if rows and not waiting:
            c.status, c.ended_at = "done", now()
    return made


def prepare_running(s: Session) -> int:
    """Once a working day, write each running campaign's batch."""
    if not mailer.in_window():
        return 0
    today = local().date().isoformat()
    n = 0
    for c in s.scalars(select(Campaign).where(Campaign.status == "running")):
        key = f"prepared:{c.id}"
        if mailer.get_setting(s, key, None) != today:
            n += prepare(s, c)
            if not c.start_on or c.start_on <= today:
                mailer.set_setting(s, key, today)
    return n


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
    letters = {t.id: _letter(i) for i, t in enumerate(tpls)}
    rows = list(s.scalars(
        select(CampaignLead).where(CampaignLead.campaign_id == c.id).order_by(CampaignLead.rank)
    ))  # fmt: skip
    written = _written(s, c)
    sent = {lid: m for lid, m in written.items() if m.status == "sent"}
    outcome = experiments._outcomes(s, set(sent))
    leads = {l.id: l for l in s.scalars(select(Lead).where(Lead.id.in_([r.lead_id for r in rows])))} if rows else {}
    replies: dict[int, Message] = {}
    if sent:
        for m in s.scalars(
            select(Message).where(Message.direction == "in", Message.lead_id.in_(list(sent)))
            .order_by(Message.id)
        ):  # fmt: skip
            replies[m.lead_id] = m

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
            return "failed", m.due_at
        o = outcome.get(row.lead_id, {})
        if o.get("stopped"):
            return "stopped", m.sent_at
        if o.get("interested"):
            return "interested", m.sent_at
        if o.get("reply"):
            return "replied", (replies.get(row.lead_id) or m).sent_at
        return "waiting", m.sent_at

    states = [(r, *state(r)) for r in rows]
    count = Counter(st for _, st, _ in states)
    replied = sum(count[k] for k in ("replied", "interested"))
    totals = {
        "leads": len(rows), "sent": len(sent), "to_review": count["to_review"],
        "approved": count["approved"], "not_sent": count["not_sent"],
        "replied": replied, "interested": count["interested"], "stopped": count["stopped"],
        "skipped": count["skipped"], "failed": count["failed"],
        "pilots": sum(1 for l in leads.values() if l.pilot_stage),
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

    out: dict[str, Any] = {"totals": totals, "test": test, "versions": per_version, "days": span}
    left = totals["not_sent"]
    cap = c.daily_cap or 20
    out["days_left"] = -(-left // cap) if left else 0
    if with_leads:
        out["leads"] = [
            {"id": r.lead_id, "name": leads[r.lead_id].name if r.lead_id in leads else "?",
             "person": _person(leads.get(r.lead_id)), "city": leads[r.lead_id].city if r.lead_id in leads else None,
             "letter": letters.get(r.template_id, "?"), "state": st, "at": at,
             "reply": (replies[r.lead_id].body[:160] if r.lead_id in replies else None),
             "message_id": written[r.lead_id].id if r.lead_id in written else None}
            for r, st, at in states
        ]  # fmt: skip
    return out


def _person(lead: Lead | None) -> str | None:
    if lead is None:
        return None
    from prospecting.outreach import person

    return person(lead)


def new_version(s: Session, c: Campaign, name: str, subject: str, body: str,
                asset_ids: list[int], attach_photo: bool) -> Template:  # fmt: skip
    t = Template(
        key=f"camp{c.id}_{uuid.uuid4().hex[:8]}", name=name.strip()[:120] or "Version",
        segment=c.kind, step=0, subject=subject, body=body, attach_photo=attach_photo,
        asset_ids=list(asset_ids), campaign_id=c.id,
    )  # fmt: skip
    s.add(t)
    s.flush()
    return t
