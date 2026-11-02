"""Write or rewrite an email from a prompt, with the AI you chose in Settings.

Providers: Gemini and Mistral (free tiers, API key), Ollama (free, runs on this machine)
and Claude (paid, Anthropic API key). Each gets the same instructions and returns
{"subject", "body"}. The model only sees facts the app knows about the lead and is told
not to invent any; the opt-out footer is added by the app when sending, never by the AI.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from prospecting import mailer
from prospecting.config import settings
from prospecting.db import Lead, Message

SCHEMA = {
    "type": "object",
    "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
    "required": ["subject", "body"],
    "additionalProperties": False,
}
PLACEHOLDERS = ["prenom", "agence", "ville", "lean", "n", "site", "signature"]
KIND_FR = {
    "photographer": "photographe immobilier",
    "agency": "agence immobilière",
    "independent_agent": "agent immobilier indépendant",
    "other": "professionnel de l'immobilier",
}
STEP_FR = {0: "premier email (prise de contact)", 1: "relance 1", 2: "relance 2",
           -1: "réponse à leur message"}  # fmt: skip
DEFAULT_BRIEF = (
    "Veralume est un service de retouche photo immobilière : redressement des verticales, "
    "correction de la perspective, photos livrées en 24 h. Je suis Yacine, le fondateur, "
    "basé à Strasbourg. Je cherche une dizaine de photographes immobiliers et d'agences "
    "pour un pilote gratuit : ils m'envoient une séance, je leur renvoie les photos corrigées."
)


class WriterError(Exception):
    """Shown to the user as is."""


# --- providers -----------------------------------------------------------------------

PROVIDERS: dict[str, dict[str, Any]] = {
    "gemini": {"label": "Google Gemini", "cost": "free tier", "env": "PROSPECT_GEMINI_API_KEY"},
    "mistral": {"label": "Mistral", "cost": "free tier", "env": "PROSPECT_MISTRAL_API_KEY"},
    "ollama": {"label": "Ollama (on this machine)", "cost": "free, private",
               "env": "PROSPECT_OLLAMA_URL"},
    "claude": {"label": "Claude (Anthropic)", "cost": "paid", "env": "PROSPECT_ANTHROPIC_API_KEY"},
}  # fmt: skip


def _claude_key() -> str | None:
    return settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")


def configured(name: str) -> bool:
    return bool({
        "gemini": settings.gemini_api_key, "mistral": settings.mistral_api_key,
        "ollama": settings.ollama_url, "claude": _claude_key(),
    }.get(name))  # fmt: skip


def model_of(name: str) -> str:
    return {
        "gemini": settings.ai_model_gemini, "mistral": settings.ai_model_mistral,
        "ollama": settings.ai_model_ollama, "claude": settings.ai_model_claude,
    }[name]  # fmt: skip


def current(s: Session) -> str | None:
    """The provider chosen in Settings, or the first one set up (free ones first)."""
    chosen = mailer.get_setting(s, "ai_provider", None)
    if chosen in PROVIDERS and configured(chosen):
        return chosen
    return next((p for p in PROVIDERS if configured(p)), None)


def status(s: Session) -> dict[str, Any]:
    return {
        "providers": [
            {"key": k, **v, "model": model_of(k), "configured": configured(k)}
            for k, v in PROVIDERS.items()
        ],
        "current": current(s),
        "brief": mailer.get_setting(s, "ai_brief", DEFAULT_BRIEF),
    }


def _parse(text: str) -> dict[str, str]:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        try:
            data = json.loads(m.group(0)) if m else None
        except json.JSONDecodeError:
            data = None
        if not isinstance(data, dict):
            raise WriterError("The AI didn't return an email. Try again.") from None
    subject, body = str(data.get("subject", "")).strip(), str(data.get("body", "")).strip()
    if not body:
        raise WriterError("The AI returned an empty email. Try again or reword the prompt.")
    return {"subject": subject, "body": body}


def _raise(r: httpx.Response, who: str) -> None:
    if r.status_code in (401, 403):
        raise WriterError(f"{who} refused the API key. Check it in .env.")
    if r.status_code == 429:
        raise WriterError(
            f"{who}'s free quota is used up for now. Wait a minute, or switch AI in Settings."
        )
    if r.status_code >= 400:
        raise WriterError(f"{who} error {r.status_code}: {r.text[:200]}")


def _gemini(system: str, user: str) -> str:
    r = httpx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.ai_model_gemini}:generateContent",
        headers={"x-goog-api-key": settings.gemini_api_key or ""},
        json={
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "OBJECT",
                    "properties": {"subject": {"type": "STRING"}, "body": {"type": "STRING"}},
                    "required": ["subject", "body"],
                },
                "temperature": 0.7,
            },
        },
        timeout=60,
    )
    _raise(r, "Gemini")
    parts = r.json()["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts)


def _mistral(system: str, user: str) -> str:
    r = httpx.post(
        "https://api.mistral.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {settings.mistral_api_key}"},
        json={
            "model": settings.ai_model_mistral,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
            "temperature": 0.7,
        },
        timeout=60,
    )
    _raise(r, "Mistral")
    return r.json()["choices"][0]["message"]["content"]


def _ollama(system: str, user: str) -> str:
    try:
        r = httpx.post(
            f"{(settings.ollama_url or '').rstrip('/')}/api/chat",
            json={
                "model": settings.ai_model_ollama, "stream": False, "format": SCHEMA,
                "options": {"temperature": 0.7},
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
            },
            timeout=300,
        )  # fmt: skip
    except httpx.ConnectError as e:
        raise WriterError(
            f"Ollama isn't answering at {settings.ollama_url}. Start it with `ollama serve`."
        ) from e
    if r.status_code == 404:
        raise WriterError(
            f"Ollama doesn't have {settings.ai_model_ollama} yet. Run "
            f"`ollama pull {settings.ai_model_ollama}` once."
        )
    _raise(r, "Ollama")
    return r.json()["message"]["content"]


# Models that accept server-side refusal fallbacks ("default" routing).
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}


def _claude(system: str, user: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=_claude_key(), max_retries=2, timeout=90)
    model = settings.ai_model_claude
    extra: dict[str, Any] = {}
    if model in _FALLBACK_MODELS:
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    try:
        response = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": user}],
            # A short email: low effort is plenty and keeps it fast and cheap.
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            **extra,
        )
    except anthropic.AuthenticationError as e:
        raise WriterError("Claude refused the API key. Check PROSPECT_ANTHROPIC_API_KEY.") from e
    except anthropic.RateLimitError as e:
        raise WriterError("Claude is rate-limiting you. Wait a minute and try again.") from e
    except anthropic.APIStatusError as e:
        raise WriterError(f"Claude error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise WriterError("Couldn't reach Claude. Check your connection.") from e
    if response.stop_reason == "refusal":
        raise WriterError("Claude declined to write this one. Reword the prompt.")
    if response.stop_reason == "max_tokens":
        raise WriterError("The email came out too long. Ask for something shorter.")
    return "".join(b.text for b in response.content if b.type == "text")


CALL = {"gemini": _gemini, "mistral": _mistral, "ollama": _ollama, "claude": _claude}


# --- prompts -------------------------------------------------------------------------

SYSTEM = """Tu écris des emails de prospection B2B en français pour l'expéditeur décrit ci-dessous.

Ce que propose l'expéditeur :
{brief}

Règles :
- Français naturel et correct, vouvoiement, ton direct et humain, pas de jargon marketing.
- Court : un premier email tient en 60 à 120 mots, une relance en 30 à 70 mots, sauf consigne contraire.
- Texte brut : pas de markdown, pas de gras, pas de listes à puces sauf si on te le demande, pas d'emoji.
- N'invente rien : aucun chiffre, client, résultat, prix, délai ou fait sur le destinataire qui ne figure pas dans les informations données. Ne parle de l'inclinaison de leurs photos que si une mesure est donnée.
- N'ajoute pas de mention de désinscription ni de pied de page légal : l'application l'ajoute.
- Termine par la signature fournie, telle quelle.
- Quand on te demande de modifier un email, change seulement ce que la consigne demande et garde le reste.
- L'objet est court (moins de 60 caractères), sans majuscules abusives ni point d'exclamation.

Réponds uniquement avec un objet JSON : {{"subject": "...", "body": "..."}}."""

TEMPLATE_RULES = """Tu écris un MODÈLE réutilisable, pas un email pour une personne précise.
Utilise ces champs tels quels, entre accolades, là où ils ont du sens : {prenom} (prénom, peut être vide : écris « Bonjour {prenom}, »), {agence} (nom de l'entreprise), {ville}, {lean} (inclinaison mesurée de leurs photos, par ex. « 2,4° »), {n} (nombre de photos mesurées), {site} (leur site web), {signature} (à la fin, toujours).
N'écris jamais de vrai nom de personne ou d'entreprise."""


def _fmt_lean(v: float | None) -> str:
    return f"{v:.1f}".replace(".", ",") + "°" if v is not None else ""


def lead_facts(lead: Lead) -> str:
    """What the AI may say about this lead, nothing more."""
    first = mailer.first_name(lead)
    lines = [
        f"- Entreprise : {lead.name}",
        f"- Type : {KIND_FR.get(lead.kind, lead.kind)}",
        f"- Prénom du contact : {first or 'inconnu (écris « Bonjour, »)'}",
    ]
    if lead.city:
        lines.append(f"- Ville : {lead.city}")
    if lead.website:
        lines.append(f"- Site web : {lead.website}")
    if lead.lean_median is not None and lead.photos_measured:
        verdict = (
            "verticales visiblement penchées" if lead.lean_median >= 1 else "verticales déjà droites"
        )
        lines.append(
            f"- Mesure sur {lead.photos_measured} photos de leur site : inclinaison médiane "
            f"{_fmt_lean(lead.lean_median)} ({verdict})"
        )
    else:
        lines.append("- Leurs photos n'ont pas été mesurées : ne dis rien sur leur qualité.")
    tags = {"new_business": "entreprise récente", "sole_trader": "travaille seul(e)"}
    small = [t for k, t in tags.items() if k in (lead.tags or [])]
    if small:
        lines.append(f"- {', '.join(small).capitalize()}")
    return "\n".join(lines)


def _history(s: Session, lead: Lead) -> str:
    rows = list(
        s.scalars(
            select(Message)
            .where(Message.lead_id == lead.id,
                   (Message.direction == "in") | (Message.status == "sent"))
            .order_by(Message.id.desc()).limit(3)
        )
    )  # fmt: skip
    if not rows:
        return ""
    out = []
    for m in reversed(rows):
        who = "Eux" if m.direction == "in" else "Moi"
        out.append(f"[{who}] Objet : {m.subject}\n{m.body[:900]}")
    return "Échanges précédents (du plus ancien au plus récent) :\n\n" + "\n\n".join(out)


def build_prompt(
    s: Session, *, instruction: str, subject: str, body: str, mode: str, step: int,
    lead: Lead | None = None, segment: str | None = None,
) -> tuple[str, str]:  # fmt: skip
    system = SYSTEM.format(brief=mailer.get_setting(s, "ai_brief", DEFAULT_BRIEF))
    parts = [f"Type d'email : {STEP_FR.get(step, 'email')}."]
    if lead is not None:
        parts.append("Destinataire (seuls faits connus) :\n" + lead_facts(lead))
        hist = _history(s, lead)
        if hist:
            parts.append(hist)
        sig = mailer.get_setting(s, "signature", mailer.DEFAULT_SIGNATURE)
        parts.append(f"Signature à mettre à la fin :\n{sig}")
    else:
        system += "\n\n" + TEMPLATE_RULES
        if segment and segment != "any":
            parts.append(f"Destinataires : {KIND_FR.get(segment, segment)}s.")
    if mode == "edit" and body.strip():
        parts.append(f"Email actuel :\nObjet : {subject}\n\n{body}")
        parts.append(f"Consigne de modification : {instruction or 'améliore-le sans le rallonger'}")
    else:
        if body.strip():
            parts.append(
                f"Brouillon existant, pour le fond (tu peux tout réécrire) :\nObjet : {subject}\n\n{body}"
            )
        parts.append(f"Consigne : {instruction or 'écris un email court et naturel'}")
    return system, "\n\n".join(parts)


def write(
    s: Session, *, instruction: str, subject: str = "", body: str = "", mode: str = "edit",
    step: int = 0, lead_id: int | None = None, segment: str | None = None,
) -> dict[str, Any]:  # fmt: skip
    provider = current(s)
    if provider is None:
        raise WriterError(
            "No AI is set up yet. Add a free Gemini or Mistral key, an Ollama address or an "
            "Anthropic key to .env (see Settings), then restart the app."
        )
    lead = s.get(Lead, lead_id) if lead_id else None
    if lead_id and lead is None:
        raise WriterError("No lead with this id.")
    system, user = build_prompt(s, instruction=instruction.strip(), subject=subject, body=body,
                                mode=mode, step=step, lead=lead, segment=segment)  # fmt: skip
    label = PROVIDERS[provider]["label"]
    t0 = time.monotonic()
    try:
        text = CALL[provider](system, user)
    except httpx.TimeoutException as e:
        raise WriterError(f"{label} took too long. Try again.") from e
    except httpx.HTTPError as e:
        raise WriterError(f"Couldn't reach {label}: {e}") from e
    except (KeyError, IndexError, ValueError) as e:
        raise WriterError(f"{label} sent back something unexpected. Try again.") from e
    out = _parse(text)
    if lead is None:  # a template keeps the known placeholders only
        out["body"] = re.sub(
            r"\{(\w+)\}",
            lambda m: m.group(0) if m.group(1) in PLACEHOLDERS else m.group(1),
            out["body"],
        )
    return {**out, "provider": provider, "model": model_of(provider),
            "seconds": round(time.monotonic() - t0, 1)}  # fmt: skip
