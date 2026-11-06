"""Text helpers: names, French phone numbers, emails, domains, distances."""

from __future__ import annotations

import math
import re
import unicodedata
from urllib.parse import urlsplit

# Words that say nothing about which agency it is.
_STOP = {
    "sarl", "sas", "sasu", "eurl", "sa", "sci", "snc", "selarl", "ei", "en", "abrege",
    "immobilier", "immobiliere", "immobilieres", "immobiliers", "immo", "agence",
    "cabinet", "groupe", "transaction", "transactions", "conseil", "conseils",
    "l", "la", "le", "les", "de", "du", "des", "et", "d", "a", "au", "aux", "the",
}  # fmt: skip

# Franchise and agent networks: the website is then the network's, and photos and
# marketing are partly decided centrally.
NETWORKS = {
    "iadfrance.fr": "iad",
    "safti.fr": "SAFTI",
    "capifrance.fr": "Capifrance",
    "optimhome.com": "Optimhome",
    "proprietes-privees.com": "Propriétés Privées",
    "bskimmobilier.com": "BSK",
    "efficity.com": "efficity",
    "megagence.com": "Megagence",
    "lafourmi-immo.com": "La Fourmi",
    "foncia.com": "Foncia",
    "century21.fr": "Century 21",
    "orpi.com": "Orpi",
    "laforet.com": "Laforêt",
    "guy-hoquet.com": "Guy Hoquet",
    "stephaneplazaimmobilier.com": "Stéphane Plaza",
    "squarehabitat.fr": "Square Habitat",
    "era.fr": "ERA",
    "nestenn.com": "Nestenn",
    "citya.com": "Citya",
    "immobilier.lcl.fr": "LCL",
    "arthurimmo.com": "Arthurimmo",
    "l-adresse.fr": "L'Adresse",
    "coldwellbanker.fr": "Coldwell Banker",
    "barnes-international.com": "Barnes",
    "sothebysrealty-france.com": "Sotheby's",
    "engelvoelkers.com": "Engel & Völkers",
    "immobilier.notaires.fr": "Notaires",
}
NETWORK_NAMES = {v.lower(): v for v in NETWORKS.values()} | {
    "square habitat": "Square Habitat",
    "century 21": "Century 21",
    "guy hoquet": "Guy Hoquet",
    "stephane plaza": "Stéphane Plaza",
}

# Never a lead's own website: portals, directories, social networks, registries.
NOT_OWN_SITE = {
    "seloger.com", "leboncoin.fr", "bienici.com", "logic-immo.com", "pap.fr",
    "meilleursagents.com", "figaroimmo.fr", "lefigaro.fr", "ouestfrance-immo.com",
    "pagesjaunes.fr", "societe.com", "pappers.fr", "infogreffe.fr", "verif.com",
    "annuaire-entreprises.data.gouv.fr", "manageo.fr", "corporama.com", "kompass.com",
    "facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com",
    "youtube.com", "tiktok.com", "pinterest.com", "google.com", "goo.gl", "yelp.fr",
    "malt.fr", "malt.com", "photopresta.fr", "missionphotographe.com", "synaptiik.fr",
    "trouver-mon-photographe.fr", "starofservice.com", "houzz.fr", "wikipedia.org",
    "tripadvisor.fr", "mappy.com", "justacote.com", "118712.fr", "hoodspot.fr",
    "cylex-france.fr", "infobel.com", "123pages.fr", "lesbonsprofessionnels.com",
    "opinionsystem.fr", "immodvisor.com", "avis-verifies.com", "wix.com",
    # Abroad: portals and directories.
    "immoweb.be", "zimmo.be", "immovlan.be", "goudengids.be", "pagesdor.be",
    "athome.lu", "immotop.lu", "editus.lu", "homegate.ch", "immoscout24.ch", "comparis.ch",
    "local.ch", "search.ch", "immobilienscout24.de", "immowelt.de", "gelbeseiten.de",
    "dasoertliche.de", "11880.com", "golocal.de", "werkenntdenbesten.de", "fotografen.de",
    "centris.ca", "duproprio.com", "realtor.ca", "pagesjaunes.ca", "yellowpages.ca",
    "kijiji.ca", "yelp.com", "yelp.ca", "yelp.de", "yelp.be", "yelp.ch", "tripadvisor.com",
    "fiverr.com", "upwork.com", "behance.net", "bark.com", "houzz.com", "houzz.de",
    "wikipedia.de", "trustlocal.be", "provenexpert.com", "jameda.de", "booking.com",
}  # fmt: skip

SOCIAL = {"facebook.com": "facebook", "instagram.com": "instagram", "linkedin.com": "linkedin"}


def ascii_lower(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def name_tokens(name: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", ascii_lower(name)) if t and t not in _STOP]


def name_similarity(a: str, b: str) -> float:
    """Jaccard similarity of meaningful name tokens."""
    ta, tb = set(name_tokens(a)), set(name_tokens(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def pretty_name(raw: str) -> str:
    """'AGENCE IMMOBILIERE DE LA ROBERTSAU' -> 'Agence Immobiliere de la Robertsau'."""
    if raw != raw.upper():
        return raw.strip()
    small = {"de", "du", "des", "la", "le", "les", "et", "en", "d", "l", "a", "au", "aux"}
    words = []
    for i, w in enumerate(raw.lower().split()):
        if i and w in small:
            words.append(w)
        else:
            words.append("-".join(p[:1].upper() + p[1:] for p in w.split("-")))
    return " ".join(words)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def host(url: str) -> str:
    h = urlsplit(url if "//" in url else "//" + url).hostname or ""
    return h.lower().removeprefix("www.")


def domain_in(url: str, domains: set[str] | dict[str, str]) -> str | None:
    """The entry of `domains` that `url`'s host is, or is a subdomain of."""
    h = host(url)
    for d in domains:
        if h == d or h.endswith("." + d):
            return d
    return None


def network_of(url: str | None, *names: str | None) -> str | None:
    if url and (d := domain_in(url, NETWORKS)):
        return NETWORKS[d]
    for n in names:
        if not n:
            continue
        low = ascii_lower(n)
        for key, label in NETWORK_NAMES.items():
            if re.search(rf"\b{re.escape(ascii_lower(key))}\b", low):
                return label
    return None


_PHONE_RE = re.compile(
    r"(?<![\d+])(?:(?:\+|00)33[\s.\-]?(?:\(0\)[\s.\-]?)?|0)([1-9](?:[\s.\-]?\d{2}){4})(?!\d)"
)


def find_phones(text: str, country: str | None = "FR") -> list[str]:
    """French numbers as '03 88 12 34 56'; elsewhere the international form
    ('+32 471 12 34 56'), in order of appearance, deduplicated."""
    if country and country != "FR":
        return _foreign_phones(text, country)
    out: list[str] = []
    for m in _PHONE_RE.finditer(text):
        digits = "0" + re.sub(r"\D", "", m.group(1))
        if len(digits) != 10:
            continue
        pretty = " ".join(digits[i : i + 2] for i in range(0, 10, 2))
        if pretty not in out:
            out.append(pretty)
    return out


def _foreign_phones(text: str, country: str) -> list[str]:
    import phonenumbers as pn

    out: list[str] = []
    try:
        matches = list(pn.PhoneNumberMatcher(text[:200_000], country, leniency=pn.Leniency.VALID))
    except Exception:  # odd text must never stop a site's reading
        return out
    for m in matches:
        pretty = pn.format_number(m.number, pn.PhoneNumberFormat.INTERNATIONAL)
        if pretty not in out:
            out.append(pretty)
    return out


_EMAIL_RE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._%+\-]*@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,12}"
)
_EMAIL_JUNK = re.compile(
    r"(\.(png|jpe?g|gif|webp|svg|css|js)$)|example\.|sentry|wixpress|domain\.(com|fr)|"
    r"votre|your|email@|nom@|prenom|xxx|@2x|u00|noreply|no-reply|test@|"
    r"^dpo@|^rgpd@|donneespersonnelles|^privacy@",
    re.IGNORECASE,
)


def find_emails(text: str) -> list[str]:
    out: list[str] = []
    for m in _EMAIL_RE.finditer(text):
        e = m.group(0).strip(".").lower()
        if _EMAIL_JUNK.search(e) or e in out:
            continue
        out.append(e)
    return out


FREE_MAIL = {
    "gmail.com", "orange.fr", "wanadoo.fr", "free.fr", "hotmail.com", "hotmail.fr",
    "outlook.com", "outlook.fr", "live.fr", "yahoo.fr", "yahoo.com", "laposte.net",
    "sfr.fr", "neuf.fr", "icloud.com", "aol.com", "bbox.fr", "numericable.fr",
    "gmx.de", "gmx.net", "gmx.ch", "web.de", "t-online.de", "outlook.de", "hotmail.de",
    "yahoo.de", "googlemail.com", "freenet.de", "bluewin.ch", "hispeed.ch", "sunrise.ch",
    "skynet.be", "telenet.be", "proximus.be", "hotmail.be", "live.be", "pt.lu",
    "videotron.ca", "hotmail.ca", "yahoo.ca", "sympatico.ca", "live.ca", "me.com", "proton.me",
    "protonmail.com",
}  # fmt: skip


def email_belongs(email: str, site: str) -> bool:
    """The business's own address: on its site's domain, or a free mailbox.

    Drops the website vendor's or mediator's address printed in the footer.
    """
    domain = email.rsplit("@", 1)[-1]
    site_host = host(site)
    site_root = ".".join(site_host.split(".")[-2:])
    return domain in FREE_MAIL or domain == site_host or domain.endswith(site_root)


def email_is_personal(email: str) -> bool:
    """firstname.lastname@ rather than contact@ / agence@."""
    local = email.split("@", 1)[0]
    generic = {"contact", "info", "infos", "agence", "accueil", "bonjour", "hello", "admin",
               "gestion", "location", "transaction", "vente", "ventes", "syndic", "office",
               "direction", "secretariat", "commercial", "immobilier", "immo"}  # fmt: skip
    return local not in generic and not any(local.startswith(g) for g in generic)
