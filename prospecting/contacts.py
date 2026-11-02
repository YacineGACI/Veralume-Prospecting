"""Which of a lead's emails and phones really reach them.

Some addresses we collected are not the lead's: a network's head office (`contact@safti.fr`
on 400 independent agents), a data-protection inbox (`donneespersonnelles@laforet.com`), or
a switchboard shared by many leads. They stay in the database with their source, but are
flagged, never picked to send to, and don't count in the score.
"""

from __future__ import annotations

import re
import time
from collections import Counter
from typing import Any

from sqlalchemy import select

from prospecting.db import Lead, session
from prospecting.text import NETWORKS, email_is_personal

# Inboxes nobody reads for business: privacy officers, legal, accounting, jobs.
_ADMIN_LOCAL = re.compile(
    r"^(dpo|rgpd|gdpr|privacy|donnees|donneespersonnelles|cnil|juridique|legal|abuse|"
    r"webmaster|postmaster|hostmaster|noreply|no-reply|comptabilite|compta|facturation|"
    r"recrutement|rh|jobs|candidature|informatique-et-libertes|cil|marketing)"
)
# A head office or switchboard: a generic address on 5+ leads (any address on 20+), a
# phone on 6+. Below that it's usually one agency's branches, which still reach someone.
SHARED_GENERIC_EMAIL = 5
SHARED_ANY_EMAIL = 20
SHARED_PHONE = 6
_CACHE: dict[str, Any] = {"at": -1e9, "emails": set(), "phones": set()}


def shared_contacts(max_age_s: float = 120) -> tuple[set[str], set[str]]:
    """Head-office emails and switchboard phones, by how many leads carry them (cached)."""
    if time.monotonic() - _CACHE["at"] > max_age_s:
        emails: Counter[str] = Counter()
        phones: Counter[str] = Counter()
        with session() as s:
            for em, ph in s.execute(select(Lead.emails, Lead.phones)):
                emails.update({e["value"].lower() for e in em or []})
                phones.update({p["value"] for p in ph or []})
        _CACHE.update(
            at=time.monotonic(),
            emails={
                e for e, n in emails.items()
                if n >= SHARED_ANY_EMAIL or (n >= SHARED_GENERIC_EMAIL and not email_is_personal(e))
            },  # fmt: skip
            phones={p for p, n in phones.items() if n >= SHARED_PHONE},
        )
    return _CACHE["emails"], _CACHE["phones"]


def email_problem(item: dict[str, Any], lead: Lead, shared: set[str] | None = None) -> str | None:
    """Why this address won't reach the lead, or None if it should."""
    email = item["value"].lower()
    local, _, domain = email.partition("@")
    if item.get("bounced"):
        return "bounced"
    if _ADMIN_LOCAL.match(local):
        return "data-protection or admin inbox"
    if domain in NETWORKS and not email_is_personal(email) and lead.kind != "agency":
        return "network head-office address"
    if shared and email in shared:
        return "shared by several leads"
    return None


def phone_problem(item: dict[str, Any], shared: set[str] | None = None) -> str | None:
    if item.get("wrong"):
        return "wrong number"
    if shared and item["value"] in shared:
        return "shared by several leads (head office?)"
    return None


def usable_emails(lead: Lead, shared: set[str] | None = None) -> list[str]:
    """Addresses worth sending to, personal ones first."""
    ok = [e["value"] for e in lead.emails or [] if not email_problem(e, lead, shared)]
    return sorted(ok, key=lambda e: not email_is_personal(e))


def usable_phones(lead: Lead, shared: set[str] | None = None) -> list[str]:
    ok = [p["value"] for p in lead.phones or [] if not phone_problem(p, shared)]
    # Mobiles first: independents and photographers answer their own.
    return sorted(ok, key=lambda p: not p.startswith(("06", "07")))


def flags(
    lead: Lead, shared_e: set[str] | None = None, shared_p: set[str] | None = None
) -> list[str]:
    """Short warnings shown on the lead: contacts that won't reach them."""
    out: list[str] = []
    bad_e = {email_problem(e, lead, shared_e) for e in lead.emails or []} - {None}
    if lead.emails and not usable_emails(lead, shared_e):
        out.append(f"Email: {sorted(bad_e)[0]}" if bad_e else "No usable email")
    bad_p = {phone_problem(p, shared_p) for p in lead.phones or []} - {None}
    if lead.phones and not usable_phones(lead, shared_p):
        out.append(f"Phone: {sorted(bad_p)[0]}")
    return out


def mark(lead: Lead, field: str, value: str, **flag: Any) -> None:
    """Set a flag (bounced, wrong) on one email or phone. JSON columns need a new list."""
    items = [dict(i) for i in getattr(lead, field) or []]
    for i in items:
        if i["value"].lower() == value.lower():
            i.update(flag)
    setattr(lead, field, items)
