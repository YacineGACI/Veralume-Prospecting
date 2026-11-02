"""Estate agencies from the French company register (recherche-entreprises.api.gouv.fr).

Free, official, no key. Activity code 68.31Z covers agencies and the independent agents
of networks such as iad or SAFTI (registered as sole traders). One lead per active
establishment within the radius: each branch decides on its own photos.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable

import httpx
from sqlalchemy import select

from prospecting.config import settings
from prospecting.db import Lead, session
from prospecting.leads import distance_from_center
from prospecting.text import network_of, pretty_name

API = "https://recherche-entreprises.api.gouv.fr/near_point"
SEARCH_API = "https://recherche-entreprises.api.gouv.fr/search"
NAF_AGENCIES = "68.31Z"


def _person(d: dict) -> str | None:
    if d.get("type_dirigeant") != "personne physique" or not d.get("nom"):
        return None
    first = (d.get("prenoms") or "").split()[:1]
    return pretty_name(" ".join([*first, d["nom"]]))


def _apply(lead: Lead, company: dict, est: dict) -> None:
    sole_trader = (company.get("nature_juridique") or "").startswith("1")
    enseigne = (est.get("liste_enseignes") or [None])[0] or est.get("nom_commercial")
    legal = company.get("nom_raison_sociale") or company.get("nom_complet") or ""
    lead.kind = "independent_agent" if sole_trader else "agency"
    lead.name = pretty_name(enseigne or legal or company["nom_complet"])
    lead.legal_name = pretty_name(company.get("nom_complet") or legal)
    lead.siren = company["siren"]
    lead.siret = est["siret"]
    lead.legal_form = company.get("nature_juridique")
    lead.company_category = company.get("categorie_entreprise")
    lead.headcount = est.get("tranche_effectif_salarie") or company.get("tranche_effectif_salarie")
    lead.branches = company.get("nombre_etablissements_ouverts")
    lead.founded = est.get("date_creation") or company.get("date_creation")
    lead.address = est.get("adresse")
    lead.postcode = est.get("code_postal")
    lead.city = pretty_name(est.get("libelle_commune") or "") or None
    if est.get("latitude") and est.get("longitude"):
        lead.lat, lead.lon = float(est["latitude"]), float(est["longitude"])
    lead.distance_km = distance_from_center(lead.lat, lead.lon)
    lead.network = lead.network or network_of(None, enseigne, legal)
    for d in company.get("dirigeants") or []:
        if p := _person(d):
            lead.add_person(p, d.get("qualite"), "registre")
    if sole_trader and not lead.people:
        # 'Agnes Hess (Ehrhardt)': drop the birth name in brackets.
        person = re.sub(r"\s*\(.*?\)", "", lead.legal_name).strip()
        lead.add_person(person, "Entrepreneur individuel", "registre")
    lead.add_source(
        "registre", f"https://annuaire-entreprises.data.gouv.fr/etablissement/{est['siret']}"
    )


def collect(
    departement: str | None = None,
    radius_km: float | None = None,
    progress: Callable[[str], None] = print,
) -> tuple[int, int]:
    """Active agencies in a circle around the centre (the API allows 50 km at most), or
    in a whole département."""
    if departement:
        url = SEARCH_API
        scope: dict = {"departement": departement, "etat_administratif": "A"}
        label = f"registre {departement}"
    else:
        url = API
        radius = min(50.0, radius_km or settings.radius_km)
        scope = {"lat": settings.center_lat, "long": settings.center_lon, "radius": radius}
        label = f"registre {radius:g} km"
    created = updated = 0
    page, pages = 1, 1
    with httpx.Client(timeout=30, headers={"User-Agent": settings.user_agent}) as client:
        while page <= pages and page * 25 <= 10_000:  # the API stops at 10,000 results
            params = {**scope, "activite_principale": NAF_AGENCIES, "per_page": 25, "page": page}
            for attempt in range(6):
                r = client.get(url, params=params)
                if r.status_code not in (429, 502, 503, 504):
                    break
                time.sleep(2 + attempt * 3)  # the API allows about 7 requests per second
            r.raise_for_status()
            data = r.json()
            pages = data.get("total_pages") or 1
            with session() as s:
                for company in data["results"]:
                    for est in company.get("matching_etablissements") or []:
                        if est.get("etat_administratif") != "A" or est.get("code_pays_etranger"):
                            continue
                        if departement and not (est.get("code_postal") or "").startswith(
                            departement[:2]
                        ):
                            continue
                        lead = s.scalar(select(Lead).where(Lead.siret == est["siret"]))
                        if lead is None:
                            lead = Lead(kind="agency", name="")
                            s.add(lead)
                            created += 1
                        else:
                            updated += 1
                        _apply(lead, company, est)
                s.commit()
            if page % 20 == 0 or page == pages:
                progress(f"{label}: page {page}/{pages}, {created} new, {updated} updated")
            page += 1
            time.sleep(0.2)
    return created, updated
