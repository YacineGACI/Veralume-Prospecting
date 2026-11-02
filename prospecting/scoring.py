"""Lead score, 0-100, with the reasons spelled out.

- fit (35): who they are. Small agencies decide fast and do their own photos; large
  corporate groups decide centrally; syndic-only firms rarely market photos.
- reach (30): an email, a phone, a named person.
- need (30): how much their listing photos lean, as Veralume measures it.
- volume (5) and proximity (3).
"""

from __future__ import annotations

from sqlalchemy import select

from prospecting.contacts import shared_contacts, usable_emails, usable_phones
from prospecting.db import Lead, session
from prospecting.text import email_is_personal

SMALL_TEAM = {"01", "02", "03", "11", "12"}  # 1 to 49 employees
HEADCOUNT_LABELS = {
    "NN": "no employees declared", "00": "0 employees", "01": "1-2 employees",
    "02": "3-5 employees", "03": "6-9 employees", "11": "10-19 employees",
    "12": "20-49 employees", "21": "50-99 employees", "22": "100-199 employees",
    "31": "200-249 employees", "32": "250-499 employees", "41": "500-999 employees",
    "42": "1000-1999 employees", "51": "2000-4999 employees", "52": "5000-9999 employees",
    "53": "10000+ employees",
}  # fmt: skip


def score(
    lead: Lead, shared: tuple[set[str], set[str]] | None = None
) -> tuple[int, list[str]]:
    """Score one lead. `shared` (from shared_contacts) drops head-office emails and phones."""
    shared_e, shared_p = shared or (None, None)
    reasons: list[str] = []
    tags = set(lead.tags or [])
    pts = 0

    # Fit
    if lead.kind == "photographer":
        if "real_estate" in tags:
            pts += 30
            reasons.append("+30 real-estate photographer: edits for several agencies")
        else:
            pts += 12
            reasons.append("+12 photographer, real-estate work not confirmed")
        # Small and new photographers are the most open to a new tool: favour them.
        if "new_business" in tags:
            pts += 10
            reasons.append(
                f"+10 new business (since {(lead.founded or '')[:4]}): building their clientele"
            )
        elif "recent_profile" in tags:
            pts += 6
            reasons.append("+6 recent sign-up on the photographer directory")
        if "sole_trader" in tags:
            pts += 5
            reasons.append("+5 one-person business: decides alone, edits their own shoots")
        if "few_reviews" in tags:
            pts += 4
            reasons.append("+4 few client reviews yet: still growing")
        if "social_only" in tags or (lead.socials and not lead.website):
            pts += 4
            reasons.append("+4 works from Instagram/Facebook: likely just starting")
    elif lead.kind == "agency":
        pts += 25
        reasons.append("+25 estate agency")
        if lead.headcount in SMALL_TEAM:
            pts += 5
            reasons.append(f"+5 small team ({HEADCOUNT_LABELS[lead.headcount]}): owner decides")
        if lead.company_category in ("GE", "ETI"):
            pts -= 10
            reasons.append("-10 large group: photos likely decided centrally")
        if tags & {"syndic", "management"} and "transaction" not in tags:
            pts -= 10
            reasons.append("-10 management / syndic only, no sales seen")
        elif "commercial" in tags and "transaction" not in tags:
            pts -= 5
            reasons.append("-5 commercial property only")
    elif lead.kind == "independent_agent":
        pts += 20
        reasons.append("+20 independent agent: shoots and edits their own photos")
    else:
        pts += 10
        reasons.append("+10 other")

    # Reach
    emails = usable_emails(lead, shared_e)
    phones = usable_phones(lead, shared_p)
    if lead.emails and not emails:
        reasons.append("+0 email found, but not theirs (head office or admin inbox)")
    if emails:
        pts += 12
        reasons.append("+12 email found")
        if any(email_is_personal(e) for e in emails):
            pts += 4
            reasons.append("+4 personal email")
    if phones:
        pts += 10
        reasons.append("+10 phone found")
    if lead.kind == "photographer" and not emails and not phones and lead.socials:
        pts += 8
        reasons.append("+8 reachable by Instagram/Facebook message")
    if lead.people:
        pts += 4
        reasons.append(f"+4 named contact ({lead.people[0]['name']})")

    # Need
    if lead.kind == "photographer":
        pts += 20
        reasons.append("+20 every shoot needs editing: Veralume saves them hours")
    elif lead.lean_median is None:
        pts += 8
        reasons.append("+8 photos not measured yet")
    elif lead.lean_median >= 3:
        pts += 30
        reasons.append(f"+30 walls lean {lead.lean_median:.1f}° (median): clearly visible")
    elif lead.lean_median >= 1.5:
        pts += 22
        reasons.append(f"+22 walls lean {lead.lean_median:.1f}° (median): visible")
    elif lead.lean_median >= 0.8:
        pts += 12
        reasons.append(f"+12 walls lean {lead.lean_median:.1f}° (median): slight")
    else:
        pts += 3
        reasons.append(f"+3 photos already straight ({lead.lean_median:.1f}°): maybe a pro already")

    # Volume and proximity
    if (lead.listings_seen or 0) >= 5:
        pts += 5
        reasons.append(f"+5 active: {lead.listings_seen} listings on the site")
    elif lead.listings_seen:
        pts += 2
        reasons.append(f"+2 {lead.listings_seen} listings on the site")
    if lead.distance_km is not None and lead.distance_km <= 10:
        pts += 3
        reasons.append(f"+3 close: {lead.distance_km:.0f} km from Strasbourg")

    if lead.do_not_contact:
        return 0, ["do not contact"]
    return max(0, min(100, pts)), reasons


def rescore(lead_ids: list[int] | None = None) -> int:
    shared = shared_contacts(max_age_s=0)
    with session() as s:
        q = select(Lead)
        if lead_ids:
            q = q.where(Lead.id.in_(lead_ids))
        n = 0
        for lead in s.scalars(q):
            lead.score, lead.score_reasons = score(lead, shared)
            n += 1
        s.commit()
    return n
