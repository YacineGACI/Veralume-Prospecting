"""A/B tests of the first email.

A running test owns the first emails to one segment: each new lead gets one of the
versions, chosen to keep the versions balanced (ties broken at random, the same way each
time for a given lead). Only leads every version fits enter the test, so the versions
are compared on the same kind of lead. Follow-ups are shared: a reply at any step counts
for the version of the first email.

The analysis is Bayesian: each version's rate gets a Beta(1 + hits, 1 + misses)
posterior, and we report the chance each version is the best. Small numbers are said to
be small: with reply rates around 5 %, telling 5 % from 10 % takes hundreds of emails
per version.
"""

from __future__ import annotations

import math
import random
import re
from collections import defaultdict
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from prospecting import mailer
from prospecting.contacts import shared_contacts, usable_emails
from prospecting.db import Activity, Experiment, Lead, Message, Template, now

LETTERS = "ABCD"
METRICS = {"reply": "Replies", "interested": "Interested (interview or further)"}
REPLIED = {"replied", "interview", "pilot", "customer"}
INTERESTED = {"interview", "pilot", "customer"}
_REACHED = re.compile(r"→ (replied|interview|pilot|customer)\b")
MIN_EACH = 20  # below this many per version, results swing too much to read
DRAWS = 20_000


def running_for(s: Session, kind: str) -> Experiment | None:
    return s.scalars(
        select(Experiment).where(Experiment.kind == kind, Experiment.status == "running")
    ).first()


def versions(s: Session, exp: Experiment) -> list[Template]:
    rows = {t.id: t for t in s.scalars(select(Template).where(Template.id.in_(exp.template_ids)))}
    return [rows[i] for i in exp.template_ids if i in rows]


def assign(s: Session, lead: Lead) -> tuple[Template, Experiment] | None:
    """The version this lead gets, if a test runs for its segment and every version fits it."""
    exp = running_for(s, lead.kind)
    if exp is None:
        return None
    tpls = [t for t in versions(s, exp) if not t.archived]
    if len(tpls) < 2 or not all(mailer.fits_lead(t, lead) for t in tpls):
        return None
    by_id = {t.id: t for t in tpls}
    before = s.scalar(
        select(Message.template_id).where(
            Message.lead_id == lead.id, Message.experiment_id == exp.id, Message.step == 0
        )
    )
    if before in by_id:
        return by_id[before], exp
    counts = dict.fromkeys(by_id, 0)
    for tid, n in s.execute(
        select(Message.template_id, func.count(func.distinct(Message.lead_id)))
        .where(Message.experiment_id == exp.id, Message.step == 0,
               Message.status != "cancelled")
        .group_by(Message.template_id)
    ):  # fmt: skip
        if tid in counts:
            counts[tid] = n
    low = min(counts.values())
    pick = random.Random(f"{exp.id}:{lead.id}").choice([t for t in by_id if counts[t] == low])
    return by_id[pick], exp


def choose_first(s: Session, lead: Lead) -> tuple[Template | None, Experiment | None]:
    """The first email for this lead: its A/B test version, or the usual best template."""
    got = assign(s, lead)
    if got:
        return got
    return mailer.pick_template(s, lead, 0), None


def audience_rule(tpls: list[Template]) -> str | None:
    """Who can enter, in words, when a version restricts it."""
    if any("{lean}" in t.body + t.subject for t in tpls):
        return "Only leads with a measured lean of 1° or more enter, because a version quotes their lean."
    if any(t.key == "ph_straight" for t in tpls):
        return "Only leads whose photos measured straight enter, because a version says so."
    return None


def audience(s: Session, exp: Experiment) -> int:
    """Leads still to write to that would enter this test."""
    tpls = versions(s, exp)
    se, _ = shared_contacts()
    started = set(
        s.scalars(select(Message.lead_id).where(Message.direction == "out", Message.step == 0))
    )
    n = 0
    for lead in s.scalars(
        select(Lead).where(Lead.kind == exp.kind, Lead.status == "new",
                           Lead.do_not_contact.is_(False), Lead.last_contacted_at.is_(None))
    ):  # fmt: skip
        if lead.id not in started and all(mailer.fits_lead(t, lead) for t in tpls) \
                and usable_emails(lead, se):  # fmt: skip
            n += 1
    return n


# --- analysis ------------------------------------------------------------------------


def chance_best(counts: list[tuple[int, int]], seed: int = 0) -> tuple[list[float], list[tuple[float, float]]]:
    """For (hits, trials) per version: the chance each is the best, and a 90 % interval
    for each rate, from Beta(1 + hits, 1 + misses) posteriors."""
    rng = random.Random(seed)
    wins = [0] * len(counts)
    samples: list[list[float]] = [[] for _ in counts]
    for _ in range(DRAWS):
        draw = [rng.betavariate(1 + k, 1 + n - k) for k, n in counts]
        top = max(draw)
        best = [i for i, x in enumerate(draw) if x == top]
        wins[rng.choice(best)] += 1
        for i, x in enumerate(draw):
            samples[i].append(x)
    ranges = []
    for xs in samples:
        xs.sort()
        ranges.append((xs[int(0.05 * len(xs))], xs[int(0.95 * len(xs)) - 1]))
    return [w / DRAWS for w in wins], ranges


def needed_each(rate: float) -> int:
    """Emails per version to spot a version that doubles `rate` (95 % confidence,
    80 % power, two proportions)."""
    p1 = min(max(rate, 0.02), 0.45)
    p2 = 2 * p1
    return math.ceil((1.96 + 0.84) ** 2 * (p1 * (1 - p1) + p2 * (1 - p2)) / (p2 - p1) ** 2)


def _outcomes(s: Session, lead_ids: set[int]) -> dict[int, dict[str, bool]]:
    if not lead_ids:
        return {}
    leads = {l.id: l for l in s.scalars(select(Lead).where(Lead.id.in_(lead_ids)))}
    wrote = set(
        s.scalars(select(Message.lead_id).where(Message.direction == "in",
                                                Message.lead_id.in_(lead_ids)))
    )  # fmt: skip
    reached: dict[int, set[str]] = defaultdict(set)
    for lid, text in s.execute(
        select(Activity.lead_id, Activity.text)
        .where(Activity.kind == "status", Activity.lead_id.in_(lead_ids))
    ):  # fmt: skip
        reached[lid].update(_REACHED.findall(text or ""))
    out = {}
    for lid, lead in leads.items():
        stages = reached[lid] | ({lead.status} & REPLIED)
        interested = bool(stages & INTERESTED)
        stopped = bool(lead.do_not_contact) and not interested
        out[lid] = {
            # a "stop" is an answer, not a win
            "reply": (lid in wrote or bool(stages)) and not stopped,
            "interested": interested,
            "stopped": stopped,
        }
    return out


def results(s: Session, exp: Experiment) -> dict[str, Any]:
    tpls = versions(s, exp)
    first = list(
        s.scalars(
            select(Message).where(Message.experiment_id == exp.id, Message.step == 0,
                                  Message.direction == "out")
            .order_by(Message.id)
        )
    )  # fmt: skip
    sent: dict[int, Message] = {}
    waiting: dict[int | None, int] = defaultdict(int)
    for m in first:
        if m.status == "sent":
            sent.setdefault(m.lead_id, m)
        elif m.status in ("queued", "approved"):
            waiting[m.template_id] += 1
    outcome = _outcomes(s, set(sent))
    rows = []
    for i, t in enumerate(tpls):
        mine = [m for m in sent.values() if m.template_id == t.id]
        o = [outcome[m.lead_id] for m in mine if m.lead_id in outcome]
        rows.append({
            "template_id": t.id, "letter": LETTERS[i], "name": t.name, "subject": t.subject,
            "body": t.body, "archived": t.archived,
            "sent": len(mine), "replied": sum(x["reply"] for x in o),
            "interested": sum(x["interested"] for x in o), "stopped": sum(x["stopped"] for x in o),
            "edited": sum(1 for m in mine if m.edited), "waiting": waiting.get(t.id, 0),
        })  # fmt: skip
    hits = [(r["replied"] if exp.metric == "reply" else r["interested"], r["sent"]) for r in rows]
    best_p, ranges = chance_best(hits, seed=exp.id) if rows else ([], [])
    for r, (k, n), p, (lo, hi) in zip(rows, hits, best_p, ranges):
        r.update(hits=k, rate=k / n if n else None, chance_best=p, low=lo, high=hi)

    total_n = sum(n for _, n in hits)
    pooled = sum(k for k, _ in hits) / total_n if total_n else 0.0
    need = needed_each(pooled if total_n >= 40 else 0.05)
    since = mailer.iso(mailer.local() - timedelta(days=14))
    recent = sum(1 for m in sent.values() if (m.sent_at or "") >= since)
    pace = recent / 10  # working days in two weeks
    smallest = min((n for _, n in hits), default=0)
    lead = max(range(len(rows)), key=lambda i: best_p[i]) if rows else None
    days_left = (
        math.ceil((need - smallest) * len(rows) / pace) if pace and smallest < need else None
    )
    return {
        "versions": rows, "verdict": _verdict(exp, rows, lead, smallest, need),
        "sent": total_n, "pooled": pooled, "needed_each": need, "pace": round(pace, 1),
        "days_left": days_left, "audience_rule": audience_rule(tpls),
        "leader": rows[lead]["letter"] if lead is not None and total_n else None,
    }  # fmt: skip


def _verdict(
    exp: Experiment, rows: list[dict[str, Any]], lead: int | None, smallest: int, need: int
) -> dict[str, str]:
    what = "replies" if exp.metric == "reply" else "interested leads"
    if not rows or not sum(r["sent"] for r in rows):
        return {"state": "waiting", "text": "No first email sent in this test yet."}
    top = rows[lead]
    pct = round(top["chance_best"] * 100)
    if smallest < MIN_EACH:
        return {
            "state": "early",
            "text": f"Too early to tell: the smallest version has {smallest} sent. "
            f"Results swing a lot until each has {MIN_EACH} to 30.",
        }
    if top["chance_best"] >= 0.95:
        return {"state": "winner",
                "text": f"{top['letter']} wins: {pct} % chance it gets the most {what}."}  # fmt: skip
    if smallest >= need and top["chance_best"] < 0.8:
        return {"state": "tie",
                "text": "No real difference after a fair number of emails. Keep the one you "
                        "prefer, or test a bolder change."}  # fmt: skip
    return {"state": "leaning",
            "text": f"{top['letter']} is ahead ({pct} % chance it's best), but it isn't sure "
                    "yet. Keep sending."}  # fmt: skip


def stop(s: Session, exp: Experiment, winner_id: int | None, archive_losers: bool) -> None:
    exp.status, exp.ended_at, exp.winner_id = "stopped", now(), winner_id
    if winner_id is None:
        return
    # The winner becomes this segment's first email from now on.
    mailer.set_setting(s, f"first_template:{exp.kind}", winner_id)
    if archive_losers:
        for t in versions(s, exp):
            if t.id != winner_id:
                t.archived = True
