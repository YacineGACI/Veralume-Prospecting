"""Is this photographer a new or a one-person business? Asked of the company register.

Photographers are registered under activity 74.20Z, usually as sole traders (often
micro-entrepreneurs) in their own name. We look up the person's or the studio's name and
keep the match only when the name agrees and, if we know it, the département too.
New businesses (created in the last 3 years) and one-person ones are the most open to a
new tool, so scoring favours them; they are tagged, never filtered out.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date

import httpx
from sqlalchemy import select

from prospecting.config import settings
from prospecting.db import Lead, session
from prospecting.text import name_similarity

API = "https://recherche-entreprises.api.gouv.fr/search"
NEW_YEARS = 3
PHOTO_CODES = "74.20Z,59.11A,59.11B,59.11C,74.10Z,73.11Z,90.03A,90.03B"


def _find(client: httpx.Client, name: str, dep: str | None) -> dict | None:
    # Photography, but also video, design and advertising codes some photographers use.
    params = {"q": name, "activite_principale": PHOTO_CODES, "per_page": 5}
    for attempt in range(4):
        r = client.get(API, params=params)
        if r.status_code != 429:
            break
        time.sleep(2 + attempt * 2)
    if r.status_code != 200:
        return None
    best = None
    for c in r.json().get("results", []):
        names = [c.get("nom_complet") or "", c.get("nom_raison_sociale") or ""]
        names += (c.get("siege") or {}).get("liste_enseignes") or []
        sim = max((name_similarity(name, n) for n in names if n), default=0.0)
        if sim < 0.67:
            continue
        cp = (c.get("siege") or {}).get("code_postal") or ""
        if dep and cp and not cp.startswith(dep):
            continue
        if best is None or sim > best[0]:
            best = (sim, c)
    return best[1] if best else None


def run(lead_ids: list[int] | None = None, progress: Callable[[str], None] = print) -> int:
    """Look up photographers not checked yet. Returns how many were matched."""
    with session() as s:
        q = select(Lead).where(Lead.kind == "photographer", Lead.country == "FR")  # French register
        if lead_ids:
            q = q.where(Lead.id.in_(lead_ids))
        todo = [
            (l.id, [p["name"] for p in (l.people or [])] + [l.name], (l.postcode or "")[:2] or None)
            for l in s.scalars(q)
            if "register_checked" not in (l.tags or [])
        ]
    matched = 0
    today = date.today()
    cutoff = today.replace(year=today.year - NEW_YEARS).isoformat()
    with httpx.Client(timeout=20, headers={"User-Agent": settings.user_agent}) as client:
        for i, (lead_id, names, dep) in enumerate(todo, 1):
            hit = None
            for name in dict.fromkeys(n for n in names if n):
                try:
                    hit = _find(client, name, dep)
                except httpx.HTTPError:
                    hit = None
                if hit:
                    break
                time.sleep(0.2)
            with session() as s:
                lead = s.get(Lead, lead_id)
                if lead is None:
                    continue
                tags = {"register_checked"}
                if hit:
                    matched += 1
                    lead.siren = lead.siren or hit["siren"]
                    lead.founded = hit.get("date_creation")
                    lead.legal_form = hit.get("nature_juridique")
                    lead.headcount = hit.get("tranche_effectif_salarie")
                    if lead.founded and lead.founded >= cutoff:
                        tags.add("new_business")
                    if (lead.legal_form or "").startswith("1"):
                        tags.add("sole_trader")
                    lead.add_source(
                        "registre",
                        f"https://annuaire-entreprises.data.gouv.fr/entreprise/{hit['siren']}",
                    )
                lead.add_tags(tags)
                s.commit()
            if i % 50 == 0 or i == len(todo):
                progress(f"register check: {i}/{len(todo)}, {matched} matched")
            time.sleep(0.2)
    return matched
